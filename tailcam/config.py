import os

PORT = int(os.environ.get("TAILCAM_PORT", "8080"))
MAX_CAMERAS = int(os.environ.get("TAILCAM_MAX_CAMERAS", "10"))
JPEG_QUALITY = int(os.environ.get("TAILCAM_JPEG_QUALITY", "80"))
AUDIO_RATE = 24000  # mono s16le; ~48 KB/s is nothing on a tailnet and avoids codec delay
# Cameras stop processing frames (but stay open) when nobody has watched for TAILCAM_IDLE_GRACE seconds.
IDLE_GRACE = int(os.environ.get("TAILCAM_IDLE_GRACE", "10"))
# Comma-separated Host headers to answer, e.g. "100.64.0.5:8555"; anything else gets a 403. This stops a
# DNS-rebinding page from reaching the cameras through a viewer's browser. Unset answers any Host.
HOSTS = {h.strip().lower() for h in os.environ.get("TAILCAM_HOSTS", "").split(",") if h.strip()}
