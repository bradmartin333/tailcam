# tailcam

A small MJPEG webcam server for watching the dogs over Tailscale. It detects every attached camera at startup and serves them all on one page.

## Run on the homelab

```sh
docker compose pull && docker compose up -d
```

| Path | |
|---|---|
| `/` | grid of all cameras |
| `/stream/<n>` | MJPEG stream for camera index `n` |
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
