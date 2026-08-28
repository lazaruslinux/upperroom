"""
Configuration for the projector.

Everything is read from the environment at import, like the gate's config. There
are no defaults for the secrets and no file to leave lying around: an unset key
means the projector simply cannot connect, which is the right failure.
"""

import os

# The gate's projector socket, e.g. wss://your-domain/ws/projector.
GATE_URL = os.environ.get("PROJECTOR_GATE_URL", "").strip()
# The bearer key generated in the gate's dashboard. Never logged.
KEY = os.environ.get("PROJECTOR_KEY", "").strip()

# Where to publish, e.g. rtmp://your-domain:1935/live, and the channel's stream
# key, which is appended as ?pass= exactly as OBS sends it.
INGEST_URL = os.environ.get("PROJECTOR_INGEST_URL", "").strip()
STREAM_KEY = os.environ.get("PROJECTOR_STREAM_KEY", "").strip()

# The media library. Only used outside demo mode.
JELLYFIN_URL = os.environ.get("JELLYFIN_URL", "").strip().rstrip("/")
JELLYFIN_API_KEY = os.environ.get("JELLYFIN_API_KEY", "").strip()

# Hardware encoding. Set to a render node (e.g. /dev/dri/renderD128) to encode
# with VAAPI; left unset the projector encodes with libx264 on the CPU.
VAAPI_DEVICE = os.environ.get("PROJECTOR_VAAPI_DEVICE", "").strip()

VIDEO_BITRATE = os.environ.get("PROJECTOR_VIDEO_BITRATE", "6000k").strip()
try:
    MAX_HEIGHT = int(os.environ.get("PROJECTOR_MAX_HEIGHT", "1080"))
except ValueError:
    MAX_HEIGHT = 1080

# Demo mode plays three built-in synthetic titles and never touches a library,
# so the demo stack and local testing need no Jellyfin at all.
DEMO = os.environ.get("PROJECTOR_DEMO", "").strip() == "1"

# Reconnect backoff, in seconds. The last value repeats.
BACKOFF = (1, 5, 15, 60)
# How often a playing projector reports its position.
STATUS_INTERVAL = 5


def encoder_options():
    """The options the argv builder needs, as one dict."""
    return {
        "ingest_url": INGEST_URL,
        "stream_key": STREAM_KEY,
        "bitrate": VIDEO_BITRATE,
        "max_height": MAX_HEIGHT,
        "vaapi_device": VAAPI_DEVICE,
    }
