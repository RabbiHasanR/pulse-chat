import os
import shutil
import tempfile
import logging
import magic
import ffmpeg # pip install ffmpeg-python
from utils.aws import s3, AWS_BUCKET
from .ffmpeg_progress import FFmpegProgressTracker

logger = logging.getLogger(__name__)

# --- Configuration ---
SEGMENT_DURATION = 10 
RESOLUTIONS = [
    {"name": "1080p", "w": 1920, "h": 1080, "bitrate": "4500k", "maxrate": "4800k", "bufsize": "6000k"},
    {"name": "720p",  "w": 1280, "h": 720,  "bitrate": "2500k", "maxrate": "2800k", "bufsize": "3500k"},
    {"name": "480p",  "w": 854,  "h": 480,  "bitrate": "1200k",  "maxrate": "1400k",  "bufsize": "2000k"},
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
            ExpiresIn=3600
        )

    def process(self, on_progress_callback=None, on_checkpoint_save=None, on_playable_callback=None):
        """
        Main execution pipeline with Resume-on-Retry logic.
        """
        try:
            input_url = self.get_input_url()
            logger.info(f"Starting Video Processing for Asset: {self.asset.id}")

            # 1. Validation (Security & Integrity)
            self._validate_remote_source(input_url)

            # 2. Metadata Probe
            metadata = self._get_metadata(input_url)
            
            # 3. Thumbnail Generation (Resume Check)
            # We check if 'thumbnail' key already exists in the asset variants
            current_vars = self.asset.variants or {}
            thumb_key = current_vars.get('thumbnail')
            
            if not thumb_key:
                logger.info("Generating Thumbnail...")
                thumb_key = self._process_thumbnail(input_url, metadata['duration'])
                if on_progress_callback:
                    on_progress_callback(5.0, thumb_key=thumb_key)
            else:
                logger.info(f"⏩ Skipping Thumbnail (Found: {thumb_key})")
                if on_progress_callback:
                    on_progress_callback(5.0, thumb_key=thumb_key)

            # 4. HLS Processing (Resume Check inside)
            master_key = self._process_hls(
                input_url, 
                metadata, 
                on_progress_callback, 
                on_checkpoint_save, 
                on_playable_callback
            )

            # 5. Cleanup Original File (Only if successful)
            self._delete_original()

            return master_key, thumb_key

        except Exception as e:
            logger.error(f"Video Processing Failed: {e}")
            raise e
        finally:
            # Always clean up local temp files
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    def _validate_remote_source(self, presigned_url):
        """Validates Magic Bytes and Stream Integrity."""
        try:
            # Check first 2KB for magic bytes
            response = s3.get_object(Bucket=self.bucket, Key=self.original_key, Range='bytes=0-2048')
            head_bytes = response['Body'].read()
            mime_type = magic.from_buffer(head_bytes, mime=True)
            
            forbidden = ['application/x-dosexec', 'application/x-executable', 'text/x-python', 'text/html']
            if mime_type in forbidden:
                raise ValueError(f"Security Alert: Forbidden file type {mime_type}")

            # Check via FFmpeg probe
            probe = ffmpeg.probe(presigned_url)
            if not any(s['codec_type'] == 'video' for s in probe['streams']):
                raise ValueError("File contains no valid video stream")
                
        except Exception as e:
            raise ValueError(f"Security/Validation Failed: {str(e)}")

    def _get_metadata(self, input_url):
        try:
            probe = ffmpeg.probe(input_url)
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            return {
                "width": int(video_stream['width']),
                "height": int(video_stream['height']),
                "duration": float(video_stream.get('duration', 0))
            }
        except Exception as e:
            raise ValueError(f"Metadata Probe Failed: {e}")

    def _process_thumbnail(self, input_url, duration):
        output_path = os.path.join(self.temp_dir, "thumbnail.webp")
        timestamp = 1 if duration > 1 else 0
        
        (
            ffmpeg
            .input(input_url, ss=timestamp)
            .filter('scale', 320, -1)
            .output(output_path, vframes=1, vcodec='libwebp', **{'qscale': 75})
            .run(quiet=True, overwrite_output=True)
        )
        
        thumb_key = f"processed/{self.asset.id}/thumbnail.webp"
        with open(output_path, "rb") as f:
            s3.upload_fileobj(f, self.bucket, thumb_key, ExtraArgs={"ContentType": "image/webp", "CacheControl": "max-age=31536000"})
        
        return thumb_key

    def _process_hls(self, input_url, metadata, callback, checkpoint_callback, playable_callback):
        input_min_dim = min(metadata['width'], metadata['height'])
        base_s3_prefix = f"processed/{self.asset.id}/hls"
        master_key = f"{base_s3_prefix}/master.m3u8"
        
        # Load completed parts from Asset variants (passed from Task via Redis checkpoint)
        completed_parts = self.asset.variants.get('hls_parts', {})

        # Filter resolutions smaller than input
        valid_resolutions = [r for r in RESOLUTIONS if input_min_dim >= (min(r["w"], r["h"]) * 0.9)]
        if not valid_resolutions: valid_resolutions = [RESOLUTIONS[-1]]
        
        # Sort by height (ascending) so lower quality is processed/listed first
        valid_resolutions.sort(key=lambda x: x['h'])

        total_variants = len(valid_resolutions)
        base_progress = 5.0
        progress_per_variant = 95.0 / total_variants
        
        # Master Playlist Accumulator
        master_playlist_lines = ["#EXTM3U", "#EXT-X-VERSION:3"]

        for i, res in enumerate(valid_resolutions):
            variant_name = res['name']
            bandwidth = int(res['bitrate'].replace('k', '000'))
            
            # --- RESUME CHECK ---
            if completed_parts.get(variant_name):
                logger.info(f"⏩ Resuming: Skipping {variant_name}")
                
                # Even if skipped, we MUST add it to the master playlist
                master_playlist_lines.append(
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                    f"{variant_name}/index.m3u8"
                )
                
                # Re-upload Master Playlist (Ensures consistency on retry)
                self._update_master_playlist(master_playlist_lines, master_key)

                # Report "Fake" Progress to UI
                if callback:
                    fake_pct = base_progress + ((i + 1) * progress_per_variant)
                    callback(fake_pct)
                
                # If first variant is done, it's playable
                if i == 0 and playable_callback:
                    playable_callback(master_key)
                
                continue 
            # --------------------

            # Normal Processing
            logger.info(f"Transcoding variant: {variant_name}")
            variant_dir = os.path.join(self.temp_dir, variant_name)
            os.makedirs(variant_dir, exist_ok=True)
            playlist_file = os.path.join(variant_dir, "index.m3u8")
            segment_pattern = os.path.join(variant_dir, "seg_%03d.ts")

            current_variant_start_pct = base_progress + (i * progress_per_variant)
            
            def handle_ffmpeg_update(ffmpeg_pct):
                global_pct = current_variant_start_pct + ((ffmpeg_pct / 100) * progress_per_variant)
                if callback: callback(global_pct)

            tracker = FFmpegProgressTracker(metadata['duration'], handle_ffmpeg_update)
            tracker.start()
            progress_url = tracker.get_ffmpeg_arg()

            try:
                (
                    ffmpeg
                    .input(input_url)
                    .output(
                        playlist_file,
                        vf=f"scale=-2:{res['h']}",
                        c="libx264", b=res['bitrate'], maxrate=res['maxrate'], bufsize=res['bufsize'],
                        format="hls", hls_time=SEGMENT_DURATION, hls_list_size=0,
                        hls_segment_filename=segment_pattern, hls_flags="delete_segments",
                        g=SEGMENT_DURATION * 30, preset="veryfast",
                        progress=progress_url
                    )
                    .global_args('-nostats') 
                    .run(quiet=True, overwrite_output=True)
                )
            finally:
                tracker.stop()

            # Upload Segments
            self._upload_directory(variant_dir, f"{base_s3_prefix}/{variant_name}")
            shutil.rmtree(variant_dir)
            
            # Save Checkpoint (Calls back to Task -> Redis)
            if checkpoint_callback:
                checkpoint_callback(variant_name)

            # Update Master Playlist
            master_playlist_lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                f"{variant_name}/index.m3u8"
            )
            is_last = (i == total_variants - 1)
            self._update_master_playlist(master_playlist_lines, master_key, "max-age=31536000" if is_last else "no-cache")
            
            if i == 0 and playable_callback:
                playable_callback(master_key)

        return master_key

    def _update_master_playlist(self, lines, s3_key, cache_control="no-cache"):
        content = "\n".join(lines)
        master_path = os.path.join(self.temp_dir, "master.m3u8")
        with open(master_path, "w") as f:
            f.write(content)
        with open(master_path, "rb") as f:
            s3.upload_fileobj(f, self.bucket, s3_key, ExtraArgs={"ContentType": "application/x-mpegURL", "CacheControl": cache_control})

    def _upload_directory(self, local_dir, s3_prefix):
        for root, _, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                s3_key = f"{s3_prefix}/{file}"
                ctype = "video/MP2T" if file.endswith(".ts") else "application/x-mpegURL"
                cache = "max-age=31536000" if file.endswith(".ts") else "no-cache"
                
                with open(local_path, "rb") as f:
                    s3.upload_fileobj(f, self.bucket, s3_key, ExtraArgs={"ContentType": ctype, "CacheControl": cache})

    def _delete_original(self):
        try:
            logger.info(f"Deleting raw file: {self.original_key}")
            s3.delete_object(Bucket=self.bucket, Key=self.original_key)
        except Exception as e:
            logger.warning(f"Failed to delete raw file: {e}")