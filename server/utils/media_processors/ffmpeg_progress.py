import socket
import threading
import logging
import os
import contextlib

logger = logging.getLogger(__name__)

class FFmpegProgressTracker:
    def __init__(self, total_duration, on_progress):
        """
        :param total_duration: Duration of the video in seconds (float)
        :param on_progress: Callback function(percentage: float)
        """
        self.total_duration = total_duration
        self.on_progress = on_progress
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(('localhost', 0))  # Bind to any free port
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._listen)

    def get_ffmpeg_arg(self):
        """Returns the argument string to pass to FFmpeg"""
        return f"tcp://127.0.0.1:{self.port}"

    def start(self):
        self.thread.start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass
        self.thread.join(timeout=1)

    def _listen(self):
        conn = None
        try:
            conn, addr = self.sock.accept()
            conn.settimeout(2.0)
            buffer = ""
            while self.running:
                try:
                    data = conn.recv(1024)
                    if not data:
                        break
                    buffer += data.decode('utf-8', errors='ignore')
                    
                    # Process complete lines
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        self._parse_line(line)
                except socket.timeout:
                    continue
                except Exception:
                    break
        except Exception as e:
            logger.debug(f"Progress socket closed or error: {e}")
        finally:
            if conn:
                conn.close()

    def _parse_line(self, line):
        # format: out_time_ms=1234567
        # or: out_time=00:00:01.500000
        key, sep, value = line.partition('=')
        if key.strip() == 'out_time_us': # Microseconds
            try:
                microseconds = int(value)
                current_seconds = microseconds / 1_000_000
                if self.total_duration > 0:
                    percent = (current_seconds / self.total_duration) * 100
                    self.on_progress(min(max(percent, 0), 99)) # Cap at 99%
            except ValueError:
                pass