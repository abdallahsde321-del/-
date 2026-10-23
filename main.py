
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

YOUTUBE_STREAM_KEY = "qmbm-d84r-yvj8-exts-b3yp"
YOUTUBE_RTMP = (
    f"rtmp://a.rtmp.youtube.com/live2/{YOUTUBE_STREAM_KEY}"
)

RINGBUFFER_SIZE = "512M"
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


# ================= DEPENDENCY CHECK =================

def check_dependencies():
    if not shutil.which("streamlink"):
        raise RuntimeError("Streamlink is not installed.")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is not installed.")

    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        timeout=20
    )

    encoders = result.stdout + result.stderr

    if result.returncode != 0 or "libx264" not in encoders:
        raise RuntimeError(
            "FFmpeg does not provide libx264. "
            "Install an FFmpeg build with H.264 encoding support."
        )

    log("Streamlink and libx264 verified.")


# ================= STREAMLINK =================

def make_streamlink_command():
    return [
        "streamlink",
        "--loglevel", "info",
        "--hls-live-edge", "4",
        "--ringbuffer-size", RINGBUFFER_SIZE,
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
        "-stats",

        "-fflags", "+genpts+discardcorrupt",
        "-thread_queue_size", "4096",
        "-i", "pipe:0",

        # Video: H.264 for YouTube RTMP compatibility
        "-map", "0:v:0",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-crf", "20",
        "-fps_mode", "passthrough",

        # Audio: AAC 128 kbps
        "-map", "0:a:0?",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-af", "aresample=async=1000",

        "-flvflags", "no_duration_filesize",
        "-flush_packets", "1",
        "-f", "flv",
        YOUTUBE_RTMP
    ]


# ================= STREAM SESSION =================

def start_stream():
    global streamlink_process, ffmpeg_process

    if stop_event.is_set():
        return

    log(f"Connecting to TikTok LIVE (buffer {RINGBUFFER_SIZE})...")

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

    log("Relay started: TikTok -> YouTube.")
    log("Video: H.264 / libx264 / ultrafast / CRF 20.")
    log("Audio: AAC 128 kbps.")
    log("Automatic reconnection: enabled.")

    while not stop_event.is_set():
        sl_code = streamlink_process.poll()
        ff_code = ffmpeg_process.poll()

        if sl_code is not None:
            log(f"Streamlink exited with code {sl_code}.")
            return

        if ff_code is not None:
            log(f"FFmpeg exited with code {ff_code}.")
            return

        stop_event.wait(0.5)


# ================= MAIN LOOP =================

def main():
    if not TIKTOK_URL:
        log("ERROR: TIKTOK_URL is empty.")
        sys.exit(1)

    try:
        check_dependencies()
    except Exception as exc:
        log(f"Startup error: {exc}")
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
            log(f"Stream disconnected. Retrying in {delay}s.")
        else:
            failures += 1
            delay = min(
                CHECK_INTERVAL_OFFLINE * (2 ** min(failures - 1, 2)),
                MAX_BACKOFF
            )
            log(
                f"Stream unavailable or failed. "
                f"Retrying in {delay}s."
            )

        stop_event.wait(delay)

    cleanup()
    log("Relay stopped cleanly.")


if __name__ == "__main__":
    main()
