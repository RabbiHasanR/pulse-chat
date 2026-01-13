import os
import ffmpeg
import logging
import shutil
import tempfile
from utils.aws import s3, AWS_BUCKET
from .ffmpeg_progress import FFmpegProgressTracker

logger = logging.getLogger(__name__)

# HLS Segment Duration (Seconds)
# 10s is the sweet spot for VOD (balances network requests vs seek speed)
SEGMENT_DURATION = 10 

# Adaptive Bitrate Settings
# Ordered high-to-low, but we will sort them inside the function to be safe.
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
        # We use mkdtemp to ensure thread safety and isolation
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
        :param on_progress_callback: function(percent, thumb_key=None) - Updates Redis/UI
        :param on_checkpoint_save: function(variant_name) - Saves state to Postgres
        :param on_playable_callback: function(master_key) - Notifies UI to show "Play" button
        """
        try:
            input_url = self.get_input_url()
            logger.info(f"Starting Video Processing for Asset: {self.asset.id}")

            # 1. Probe Input (Over Network)
            metadata = self._get_metadata(input_url)
            total_duration = metadata['duration']
            
            # 2. Generate Thumbnail (0-5% Progress)
            # Optimization: Check if thumbnail exists from a previous run
            current_vars = self.asset.variants or {}
            thumb_key = current_vars.get('thumbnail')
            
            if not thumb_key:
                thumb_key = self._process_thumbnail(input_url, total_duration)
                if on_progress_callback:
                    on_progress_callback(5.0, thumb_key=thumb_key)
            else:
                logger.info("Thumbnail already exists, skipping generation.")

            # 3. Generate HLS Adaptive Stream (5-100% Progress)
            hls_master_key = self._process_hls(
                input_url, 
                metadata, 
                on_progress_callback, 
                on_checkpoint_save, 
                on_playable_callback
            )

            # 4. Delete Original Raw File
            # We don't need the massive MP4 anymore, saving S3 storage costs.
            self._delete_original()

            return hls_master_key, thumb_key

        except Exception as e:
            logger.error(f"Video Processing Failed: {e}")
            raise e
        finally:
            # OUTER CLEANUP:
            # Ensure the entire temporary directory is deleted even if code crashes.
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
        """
        Generates a highly optimized WebP thumbnail.
        WebP is ~30% smaller than JPEG at the same quality.
        """
        output_path = os.path.join(self.temp_dir, "thumbnail.webp")
        # Take screenshot at 1s mark (avoids black frames at 0s), or 0s if video is tiny
        timestamp = 1 if duration > 1 else 0
        
        (
            ffmpeg
            .input(input_url, ss=timestamp)
            .filter('scale', 320, -1) # 320px width for chat bubbles
            .output(
                output_path, 
                vframes=1, 
                vcodec='libwebp',      
                **{'qscale': 75}      # Quality 75 (Best balance)
            )
            .run(quiet=True, overwrite_output=True)
        )
        
        thumb_key = f"processed/{self.asset.id}/thumbnail.webp"
        
        with open(output_path, "rb") as f:
            s3.upload_fileobj(f, self.bucket, thumb_key, ExtraArgs={
                "ContentType": "image/webp", 
                "CacheControl": "max-age=31536000" # Cache for 1 Year (Immutable)
            })
        
        return thumb_key

    def _process_hls(self, input_url, metadata, callback, checkpoint_callback, playable_callback):
        input_min_dim = min(metadata['width'], metadata['height'])
        base_s3_prefix = f"processed/{self.asset.id}/hls"
        master_key = f"{base_s3_prefix}/master.m3u8"
        
        # Load completed parts from DB (for Resume)
        completed_parts = self.asset.variants.get('hls_parts', {})

        # Filter valid resolutions (don't upscale 480p video to 1080p)
        valid_resolutions = [
            r for r in RESOLUTIONS 
            if input_min_dim >= (min(r["w"], r["h"]) * 0.9)
        ]
        # Fallback: If video is tiny (e.g. 100px), just use the smallest resolution (240p)
        if not valid_resolutions: valid_resolutions = [RESOLUTIONS[-1]]

        # --- OPTIMIZATION: SMALLEST FIRST ---
        # Sort by height ascending (240p -> 360p -> ... -> 1080p)
        # This ensures the user gets a "Playable" video as fast as possible.
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
                
                # Still need to add it to the master playlist lines!
                master_playlist_lines.append(
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                    f"{variant_name}/index.m3u8"
                )
                
                # Fake update UI so progress bar doesn't jump weirdly
                if callback:
                    fake_pct = base_progress + ((i + 1) * progress_per_variant)
                    callback(fake_pct)
                    
                # If this was the first one (even if resumed), ensure master exists
                # This handles the edge case where server crashed AFTER upload but BEFORE playable notification
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
                        vf=f"scale=-2:{res['h']}", # -2 ensures width is divisible by 2 (Required by H.264)
                        c="libx264",
                        b=res['bitrate'],
                        maxrate=res['maxrate'],
                        bufsize=res['bufsize'],
                        format="hls",
                        hls_time=SEGMENT_DURATION,
                        hls_list_size=0, # 0 means "Keep all segments" (VOD)
                        hls_segment_filename=segment_pattern,
                        hls_flags="delete_segments",
                        g=SEGMENT_DURATION * 30, # Force Keyframe every 10s for perfect cutting
                        preset="veryfast",       # Best balance for user uploads
                        progress=progress_url
                    )
                    .global_args('-nostats') 
                    .run(quiet=True, overwrite_output=True)
                )
            finally:
                tracker.stop()

            # 1. Upload files
            self._upload_directory(variant_dir, f"{base_s3_prefix}/{variant_name}")
            
            # 2. IMMEDIATE CLEANUP (Optimization)
            # Remove this specific quality folder from local disk immediately.
            # This keeps disk usage low (max ~1GB) even for huge videos.
            try:
                shutil.rmtree(variant_dir)
            except OSError as e:
                logger.warning(f"Error cleaning up {variant_dir}: {e}")
            
            # 3. Save checkpoint to DB
            if checkpoint_callback:
                checkpoint_callback(variant_name)

            # --- INCREMENTAL MASTER PLAYLIST UPDATE ---
            # Add this new variant to our list
            master_playlist_lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                f"{variant_name}/index.m3u8"
            )
            
            # Upload the new Master Playlist immediately
            # This enables "Progressive Playback" (Watch 240p while 1080p processes)
            self._update_master_playlist(master_playlist_lines, master_key)
            
            # Notify "Playable" (Only on the first successful variant)
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
                "CacheControl": "no-cache" # Important! Tells player to always check for new qualities
            })

    def _upload_directory(self, local_dir, s3_prefix):
        """Recursively uploads a directory to S3 with optimized headers"""
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                s3_key = f"{s3_prefix}/{file}"
                
                # Smart Caching Strategy
                if file.endswith(".ts") or file.endswith(".webp"):
                    # Video chunks & images never change -> Cache for 1 Year
                    ctype = "video/MP2T" if file.endswith(".ts") else "image/webp"
                    cache = "max-age=31536000"
                else:
                    # Playlists (.m3u8) might change -> Do not cache
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