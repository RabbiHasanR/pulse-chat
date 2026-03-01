import os
import magic   # pip install python-magic
import ffmpeg  # pip install ffmpeg-python
import logging
import tempfile
import shutil
import numpy as np  # pip install numpy
from concurrent.futures import ThreadPoolExecutor
from utils.aws import s3

logger = logging.getLogger(__name__)

# Audio files shorter than this are piped to memory for waveform generation.
# Longer files fall back to a temp PCM file to avoid large in-memory buffers.
# At 8kHz mono s16le: 1200s * 8000 * 2 = ~19MB ceiling (20-min threshold)
PCM_MEMORY_DURATION_THRESHOLD_SECS = 1200  # 20 minutes


class AudioProcessor:
    def __init__(self, asset):
        self.asset = asset
        self.bucket = asset.bucket
        self.original_key = asset.object_key
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

            # 2. Validation & Duration (Parallel — magic bytes + FFprobe run concurrently)
            try:
                duration = self._validate_and_probe_parallel(input_url, self.original_key)
            except ValueError as e:
                logger.warning(f"Validation Failed: {e}")
                self._delete_from_s3(self.original_key)
                raise

            # Bind duration early so WebSocket progress events don't cause UI layout shifts
            self.asset.duration_seconds = duration

            # 3. SINGLE-PASS FFmpeg Execution
            # Input is decoded once, audio split at filter graph level:
            #   Branch A → AAC/M4A written to disk
            #   Branch B → PCM for waveform (piped to memory OR written to temp file,
            #               depending on duration threshold)
            output_filename = f"audio_{self.asset.id}.m4a"
            output_path = os.path.join(self.temp_dir, output_filename)

            use_pipe = duration <= PCM_MEMORY_DURATION_THRESHOLD_SECS
            pcm_path = None if use_pipe else os.path.join(self.temp_dir, "waveform.pcm")

            if use_pipe:
                logger.debug(f"PCM strategy: pipe (duration={duration:.1f}s <= {PCM_MEMORY_DURATION_THRESHOLD_SECS}s)")
            else:
                logger.debug(f"PCM strategy: temp file (duration={duration:.1f}s > {PCM_MEMORY_DURATION_THRESHOLD_SECS}s)")

            stream = ffmpeg.input(input_url)
            split = stream.audio.asplit()

            m4a_out = split[0].output(
                output_path,
                acodec='aac',
                audio_bitrate='64k',
                ac=1,
                movflags='+faststart'
            )

            pcm_out = split[1].output(
                'pipe:' if use_pipe else pcm_path,
                format='s16le',
                acodec='pcm_s16le',
                ac=1,
                ar='8000'
            )

            process = (
                ffmpeg
                .merge_outputs(m4a_out, pcm_out)
                .global_args('-nostats')
                .run_async(
                    pipe_stdout=use_pipe,
                    pipe_stderr=True
                )
            )

            stdout, stderr = process.communicate()

            if process.returncode != 0:
                error_log = stderr.decode('utf8', errors='ignore')
                raise ValueError(f"FFmpeg Transcode Error: {error_log}")

            # 4. Generate Waveform
            # Source is either the in-memory pipe bytes or the temp PCM file
            if use_pipe:
                waveform = self._calculate_waveform_peaks(pcm_bytes=stdout, bars=50)
            else:
                waveform = self._calculate_waveform_peaks_from_file(pcm_path, bars=50)

            # 5. Upload processed file to S3
            final_key = f"processed/{self.asset.id}/audio.m4a"
            file_size = os.path.getsize(output_path)

            with open(output_path, 'rb') as f:
                s3.upload_fileobj(f, self.bucket, final_key, ExtraArgs={
                    "ContentType": "audio/mp4",
                    "CacheControl": "max-age=31536000"
                })

            # 6. Delete original upload from S3
            self._delete_from_s3(self.original_key)

            return {
                "object_key": final_key,
                "file_size": file_size,
                "duration_seconds": duration,
                "variants": {
                    "type": "audio",
                    "format": "m4a",
                    "waveform": waveform
                }
            }

        except Exception as e:
            logger.error(f"Audio Processor Failed: {e}")
            raise
        finally:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def _validate_and_probe_parallel(self, presigned_url: str, object_key: str) -> float:
        """
        Runs magic byte check and FFprobe concurrently.
        Returns duration (float, seconds) extracted from the FFprobe result.
        Raises ValueError on any failure.
        """
        with ThreadPoolExecutor(max_workers=2) as executor:
            magic_future = executor.submit(self._check_magic_bytes, object_key)
            probe_future = executor.submit(self._probe_media, presigned_url)

            # Both must succeed — if either raises, it surfaces here
            magic_future.result()
            duration = probe_future.result()

        return duration

    def _check_magic_bytes(self, object_key: str):
        """
        Downloads only the first 2KB via S3 range request and checks MIME type.
        Raises ValueError for forbidden file types.
        """
        forbidden = {
            'application/x-dosexec',
            'application/x-executable',
            'text/x-python',
            'text/javascript',
            'text/html',
        }
        try:
            response = s3.get_object(
                Bucket=self.bucket,
                Key=object_key,
                Range='bytes=0-2048'
            )
            mime_type = magic.from_buffer(response['Body'].read(), mime=True)
            if mime_type in forbidden:
                raise ValueError(f"Security Alert: Forbidden file type '{mime_type}'")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Security Check Failed: {e}")

    def _probe_media(self, presigned_url: str) -> float:
        """
        FFprobe the remote URL — reads only enough packets to parse headers.
        Returns duration in seconds. Raises ValueError on corrupt/invalid files.
        """
        try:
            probe = ffmpeg.probe(presigned_url)
            stream = next(
                (s for s in probe.get('streams', []) if s['codec_type'] == 'audio'),
                None
            )
            if not stream:
                raise ValueError("File contains no valid audio streams")
            return float(stream.get('duration', 0.0))
        except ffmpeg.Error as e:
            error_log = e.stderr.decode() if e.stderr else str(e)
            raise ValueError(f"Corrupt Media File: {error_log}")

    # -------------------------------------------------------------------------
    # Waveform
    # -------------------------------------------------------------------------

    def _calculate_waveform_peaks(self, pcm_bytes: bytes, bars: int = 50) -> list[int]:
        """
        Vectorized waveform peak extraction from an in-memory PCM byte string.
        Used for short audio (under PCM_MEMORY_DURATION_THRESHOLD_SECS).
        """
        try:
            if not pcm_bytes:
                return [0] * bars

            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            return self._peaks_from_samples(samples, bars)
        except Exception:
            return [0] * bars

    def _calculate_waveform_peaks_from_file(self, pcm_path: str, bars: int = 50) -> list[int]:
        """
        Vectorized waveform peak extraction from a temp PCM file.
        Used for long audio (over PCM_MEMORY_DURATION_THRESHOLD_SECS).
        Reads the file in chunks to avoid loading it all into memory at once.
        """
        try:
            file_size = os.path.getsize(pcm_path)
            if file_size == 0:
                return [0] * bars

            total_samples = file_size // 2  # s16le = 2 bytes per sample
            samples_per_bar = total_samples // bars
            if samples_per_bar == 0:
                return [0] * bars

            bytes_per_bar = samples_per_bar * 2
            peaks = []

            with open(pcm_path, 'rb') as f:
                for _ in range(bars):
                    chunk = f.read(bytes_per_bar)
                    if not chunk:
                        peaks.append(0)
                        continue
                    
                    # 🚀 FIX: Prevent ValueError if EOF results in an odd number of bytes
                    if len(chunk) % 2 != 0:
                        chunk = chunk[:-1]
                        
                    if not chunk: # In case it was only 1 byte
                        peaks.append(0)
                        continue

                    samples = np.frombuffer(chunk, dtype=np.int16)
                    peak = int(np.abs(samples).max())
                    normalized = min(int((peak / 32768) * 100), 100)
                    peaks.append(normalized)

            return peaks
        except Exception:
            return [0] * bars

    def _peaks_from_samples(self, samples: np.ndarray, bars: int) -> list[int]:
        """Shared vectorized peak logic for numpy sample arrays."""
        if len(samples) == 0:
            return [0] * bars

        trim = (len(samples) // bars) * bars
        if trim == 0:
            return [0] * bars

        chunks = samples[:trim].reshape(bars, -1)       # (bars, samples_per_bar)
        peaks = np.abs(chunks).max(axis=1)              # peak amplitude per bar
        normalized = np.clip((peaks / 32768 * 100).astype(int), 0, 100)
        return normalized.tolist()

    # -------------------------------------------------------------------------
    # S3 helpers
    # -------------------------------------------------------------------------

    def _delete_from_s3(self, key: str):
        try:
            s3.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.warning(f"S3 Delete Failed for {key}: {e}")