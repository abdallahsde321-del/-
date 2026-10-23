import os
import sys
import time
import signal
import shutil
import subprocess
import threading
from datetime import datetime

# ================= SETTINGS =================

TIKTOK_URL = os.getenv(
    "TIKTOK_URL",
    "https://www.tiktok.com/@d.shakertawfiqalaroury/live"
).strip()

YOUTUBE_RTMP = os.getenv(
    "YOUTUBE_RTMP",
    "rtmp://a.rtmp.youtube.com/live2/qmbm-d84r-yvj8-exts-b3yp"
).strip()

CHECK_INTERVAL_OFFLINE = 5
RETRY_DELAY = 2
MAX_BACKOFF = 15

stop_event = threading.Event()
streamlink_process = None
ffmpeg_process = None


# ================= LOGGING =================

def log(message):
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}",
        flush=True
    )


# ================= PROCESS MANAGEMENT =================

def stop_process(process):
    if process is None or process.poll() is not None:
        return

    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=3)
        except Exception:
            pass
    except Exception as exc:
        log(f"Cleanup warning: {exc}")


def cleanup():
    global streamlink_process, ffmpeg_process

    stop_process(ffmpeg_process)
    ffmpeg_process = None

    stop_process(streamlink_process)
    streamlink_process = None


def signal_handler(sig, frame):
    log("Shutdown requested.")
    stop_event.set()
    cleanup()


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ================= STREAMLINK =================

def make_streamlink_command():
    return [
        "streamlink",
        "--loglevel", "warning",
        "--hls-live-edge", "4",
        "--ringbuffer-size", "512M",
        "--stream-segment-threads", "2",
        "--retry-streams", "5",
        "--retry-max", "5",
        "--stream-segment-attempts", "5",
        "--stream-segment-timeout", "15",
        "--stream-timeout", "30",
        "--http-header",
        "User-Agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36",
        "--stdout",
        TIKTOK_URL,
        "best"
    ]


# ================= FFMPEG =================

def make_ffmpeg_command():
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-stats_period", "10",
        "-stats",

        "-re",                             # إجبار الإرسال بالسرعة الحقيقية لمنع التقطيع
        "-fflags", "+genpts+discardcorrupt",
        "-thread_queue_size", "4096",
        "-i", "pipe:0",

        "-map", "0:v:0",
        "-c:v", "copy",

        "-map", "0:a:0?",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-af", "aresample=async=1000",

        "-fps_mode", "passthrough",
        "-flvflags", "no_duration_filesize",
        "-flush_packets", "1",

        "-f", "flv",
        YOUTUBE_RTMP
    ]


# ================= SINGLE STREAM SESSION =================

def start_stream():
    global streamlink_process, ffmpeg_process

    if stop_event.is_set():
        return False

    log("Connecting to TikTok LIVE with 512M Buffer...")

    streamlink_process = subprocess.Popen(
        make_streamlink_command(),
        stdout=subprocess.PIPE,
        stderr=None,
        bufsize=0
    )

    ffmpeg_process = subprocess.Popen(
        make_ffmpeg_command(),
        stdin=streamlink_process.stdout,
        stdout=subprocess.DEVNULL,
        stderr=None,
        bufsize=0
    )

    streamlink_process.stdout.close()

    log("Relay active: TikTok -> YouTube (512M Buffer, Video COPY, Audio AAC 128k)")

    while not stop_event.is_set():
        sl_code = streamlink_process.poll()
        ff_code = ffmpeg_process.poll()

        if sl_code is not None:
            log(f"Streamlink exited with code {sl_code}.")
            return False

        if ff_code is not None:
            log(f"FFmpeg exited with code {ff_code}.")
            return False

        stop_event.wait(0.5)

    return False


# ================= MAIN LOOP =================

def main():
    if not TIKTOK_URL:
        log("ERROR: TIKTOK_URL is empty.")
        sys.exit(1)

    if not YOUTUBE_RTMP:
        log("ERROR: YOUTUBE_RTMP is empty.")
        sys.exit(1)

    if not shutil.which("streamlink") or not shutil.which("ffmpeg"):
        log("ERROR: Streamlink or FFmpeg is not installed.")
        sys.exit(1)

    log("TikTok -> YouTube relay started.")
    failures = 0

    while not stop_event.is_set():
        started_at = time.monotonic()

        try:
            cleanup()
            start_stream()

        except Exception as exc:
            log(f"Session error: {exc}")

        finally:
            cleanup()

        if stop_event.is_set():
            break

        elapsed = time.monotonic() - started_at

        if elapsed >= 60:
            failures = 0
            delay = RETRY_DELAY
            log(f"Stream ended or disconnected. Retrying in {delay}s...")
        else:
            failures += 1
            delay = min(
                CHECK_INTERVAL_OFFLINE * (2 ** min(failures - 1, 2)),
                MAX_BACKOFF
            )
            log(
                f"Source unavailable or connection failed. "
                f"Retrying in {delay}s..."
            )

        stop_event.wait(delay)

    cleanup()
    log("Relay stopped cleanly.")


if __name__ == "__main__":
    main()
