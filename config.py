import os

PORT = int(os.environ.get("TAILCAM_PORT", "8080"))
MAX_CAMERAS = int(os.environ.get("TAILCAM_MAX_CAMERAS", "10"))
JPEG_QUALITY = int(os.environ.get("TAILCAM_JPEG_QUALITY", "80"))
AUDIO_RATE = 24000  # mono s16le; ~48 KB/s is nothing on a tailnet and avoids codec delay
# Cameras release their device once nobody has watched for IDLE_GRACE seconds; set TAILCAM_ALWAYS_ON=1 to keep them streaming.
ALWAYS_ON = os.environ.get("TAILCAM_ALWAYS_ON", "0") == "1"
IDLE_GRACE = 10
