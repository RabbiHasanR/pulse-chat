import os
import ffmpeg
import logging
import shutil
import tempfile
from utils.aws import s3, AWS_BUCKET
from .ffmpeg_progress import FFmpegProgressTracker

logger = logging.getLogger(__name__)

# HLS Segment Duration (Seconds)
SEGMENT_DURATION = 10 

# Adaptive Bitrate Settings
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

    def process(self, on_progress_callback=None, on_checkpoint_save=None, on_playable_callback=None):
        """
        Main execution pipeline.
        :param on_progress_callback: function(percent, thumb_key=None)
        :param on_checkpoint_save: function(variant_name) - Saves state to DB
        :param on_playable_callback: function(master_key) - Notifies when video is playable (even if low quality)
        """
        try:
            input_url = self.get_input_url()
            logger.info(f"Starting Video Processing for Asset: {self.asset.id}")

            # 1. Probe Input (Over Network)
            metadata = self._get_metadata(input_url)
            total_duration = metadata['duration']
            
            # 2. Generate Thumbnail (0-5% Progress)
            current_vars = self.asset.variants or {}
            thumb_key = current_vars.get('thumbnail')
            
            if not thumb_key:
                thumb_key = self._process_thumbnail(input_url, total_duration)
                if on_progress_callback:
                    on_progress_callback(5.0, thumb_key=thumb_key)
            else:
                logger.info("Thumbnail already exists, skipping generation.")

            # 3. Generate HLS Adaptive Stream (5-100% Progress)
            # Pass the new callback down to the HLS processor
            hls_master_key = self._process_hls(
                input_url, 
                metadata, 
                on_progress_callback, 
                on_checkpoint_save, 
                on_playable_callback
            )

            # 4. Delete Original Raw File
            self._delete_original()

            return hls_master_key, thumb_key

        except Exception as e:
            logger.error(f"Video Processing Failed: {e}")
            raise e
        finally:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    def _get_metadata(self, input_url):
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
        output_path = os.path.join(self.temp_dir, "thumbnail.webp")
        timestamp = 1 if duration > 1 else 0
        
        (
            ffmpeg
            .input(input_url, ss=timestamp)
            .filter('scale', 320, -1)
            .output(
                output_path, 
                vframes=1, 
                vcodec='libwebp',      
                **{'qscale': 75} 
            )
            .run(quiet=True, overwrite_output=True)
        )
        
        thumb_key = f"processed/{self.asset.id}/thumbnail.webp"
        
        with open(output_path, "rb") as f:
            s3.upload_fileobj(f, self.bucket, thumb_key, ExtraArgs={
                "ContentType": "image/webp", 
                "CacheControl": "max-age=31536000"
            })
        
        return thumb_key

    def _process_hls(self, input_url, metadata, callback, checkpoint_callback, playable_callback):
        input_min_dim = min(metadata['width'], metadata['height'])
        base_s3_prefix = f"processed/{self.asset.id}/hls"
        master_key = f"{base_s3_prefix}/master.m3u8"
        
        # Load completed parts from DB (for Resume)
        completed_parts = self.asset.variants.get('hls_parts', {})

        # Filter valid resolutions
        valid_resolutions = [
            r for r in RESOLUTIONS 
            if input_min_dim >= (min(r["w"], r["h"]) * 0.9)
        ]
        if not valid_resolutions: valid_resolutions = [RESOLUTIONS[-1]]

        # --- OPTIMIZATION: SMALLEST FIRST ---
        # Sort by height ascending (240p -> 360p -> ... -> 1080p)
        valid_resolutions.sort(key=lambda x: x['h'])
        # ------------------------------------

        total_variants = len(valid_resolutions)
        base_progress = 5.0
        progress_per_variant = 95.0 / total_variants
        
        # We store the lines of the master playlist as we build it
        master_playlist_lines = ["#EXTM3U"]

        for i, res in enumerate(valid_resolutions):
            variant_name = res['name']
            bandwidth = int(res['bitrate'].replace('k', '000'))
            
            # --- CHECKPOINT: RESUME LOGIC ---
            if completed_parts.get(variant_name):
                logger.info(f"Skipping {variant_name} (Found in checkpoint)")
                
                # Add to master playlist logic
                master_playlist_lines.append(
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                    f"{variant_name}/index.m3u8"
                )
                
                # Fake update UI
                if callback:
                    fake_pct = base_progress + ((i + 1) * progress_per_variant)
                    callback(fake_pct)
                    
                # If this was the first one (even if skipped/resumed), ensure master exists
                if i == 0:
                    self._update_master_playlist(master_playlist_lines, master_key)
                    if playable_callback: playable_callback(master_key)
                
                continue
            # --------------------------------

            logger.info(f"Transcoding variant: {variant_name}")
            variant_dir = os.path.join(self.temp_dir, variant_name)
            os.makedirs(variant_dir, exist_ok=True)
            playlist_file = os.path.join(variant_dir, "index.m3u8")
            segment_pattern = os.path.join(variant_dir, "seg_%03d.ts")

            # Progress Tracking Setup
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
                        c="libx264",
                        b=res['bitrate'],
                        maxrate=res['maxrate'],
                        bufsize=res['bufsize'],
                        format="hls",
                        hls_time=SEGMENT_DURATION,
                        hls_list_size=0,
                        hls_segment_filename=segment_pattern,
                        hls_flags="delete_segments",
                        g=SEGMENT_DURATION * 30,
                        preset="veryfast",
                        progress=progress_url
                    )
                    .global_args('-nostats') 
                    .run(quiet=True, overwrite_output=True)
                )
            finally:
                tracker.stop()

            # Upload files
            self._upload_directory(variant_dir, f"{base_s3_prefix}/{variant_name}")
            
            # Save checkpoint
            if checkpoint_callback:
                checkpoint_callback(variant_name)

            # --- INCREMENTAL MASTER PLAYLIST UPDATE ---
            # 1. Add this new variant to our list
            master_playlist_lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                f"{variant_name}/index.m3u8"
            )
            
            # 2. Upload the new Master Playlist immediately
            # This makes the video playable with the resolutions processed SO FAR
            self._update_master_playlist(master_playlist_lines, master_key)
            
            # 3. Notify "Playable" (Only on the first successful variant)
            if i == 0 and playable_callback:
                playable_callback(master_key)
            # ------------------------------------------

        return master_key

    def _update_master_playlist(self, lines, s3_key):
        """Helper to write and upload the master playlist"""
        content = "\n".join(lines)
        master_path = os.path.join(self.temp_dir, "master.m3u8")
        
        with open(master_path, "w") as f:
            f.write(content)
            
        with open(master_path, "rb") as f:
            s3.upload_fileobj(f, self.bucket, s3_key, ExtraArgs={
                "ContentType": "application/x-mpegURL",
                "CacheControl": "no-cache" # Important! Tells player to re-check for new qualities
            })

    def _upload_directory(self, local_dir, s3_prefix):
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                s3_key = f"{s3_prefix}/{file}"
                
                if file.endswith(".ts"):
                    ctype = "video/MP2T"
                    cache = "max-age=31536000"
                else:
                    ctype = "application/x-mpegURL"
                    cache = "no-cache"
                
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
            logger.warning(f"Failed to delete raw file: {e}")