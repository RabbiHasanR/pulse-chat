import os
import ffmpeg
import logging
import shutil
import tempfile
import magic   # pip install python-magic
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
        """
        try:
            input_url = self.get_input_url()
            logger.info(f"Starting Video Processing for Asset: {self.asset.id}")

            # --- 1. SECURITY & INTEGRITY VALIDATION ---
            # We check this BEFORE doing anything expensive.
            try:
                self._validate_remote_source(input_url)
            except ValueError as e:
                logger.warning(f"Security/Validation Failed: {e}")
                # Dangerous file: Delete immediately
                self._delete_original()
                raise e
            # ------------------------------------------

            # 2. Probe Input (Over Network)
            metadata = self._get_metadata(input_url)
            total_duration = metadata['duration']
            
            # 3. Generate Thumbnail (0-5% Progress)
            current_vars = self.asset.variants or {}
            thumb_key = current_vars.get('thumbnail')
            
            if not thumb_key:
                thumb_key = self._process_thumbnail(input_url, total_duration)
                if on_progress_callback:
                    on_progress_callback(5.0, thumb_key=thumb_key)
            else:
                logger.info("Thumbnail already exists, skipping generation.")

            # 4. Generate HLS Adaptive Stream (5-100% Progress)
            hls_master_key = self._process_hls(
                input_url, 
                metadata, 
                on_progress_callback, 
                on_checkpoint_save, 
                on_playable_callback
            )

            # 5. Delete Original Raw File
            self._delete_original()

            return hls_master_key, thumb_key

        except Exception as e:
            logger.error(f"Video Processing Failed: {e}")
            raise e
        finally:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    def _validate_remote_source(self, presigned_url):
        """
        Validates Magic Bytes (Security) and Stream Integrity (Corruption)
        without downloading the full file.
        """
        # A. Security Check (Magic Bytes) via Range Request
        # Download only the first 2KB to memory.
        try:
            response = s3.get_object(
                Bucket=self.bucket, 
                Key=self.original_key, 
                Range='bytes=0-2048'
            )
            head_bytes = response['Body'].read()
            
            # Check magic bytes from buffer
            mime_type = magic.from_buffer(head_bytes, mime=True)
            
            # Strict Blocklist
            forbidden = [
                'application/x-dosexec',
                'application/x-executable',
                'text/x-python',
                'text/javascript',
                'text/html'
            ]
            
            if mime_type in forbidden:
                raise ValueError(f"Security Alert: Forbidden file type {mime_type}")
                
            # Optional: Enforce video MIME types (Safest)
            # if not mime_type.startswith('video/') and mime_type != 'application/octet-stream':
            #     raise ValueError(f"Invalid Video Type: {mime_type}")

        except Exception as e:
            raise ValueError(f"Security Check Failed: {str(e)}")

        # B. Integrity Check via FFmpeg Network Probe
        # FFmpeg connects to the URL and reads just enough packets to parse headers.
        try:
            probe = ffmpeg.probe(presigned_url)
            
            # Check if it actually has a video stream
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                raise ValueError("File contains no valid video stream")
                
        except ffmpeg.Error as e:
            error_log = e.stderr.decode() if e.stderr else str(e)
            raise ValueError(f"Corrupt Video File: {error_log}")

    def _get_metadata(self, input_url):
        # We reuse the probe logic but return specific data
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
            # Should be caught by validation, but double check
            raise ValueError(f"FFProbe failed: {e}")

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
        # ... (This method remains exactly the same as previous) ...
        # Copy the previous implementation of _process_hls, _upload_directory, 
        # _update_master_playlist here.
        # It is already highly optimized.
        
        input_min_dim = min(metadata['width'], metadata['height'])
        base_s3_prefix = f"processed/{self.asset.id}/hls"
        master_key = f"{base_s3_prefix}/master.m3u8"
        
        completed_parts = self.asset.variants.get('hls_parts', {})

        valid_resolutions = [
            r for r in RESOLUTIONS 
            if input_min_dim >= (min(r["w"], r["h"]) * 0.9)
        ]
        if not valid_resolutions: valid_resolutions = [RESOLUTIONS[-1]]

        valid_resolutions.sort(key=lambda x: x['h'])

        total_variants = len(valid_resolutions)
        base_progress = 5.0
        progress_per_variant = 95.0 / total_variants
        
        master_playlist_lines = ["#EXTM3U"]

        for i, res in enumerate(valid_resolutions):
            variant_name = res['name']
            bandwidth = int(res['bitrate'].replace('k', '000'))
            
            if completed_parts.get(variant_name):
                logger.info(f"Skipping {variant_name} (Found in checkpoint)")
                master_playlist_lines.append(
                    f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                    f"{variant_name}/index.m3u8"
                )
                if callback:
                    fake_pct = base_progress + ((i + 1) * progress_per_variant)
                    callback(fake_pct)
                if i == 0:
                    self._update_master_playlist(master_playlist_lines, master_key)
                    if playable_callback: playable_callback(master_key)
                continue

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

            self._upload_directory(variant_dir, f"{base_s3_prefix}/{variant_name}")
            
            try:
                shutil.rmtree(variant_dir)
            except OSError as e:
                logger.warning(f"Error cleaning up {variant_dir}: {e}")
            
            if checkpoint_callback:
                checkpoint_callback(variant_name)

            master_playlist_lines.append(
                f"#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={res['w']}x{res['h']}\n"
                f"{variant_name}/index.m3u8"
            )
            
            is_last_variant = (i == total_variants - 1)
            cache_control = "max-age=31536000" if is_last_variant else "no-cache"
            self._update_master_playlist(master_playlist_lines, master_key, cache_control)
            
            if i == 0 and playable_callback:
                playable_callback(master_key)

        return master_key

    def _update_master_playlist(self, lines, s3_key, cache_control="no-cache"):
        content = "\n".join(lines)
        master_path = os.path.join(self.temp_dir, "master.m3u8")
        
        with open(master_path, "w") as f:
            f.write(content)
            
        with open(master_path, "rb") as f:
            s3.upload_fileobj(f, self.bucket, s3_key, ExtraArgs={
                "ContentType": "application/x-mpegURL",
                "CacheControl": cache_control
            })

    def _upload_directory(self, local_dir, s3_prefix):
        for root, dirs, files in os.walk(local_dir):
            for file in files:
                local_path = os.path.join(root, file)
                s3_key = f"{s3_prefix}/{file}"
                
                if file.endswith(".ts") or file.endswith(".webp"):
                    ctype = "video/MP2T" if file.endswith(".ts") else "image/webp"
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