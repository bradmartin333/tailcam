# tailcam

A small MJPEG webcam server for watching the dogs over Tailscale. It detects every attached camera at startup and serves them all on one page.

## Run on the homelab

```sh
docker compose pull && docker compose up -d
```

Open `http://<tailscale-host>:8555` from any device on the tailnet. Access control is the tailnet itself; nothing is exposed beyond it unless the host forwards the port.

### More cameras

Plug the camera in, check `ls /dev/video*`, add a `devices:` line for it in `compose.yml`, and `docker compose up -d`. Cameras are detected at startup only. Many USB webcams create two nodes (e.g. `video0` and `video1`); mapping both is harmless since metadata-only nodes are skipped.

## Endpoints

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
