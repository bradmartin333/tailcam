# tailcam

A small MJPEG webcam server for watching the dogs, either over Tailscale or publicly behind a secret link. It detects every attached camera at startup and serves them all on one page.

Cameras only capture while someone is watching. Ten seconds (`TAILCAM_IDLE_GRACE`) after the last viewer leaves (or hides the tab), a camera is released and stops using CPU. The next viewer wakes it up again, which takes about a second. Audio keeps playing while the tab is hidden.

Tap a feed to listen to its mic. The page loads with camera 0 selected and muted, because browsers block autoplay. Tap the selected feed to toggle mute, or tap another feed to switch to it. A green outline means the selected feed has audio. Red means that camera has no mic.

## Run it

The app has no login of its own, so something in front of it has to decide who gets in. There are two ways to run it. Both use the same image, `bradmartin333/tailcam:latest`, which CI pushes to Docker Hub on every commit to `main`. Both compose files name the container `tailcam`, so run only one of them on a given box.

### Over Tailscale

[`compose.yml`](compose.yml) binds port 8555 on the box's tailnet IP only, so anyone on the tailnet can watch and nobody else can reach it.

```sh
echo "TAILSCALE_IP=$(tailscale ip -4)" > .env
docker compose pull && docker compose up -d
```

Then open `http://<tailnet-ip>:8555/`.

### Publicly, behind a token link

The homelab runs it this way at `https://$TAILCAM_DOMAIN`, from [`tailcam/docker-compose.yml`](https://github.com/bradmartin333/homelab/blob/main/tailcam/docker-compose.yml) in the homelab repo. There it's deployed with the rest of the stack and reached through the Cloudflare tunnel and traefik, with no published port. Traefik labels do the gating:

1. Share `https://$TAILCAM_DOMAIN/?k=<TAILCAM_TOKEN>`.
2. The first visit sets a one-year `tc` cookie and redirects to the bare URL, so the token doesn't stay in the address bar or history.
3. After that, the bare URL and the key link both work. Without the cookie, every path is a 404.

`TAILCAM_TOKEN` lives in the homelab's `tailcam/.env` (sops-encrypted as `.env.enc`) and must be hex (`openssl rand -hex 32`), because it's pasted into a router regex. Changing it breaks every link and cookie already handed out. It also shows up in traefik's access log on the first visit. `TAILCAM_DOMAIN` is set in the homelab's root `.env`.

## Endpoints

| Path | |
|---|---|
| `/` | grid of all cameras |
| `/stream/<n>` | MJPEG stream for camera index `n` |
| `/audio/<n>` | raw PCM (s16le, mono, 24 kHz) from camera `n`'s mic (404 if it has none) |
| `/healthz` | liveness check |

## Configuration

| Env var | Default | |
|---|---|---|
| `TAILCAM_PORT` | `8080` | port inside the container |
| `TAILCAM_MAX_CAMERAS` | `10` | highest device index probed |
| `TAILCAM_JPEG_QUALITY` | `80` | 0–100 |
| `TAILCAM_IDLE_GRACE` | `10` | seconds without viewers before a camera is released |
| `TAILCAM_ALWAYS_ON` | `0` | `1` keeps every camera capturing even with no viewers |

## Local dev

```sh
uv run --with-requirements requirements.txt -m tailcam
```

On Linux each camera's mic is found through sysfs (same USB device). On macOS it's matched by name ("MacBook Air Camera" → "MacBook Air Microphone") and captured with a pip-bundled ffmpeg, so nothing needs installing. The first time you listen, macOS asks for microphone permission for your terminal.
