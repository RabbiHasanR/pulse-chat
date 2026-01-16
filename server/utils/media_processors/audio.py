import os
import struct
import magic   # pip install python-magic
import ffmpeg  # pip install ffmpeg-python
import logging
import tempfile
import shutil
from utils.aws import s3

logger = logging.getLogger(__name__)

class AudioProcessor:
    def __init__(self, asset):
        self.asset = asset
        self.bucket = asset.bucket
        self.original_key = asset.object_key
        # Temp dir is ONLY for the output files (tiny), not the input (huge)
        self.temp_dir = tempfile.mkdtemp()

    def process(self):
        try:
            logger.info(f"Starting Streaming Audio Processing: {self.asset.id}")
            
            # 1. Generate Input URL (Stream from S3)
            input_url = s3.generate_presigned_url(
                ClientMethod='get_object',
                Params={'Bucket': self.bucket, 'Key': self.original_key},
                ExpiresIn=3600
            )

            # 2. Validation (Zero-Download)
            # We pass the URL for probing, and the Key for byte-range checking
            try:
                self._validate_remote_source(input_url, self.original_key)
            except ValueError as e:
                logger.warning(f"Validation Failed: {e}")
                self._delete_from_s3(self.original_key)
                raise e

            # 3. Transcode (Streaming Input)
            # FFmpeg reads directly from 'input_url'
            output_filename = f"audio_{self.asset.id}.m4a"
            output_path = os.path.join(self.temp_dir, output_filename)
            
            (
                ffmpeg
                .input(input_url)  # <--- Reading from Network, not Disk
                .output(
                    output_path, 
                    acodec='aac', 
                    audio_bitrate='64k', 
                    ac=1, 
                    movflags='+faststart' 
                )
                .global_args('-nostats')
                .run(quiet=True, overwrite_output=True)
            )

            # 4. Generate Metadata (On local output)
            duration = self._get_duration(output_path)
            
            # Generate PCM for waveform (Local file -> Local PCM)
            waveform_pcm_path = os.path.join(self.temp_dir, "waveform.pcm")
            (
                ffmpeg
                .input(output_path)
                .output(waveform_pcm_path, format='s16le', acodec='pcm_s16le', ac=1, ar='8000')
                .run(quiet=True, overwrite_output=True)
            )
            waveform = self._calculate_waveform_peaks(waveform_pcm_path, bars=50)

            # 5. Upload & Cleanup
            final_key = f"processed/{self.asset.id}/audio.m4a"
            file_size = os.path.getsize(output_path)
            
            with open(output_path, 'rb') as f:
                s3.upload_fileobj(f, self.bucket, final_key, ExtraArgs={
                    "ContentType": "audio/mp4",
                    "CacheControl": "max-age=31536000"
                })

            self._delete_from_s3(self.original_key)

            return {
                "object_key": final_key,
                "file_size": file_size,
                "variants": {
                    "type": "audio",
                    "format": "m4a",
                    "duration": duration,
                    "waveform": waveform
                }
            }

        except Exception as e:
            logger.error(f"Audio Processor Failed: {e}")
            raise e
        finally:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    def _validate_remote_source(self, presigned_url, object_key):
        """
        Validates the file WITHOUT downloading it entirely.
        """
        
        # A. Security Check (Magic Bytes) via Range Request
        # Download only the first 2KB to memory.
        try:
            response = s3.get_object(
                Bucket=self.bucket, 
                Key=object_key, 
                Range='bytes=0-2048' # Fetch header only
            )
            head_bytes = response['Body'].read()
            
            # Check magic bytes from buffer
            mime_type = magic.from_buffer(head_bytes, mime=True)
            
            forbidden = [
                'application/x-dosexec',
                'application/x-executable',
                'text/x-python',
                'text/javascript',
                'text/html'
            ]
            
            if mime_type in forbidden:
                raise ValueError(f"Security Alert: Forbidden file type {mime_type}")
                
        except Exception as e:
            # Catch S3 errors or Magic errors
            raise ValueError(f"Security Check Failed: {str(e)}")

        # B. Integrity Check via FFmpeg Network Probe
        # FFmpeg connects to the URL and reads just enough packets to parse headers.
        try:
            probe = ffmpeg.probe(presigned_url)
            if not probe.get('streams'):
                raise ValueError("File contains no valid media streams")
        except ffmpeg.Error as e:
            error_log = e.stderr.decode() if e.stderr else str(e)
            raise ValueError(f"Corrupt Media File: {error_log}")

    # ... (_get_duration, _calculate_waveform_peaks, _delete_from_s3 remain same) ...
    # (Copy them from the previous answer)
    def _get_duration(self, file_path):
        try:
            probe = ffmpeg.probe(file_path)
            stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
            return float(stream['duration']) if stream else 0.0
        except Exception:
            return 0.0

    def _calculate_waveform_peaks(self, pcm_path, bars=50):
        try:
            total_bytes = os.path.getsize(pcm_path)
            if total_bytes == 0: return [0] * bars

            total_samples = total_bytes // 2
            samples_per_bar = total_samples // bars
            if samples_per_bar == 0: return [0] * bars

            bytes_per_bar = samples_per_bar * 2
            peaks = []

            with open(pcm_path, 'rb') as f:
                for _ in range(bars):
                    chunk_bytes = f.read(bytes_per_bar)
                    if not chunk_bytes:
                        peaks.append(0)
                        continue

                    max_val = 0
                    count = len(chunk_bytes) // 2
                    iter_step = 1 if count < 1000 else int(count / 100)
                    
                    for i in range(0, count, iter_step):
                        sample_bytes = chunk_bytes[i*2 : i*2+2]
                        if len(sample_bytes) < 2: break
                        val = abs(struct.unpack('<h', sample_bytes)[0])
                        if val > max_val:
                            max_val = val
                    
                    normalized = min(int((max_val / 32768) * 100), 100)
                    peaks.append(normalized)

            return peaks
        except Exception:
            return [0] * bars

    def _delete_from_s3(self, key):
        try:
            s3.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.warning(f"S3 Delete Failed for {key}: {e}")