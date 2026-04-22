"""
screen_stream.py
~~~~~~~~~~~~~~~~
Live device screen streaming via mysc (MYScrcpy) VideoAdapter.

Each device gets one shared VideoAdapter instance. Frames are
captured at ~15 fps and yielded as an MJPEG multipart stream
suitable for Flask Response(mimetype='multipart/x-mixed-replace; boundary=frame').
"""

import io
import threading
import time
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ── Shared adapter registry ──────────────────────────────────────────────────
_adapters: Dict[str, object] = {}
_lock = threading.Lock()

_FPS = 15
_INTERVAL = 1.0 / _FPS


def _get_or_create(device_serial: str):
    """Return an active VideoAdapter for *device_serial*, creating one if needed."""
    try:
        from mysc.core.video import VideoAdapter, VideoKwargs
        from adbutils import adb
    except ImportError as exc:
        logger.error("mysc not available: %s", exc)
        return None

    with _lock:
        va = _adapters.get(device_serial)
        if va is not None:
            try:
                if va.is_ready:
                    return va
            except Exception:
                pass
            # stale — clean up
            try:
                va.disconnect()
            except Exception:
                pass
            del _adapters[device_serial]

        # ── Create a new adapter ──────────────────────────────────────────
        try:
            device = next(
                (d for d in adb.device_list() if d.serial == device_serial),
                None,
            )
            if device is None:
                logger.warning("Device %s not found via ADB", device_serial)
                return None

            va = VideoAdapter(
                VideoKwargs(
                    video_codec=VideoKwargs.EnumVideoCodec.H264,
                    max_fps=_FPS,
                    max_size=720,
                )
            )
            va.connect(device)

            # Wait up to 10 s for the first decoded frame
            deadline = time.time() + 10
            while not va.is_ready and time.time() < deadline:
                time.sleep(0.1)

            if not va.is_ready:
                logger.warning("VideoAdapter timed out for %s", device_serial)
                va.disconnect()
                return None

            _adapters[device_serial] = va
            logger.info("mysc VideoAdapter connected: %s", device_serial)
            return va

        except Exception as exc:
            logger.error("VideoAdapter creation failed for %s: %s", device_serial, exc)
            return None


def release_adapter(device_serial: str) -> None:
    """Disconnect the adapter for *device_serial* and remove it from the registry."""
    with _lock:
        va = _adapters.pop(device_serial, None)
    if va is not None:
        try:
            va.disconnect()
        except Exception:
            pass
        logger.info("mysc VideoAdapter released: %s", device_serial)


def release_all() -> None:
    """Disconnect all active adapters (call on app shutdown)."""
    with _lock:
        items = list(_adapters.items())
        _adapters.clear()
    for serial, va in items:
        try:
            va.disconnect()
        except Exception:
            pass
    if items:
        logger.info("mysc: released %d adapter(s)", len(items))


# ── MJPEG helpers ─────────────────────────────────────────────────────────────

def _boundary(jpeg_bytes: bytes) -> bytes:
    return (
        b"--frame\r\n"
        b"Content-Type: image/jpeg\r\n\r\n"
        + jpeg_bytes
        + b"\r\n"
    )


def _error_jpeg(message: str) -> bytes:
    """Generate a minimal error-image JPEG."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (480, 854), (28, 28, 35))
    draw = ImageDraw.Draw(img)
    # Centre-ish text
    draw.text((20, 390), "Screen unavailable", fill=(180, 180, 180))
    draw.text((20, 415), message[:60], fill=(200, 60, 60))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return buf.getvalue()


def generate_mjpeg(device_serial: str):
    """
    Generator that yields MJPEG boundary chunks for a live device screen.

    Usage in a Flask route::

        from flask import Response
        from app.utils.screen_stream import generate_mjpeg

        return Response(
            generate_mjpeg(device_id),
            mimetype='multipart/x-mixed-replace; boundary=frame',
        )
    """
    va = _get_or_create(device_serial)
    if va is None:
        yield _boundary(_error_jpeg(f"Cannot connect to {device_serial}"))
        return

    try:
        while True:
            try:
                img = va.get_image()
            except Exception as exc:
                logger.error("get_image error [%s]: %s", device_serial, exc)
                yield _boundary(_error_jpeg(str(exc)))
                break

            if img is None:
                time.sleep(_INTERVAL)
                continue

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=72)
            yield _boundary(buf.getvalue())
            time.sleep(_INTERVAL)

    except GeneratorExit:
        pass
