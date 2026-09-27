# tailcam

A small MJPEG webcam server for watching the dogs over Tailscale. It detects every attached camera at startup and serves them all on one page.

Tap a feed to listen to its mic. The page loads with camera 0 selected and muted, because browsers block autoplay. Tap the selected feed to toggle mute, or tap another feed to switch to it. A green outline means the selected feed has audio. Red means that camera has no mic.

## Run on the homelab

```sh
docker compose pull && docker compose up -d
```

| Path | |
|---|---|
| `/` | grid of all cameras |
| `/stream/<n>` | MJPEG stream for camera index `n` |
| `/audio/<n>` | MP3 stream from camera `n`'s mic (404 if it has none) |
| `/healthz` | liveness check |

## Configuration

| Env var | Default | |
|---|---|---|
| `TAILCAM_PORT` | `8080` | port inside the container |
| `TAILCAM_MAX_CAMERAS` | `10` | highest device index probed |
| `TAILCAM_JPEG_QUALITY` | `80` | 0–100 |

## Local dev

```sh
uv run --with-requirements requirements.txt tailcam.py
```
