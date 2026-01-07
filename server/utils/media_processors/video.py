import os
import ffmpeg
import logging
import shutil
import tempfile
from utils.aws import s3, AWS_BUCKET

logger = logging.getLogger(__name__)

# HLS Segment Duration (Seconds)
SEGMENT_DURATION = 10 

# Adaptive Bitrate Settings
# 'w' and 'h' are targets. We use the smallest dimension to determine if we should generate it.
RESOLUTIONS = [
    {"name": "1080p", "w": 1920, "h": 1080, "bitrate": "4500k", "maxrate": "4800k", "bufsize": "6000k"},
    {"name": "720p",  "w": 1280, "h": 720,  "bitrate": "2500k", "maxrate": "2800k", "bufsize": "3500k"},
    {"name": "480p",  "w": 854,  "h": 480,  "bitrate": "1200k", "maxrate": "1400k", "bufsize": "2000k"},
    {"name": "360p",  "w": 640,  "h": 360,  "bitrate": "800k",  "maxrate": "900k",  "bufsize": "1200k"},
    {"name": "240p",  "w": 426,  "h": 240,  "bitrate": "400k",  "maxrate": "450k",  "bufsize": "600k"},
]

class VideoProcessor:
    def __init__(self, asset):
        self.asset = asset
        self.bucket = asset.bucket
        self.original_key = asset.object_key
        # Create a unique temp directory for this specific job
        self.temp_dir = tempfile.mkdtemp()

    def get_input_url(self):
        """Generate a temporary signed URL so FFmpeg can stream directly from S3"""
        return s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': self.bucket, 'Key': self.original_key},
            ExpiresIn=3600 # 1 hour validity
        )

    def process(self):
        """
        Main execution pipeline.
        Returns: (master_playlist_key, thumbnail_key)
        """
        try:
            input_url = self.get_input_url()
            logger.info(f"Starting Video Processing for Asset: {self.asset.id}")

            # 1. Probe Input (Over Network) to get resolution & duration
            metadata = self._get_metadata(input_url)
            
            # 2. Generate Thumbnail (At 1 second mark)
            thumb_key = self._process_thumbnail(input_url, metadata['duration'])

            # 3. Generate HLS Adaptive Stream
            hls_master_key = self._process_hls(input_url, metadata)

            # 4. Delete Original Raw File (Save S3 Storage)
            self._delete_original()

            return hls_master_key, thumb_key

        except Exception as e:
            logger.error(f"Video Processing Failed: {e}")
            raise e
        finally:
            # Cleanup: Remove temp directory and all chunks from local disk
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    def _get_metadata(self, input_url):
        """Probe video metadata without downloading the file"""
        try:
            probe = ffmpeg.probe(input_url)
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                raise ValueError("No video stream found in file")
            
            return {
                "width": int(video_stream['width']),
                "height": int(video_stream['height']),
                "duration": float(video_stream.get('duration', 0))
            }
        except ffmpeg.Error as e:
            error_log = e.stderr.decode('utf8') if e.stderr else str(e)
            logger.error(f"FFProbe failed: {error_log}")
            raise

    def _process_thumbnail(self, input_url, duration):
        """Extract a single frame and upload it"""
        output_path = os.path.join(self.temp_dir, "thumbnail.jpg")
        
        # Capture frame at 1s, or 0s if video is extremely short
        timestamp = 1 if duration > 1 else 0
        
        try:
            (
                ffmpeg
                .input(input_url, ss=timestamp)
                .filter('scale', 320, -1) # Width 320px, auto height
                .output(output_path, vframes=1)
                .run(quiet=True, overwrite_output=True)
            )
            
            # Upload
            thumb_key = f"processed/{self.asset.id}/thumbnail.jpg"
            with open(output_path, "rb") as f:
                s3.upload_fileobj(f, self.bucket, thumb_key, ExtraArgs={"ContentType": "image/jpeg"})
            
            return thumb_key
        except ffmpeg.Error as e:
            logger.error(f"Thumbnail generation failed: {e.stderr.decode('utf8')}")
            # Non-critical: return None or raise depending on your policy. We raise to be safe.
            raise

    def _process_hls(self, input_url, metadata):
        """
        Generates HLS Master Playlist and Variant streams.
        Skip upscaling based on input dimensions.
        """
        # Determine the smallest dimension (Orientation Agnostic)
        # e.g., 1920x1080 (Landscape) -> min is 1080
        # e.g., 1080x1920 (Portrait)  -> min is 1080
        input_min_dim = min(metadata['width'], metadata['height'])

        base_s3_prefix = f"processed/{self.asset.id}/hls"
        master_playlist_content = "#EXTM3U\n"
        variants_generated = False

        for res in RESOLUTIONS:
            target_min_dim = min(res["w"], res["h"])

            # LOGIC: If input resolution < 90% of target, SKIP IT.
            # (e.g. Input 480p vs Target 720p -> Skip)
            if input_min_dim < (target_min_dim * 0.9):
                continue

            variants_generated = True
            variant_name = res['name']
            logger.info(f"Transcoding variant: {variant_name}")

            # Create subfolder: /tmp/.../720p/
            variant_dir = os.path.join(self.temp_dir, variant_name)
            os.makedirs(variant_dir, exist_ok=True)

            playlist_file = os.path.join(variant_dir, "index.m3u8")
            segment_pattern = os.path.join(variant_dir, "seg_%03d.ts")

            try:
                # Transcode logic
                (
                    ffmpeg
                    .input(input_url)
                    .output(
                        playlist_file,
                        # Scale filter:
                        # -2 ensures the calculated dimension is divisible by 2 (required by H.264)
                        # We force the 'height' to the target, and let width scale automatically
                        vf=f"scale=-2:{res['h']}", 
                        c="libx264",
                        b=res['bitrate'],
                        maxrate=res['maxrate'],
                        bufsize=res['bufsize'],
                        # HLS Flags
                        format="hls",
                        hls_time=SEGMENT_DURATION,
                        hls_list_size=0, # Include all segments
                        hls_segment_filename=segment_pattern,
                        hls_flags="delete_segments", # Don't delete local, we need to upload them first
                        g=SEGMENT_DURATION * 30, # Keyframe interval (~30fps)
                        preset="veryfast" # Speed vs Compression balance
                    )
                    .run(quiet=True, overwrite_output=True)
                )

                # Upload all .ts and .m3u8 files for this variant
                self._upload_directory(variant_dir, f"{base_s3_prefix}/{variant_name}")

                # Append to Master Playlist
                # Convert bitrate string "1500k" -> integer 1500000
                bandwidth = int(res['bitrate'].replace('k', '000'))
                
                # Note: We hardcode 16:9 res in playlist info, but the actual video preserves aspect ratio.
                # This is standard practice for HLS manifests.
                master_playlist_content += (
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                    f"{variant_name}/index.m3u8\n"
                )

            except ffmpeg.Error as e:
                logger.error(f"Failed to process variant {variant_name}: {e.stderr.decode('utf8')}")
                raise

        if not variants_generated:
            # Fallback: If video is smaller than our lowest setting (unlikely with 240p),
            # force process the lowest resolution (240p) just so we have something.
            # (Logic omitted for brevity, but you can duplicate the loop for the last item in RESOLUTIONS)
            raise ValueError("Input video resolution is too low to process")

        # Write Master Playlist to disk
        master_path = os.path.join(self.temp_dir, "master.m3u8")
        with open(master_path, "w") as f:
            f.write(master_playlist_content)
        
        # Upload Master Playlist
        master_key = f"{base_s3_prefix}/master.m3u8"
        with open(master_path, "rb") as f:
            s3.upload_fileobj(f, self.bucket, master_key, ExtraArgs={
                "ContentType": "application/x-mpegURL",
                "CacheControl": "no-cache" # Master playlist should not be cached tightly
            })

        return master_key

    def _upload_directory(self, local_dir, s3_prefix):
        """Recursively uploads chunks and playlists"""
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                s3_key = f"{s3_prefix}/{file}"
                
                # MIME Types
                if file.endswith(".ts"):
                    ctype = "video/MP2T"
                    cache = "max-age=31536000" # Segments never change, cache forever
                else:
                    ctype = "application/x-mpegURL"
                    cache = "no-cache" # Playlists might update (live), or mostly static (VOD)
                
                with open(local_path, "rb") as f:
                    s3.upload_fileobj(f, self.bucket, s3_key, ExtraArgs={
                        "ContentType": ctype,
                        "CacheControl": cache
                    })

    def _delete_original(self):
        try:
            logger.info(f"Deleting raw file: {self.original_key}")
            s3.delete_object(Bucket=self.bucket, Key=self.original_key)
        except Exception as e:
            logger.warning(f"Failed to delete raw file (non-critical): {e}")