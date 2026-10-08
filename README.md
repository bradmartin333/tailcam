# tailcam

A small MJPEG webcam server for watching the dogs, either over Tailscale or publicly behind a secret link. It finds every attached camera at startup and shows them all on one page.

Cameras stay on while the container runs, because a webcam clicks and flashes its LED when it switches on, and that bothers the dogs. Frames are only sent while someone is watching. Ten seconds (`TAILCAM_IDLE_GRACE`) after the last viewer leaves or hides the tab, a camera goes idle and drops its frames. Audio keeps playing while the tab is hidden.

At startup, tailcam switches off each camera's status LED. Newer Logitech webcams, such as the C920 and CrystalCam, are handled through their vendor extension unit. Any other camera whose driver exposes an LED control has that control set to off. The startup log lists each camera's controls, so `docker logs tailcam` shows what yours supports.

## Using the page

The page loads with camera 0 selected and muted, because browsers block autoplay. Tap the selected feed to toggle its mic, or tap another feed to switch to it. A green outline means the selected feed has audio, and red means its camera has no mic.

If the selected camera can focus manually, a slider appears under its feed. While "auto" is ticked, the slider is disabled and follows the autofocus. Untick it to focus by hand. The setting lasts until the container restarts.

## Run it

The app has no login of its own, so something in front of it decides who gets in. There are two ways to run it. Both use the image `bradmartin333/tailcam:latest`, which CI pushes to Docker Hub on every commit to `main`. Both compose files name the container `tailcam`, so run only one per box.

### Over Tailscale

[`compose.yml`](compose.yml) publishes port 8555 on the box's tailnet IP only, so anyone on the tailnet can watch and nobody else can reach it.

```sh
echo "TAILSCALE_IP=$(tailscale ip -4)" > .env
docker compose pull && docker compose up -d
```

Then open `http://<tailnet-ip>:8555/`. To use a MagicDNS name instead, add it to `TAILCAM_HOSTS` in `compose.yml`.

### Publicly, behind a token link

The homelab runs tailcam this way at `https://$TAILCAM_DOMAIN`, from [`tailcam/docker-compose.yml`](https://github.com/bradmartin333/homelab/blob/main/tailcam/docker-compose.yml) in the homelab repo. Traffic comes in through the Cloudflare tunnel and traefik, with no published port, and traefik labels do the gating:

1. Share `https://$TAILCAM_DOMAIN/?k=<TAILCAM_TOKEN>`.
2. The first visit sets a one-year `tc` cookie and redirects to the bare URL, so the token doesn't stay in the address bar or history.
3. After that, both the bare URL and the token link work. Without the cookie, every path is a 404.

`TAILCAM_TOKEN` lives in the homelab's `tailcam/.env` (sops-encrypted as `.env.enc`). It must be hex (`openssl rand -hex 32`) because it's pasted into a router regex. Changing it breaks every link and cookie already handed out. It also appears in traefik's access log on the first visit. `TAILCAM_DOMAIN` is set in the homelab's root `.env`.

## Endpoints

| Path | |
|---|---|
| `/` | grid of all cameras |
| `/stream/<n>` | MJPEG stream from camera `n` |
| `/audio/<n>` | raw PCM (s16le, mono, 24 kHz) from camera `n`'s mic; 404 if it has none |
| `/focus/<n>` | `GET` returns focus as JSON; `POST` sets it with form body `value=<n>` or `auto=0`/`auto=1`; 404 if the camera can't focus |
| `/healthz` | liveness check |

## Configuration

| Env var | Default | |
|---|---|---|
| `TAILCAM_PORT` | `8080` | port inside the container |
| `TAILCAM_MAX_CAMERAS` | `10` | number of device indexes probed, from 0 |
| `TAILCAM_JPEG_QUALITY` | `80` | 0–100, for cameras that can't send MJPEG (MJPEG frames pass through unchanged) |
| `TAILCAM_IDLE_GRACE` | `10` | seconds without viewers before a camera goes idle |
| `TAILCAM_HOSTS` | any | comma-separated `Host` values to answer; others get a 403, which blocks DNS rebinding. [`compose.yml`](compose.yml) sets it to the tailnet IP |

## Local dev

```sh
uv run --with-requirements requirements.txt -m tailcam
```

On Linux, your user needs to be in the `video` group to open the cameras, and audio needs a system `ffmpeg`. Each camera's mic is found through sysfs, as the sound card on the same USB device. Stop the container first, since only one process can stream from a camera.

On macOS, each mic is matched to its camera by name ("MacBook Air Camera" → "MacBook Air Microphone") and captured with a pip-bundled ffmpeg, so nothing needs installing. The first time you listen, macOS asks for microphone permission for your terminal. Camera controls (focus and LEDs) are Linux-only, so there's no focus slider on macOS.
