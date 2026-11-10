# tailcam

A small MJPEG webcam server for watching the dogs, either over Tailscale or publicly behind a secret link. It finds every attached camera at startup and shows them all on one page.

Cameras stay on while the container runs, because a webcam clicks and flashes its LED when it switches on, and that bothers the dogs. Frames are only sent while someone is watching. Ten seconds (`TAILCAM_IDLE_GRACE`) after the last viewer leaves or hides the tab, a camera goes idle and drops its frames. Audio keeps playing while the tab is hidden.

At startup, tailcam switches off each camera's status LED, or sets it back to the camera's default with `TAILCAM_LED_OFF=0`. Newer Logitech webcams, such as the C920 and CrystalCam, are handled through their vendor extension unit. Any other camera whose driver exposes an LED control has that control set to off. The startup log lists each camera's controls, so `docker logs tailcam` shows what yours supports.

## Using the page

The page loads with the first feed selected and muted, because browsers block autoplay. Tap the selected feed to toggle its mic, or tap another feed to switch to it. A green outline means the selected feed has audio, and red means its camera has no mic.

On a keyboard, ← and → move the selected feed earlier or later on the page. The order is shared by every viewer and lasts until the container restarts. Camera numbers can change across reboots, so set it again after one.

If the selected camera can focus manually, a slider appears under its feed. While "auto" is ticked, the slider is disabled and follows the autofocus. Untick it to focus by hand. The setting lasts until the container restarts.

The buttons under the feeds are **Refresh**, which reloads the page, and **Restart**, which restarts tailcam for every viewer. A restart looks for cameras again, so use it when a camera is missing, e.g. one plugged in after startup or one that was slow to wake. The page reloads itself once tailcam is back. tailcam just exits and relies on whatever runs it to start it again: Docker's `restart: unless-stopped` in [`compose.yml`](compose.yml), or `Restart=always` in [`tailcam.service`](rpi/tailcam.service). Run by hand, it simply stops. Anyone who can open the page can restart it, including through the public token link.

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
| `/stream/<n>` | MJPEG stream from camera `n`; 503 while the door sensor's circuit is open |
| `/audio/<n>` | raw PCM (s16le, mono, 24 kHz) from camera `n`'s mic; 404 if it has none, 503 while the door sensor's circuit is open |
| `/focus/<n>` | `GET` returns focus as JSON; `POST` sets it with form body `value=<n>` or `auto=0`/`auto=1`; 404 if the camera can't focus |
| `/order` | `POST` sets the page order with form body `order=2,0,1` (every camera, once each) |
| `/restart` | `POST` (empty body) exits so the supervisor restarts tailcam, which looks for cameras again |
| `/status` | JSON `{"door": "open"}`, `"closed"`, or `null` without a door sensor |
| `/healthz` | liveness check |

## Configuration

| Env var | Default | |
|---|---|---|
| `TAILCAM_PORT` | `8080` | port inside the container |
| `TAILCAM_MAX_CAMERAS` | `10` | number of device indexes probed, from 0 |
| `TAILCAM_JPEG_QUALITY` | `80` | 0–100, for cameras that can't send MJPEG (MJPEG frames pass through unchanged) |
| `TAILCAM_IDLE_GRACE` | `10` | seconds without viewers before a camera goes idle |
| `TAILCAM_FIRST_FRAME_TIMEOUT` | `5` | seconds a camera gets at startup to send its first frame before it's left out, for cameras slow to wake |
| `TAILCAM_HOSTS` | any | comma-separated `Host` values to answer; others get a 403, which blocks DNS rebinding. [`compose.yml`](compose.yml) sets it to the tailnet IP |
| `TAILCAM_LED_OFF` | `1` | `1` turns status LEDs off; `0` (or `false`, `off`, `no`) sets them back to the camera's default (usually lit while streaming) |
| `TAILCAM_GATE_PIN` | unset | a door sensor's BCM GPIO, e.g. `17`; feeds and mics are served only while its circuit is closed. See "Door sensor" below |
| `TAILCAM_GATE_SETTLE` | `2` | seconds the door sensor's pin must hold steady before feeds come back |

[`run.sh`](run.sh) loads these from a `.env` beside it, and [`compose.yml`](compose.yml) passes `TAILCAM_LED_OFF` through from its `.env`.

## Door sensor (Raspberry Pi)

On a Pi, tailcam can run without Docker as [`rpi/tailcam.service`](rpi/tailcam.service), which runs `run.sh`. It needs a 64-bit OS, because OpenCV has no 32-bit ARM build on PyPI, and `ffmpeg` for audio (`sudo apt install ffmpeg`). Run this way, it answers on every network interface at port 8080, so anyone on the same network can watch without joining the tailnet. Set `TAILCAM_HOSTS` in `.env` to limit which addresses it answers to.

With `TAILCAM_GATE_PIN` set, tailcam serves the feeds and mics only while a door sensor's circuit is closed. It works like a shutter over the lenses: tailcam and the cameras keep running, but nothing is sent while the door is open. Any stream in progress ends as soon as the circuit opens, and the page greys out and says "Door open". Feeds come back once the pin has held closed for `TAILCAM_GATE_SETTLE` seconds, and the page reconnects them on its own. If the pin can't be opened or read, tailcam keeps the feeds off and logs why. The code is in [`tailcam/gate.py`](tailcam/gate.py). It sleeps until the pin changes, so it uses no CPU.

The gate used to stop tailcam while the door was open and start it again when the door closed. Opening the cameras on every door close sometimes left the IR Arducam sending no frames, or stuck until it lost power, so tailcam now opens them once, at boot.

Wire the sensor between a GPIO (BCM 17, physical pin 11, by default) and ground. The gate turns on the Pi's internal pull-up, so the pin reads closed while the sensor pulls it low. That suits a reed switch or a bare open-collector hall sensor such as the A3144, and neither needs a resistor. Power a hall sensor from 5 V if its datasheet requires it (the A3144 needs 4.5 V), since its output only ever sinks to ground. A breakout board with its own pull-up resistor or LED is different: it pulls the output up to its supply, so power it from 3.3 V, never 5 V.

Install the unit from the checkout as the user tailcam should run as, not with `sudo`. [`rpi/install.sh`](rpi/install.sh) fills in the checkout path, your user and `uv`'s location, and asks for `sudo` itself. tailcam then runs all the time, from boot:

```sh
rpi/install.sh gate     # serve the feeds only while the circuit is closed
rpi/install.sh always   # serve them all the time, without the sensor
```

In `gate` mode it sets `TAILCAM_GATE_PIN=17` and `TAILCAM_GATE_SETTLE=2` in a drop-in, and adds tailcam to the `gpio` group to read the pin. Change them with `sudo systemctl edit tailcam.service`, which keeps them across reinstalls. If an older install ran the separate `tailcam-gate` service, the script stops and removes it.

It also installs [`rpi/50-tailcam-cameras.rules`](rpi/50-tailcam-cameras.rules), a udev rule that keeps every USB webcam out of USB autosuspend. Otherwise the kernel can suspend a camera before tailcam opens it at boot. tailcam looks for cameras only at startup and gives each one `TAILCAM_FIRST_FRAME_TIMEOUT` seconds to send a frame, but a camera that misses that window stays missing until the next restart.

When a camera opens but sends no frames, tailcam starts its stream directly and logs the error, which OpenCV hides. `Protocol error` (EPROTO) means the camera rejected the start. The IR Arducam gets stuck this way, and only losing power fixes it: unplug it or reboot the Pi, since restarting tailcam or resetting the USB device doesn't help. `Device or resource busy` means another process is streaming from it. While a camera won't stream, tailcam keeps reopening it, slowing down to once a minute, and logs again only if the reason changes. It logs `streaming again` when frames come back, so a replugged camera returns without a restart. tailcam also logs when a mic starts and stops, and the CPU temperature at each start and whenever a camera stops streaming, so `journalctl -u tailcam` shows what the cameras, mics and Pi were doing before a failure. The Arducam got stuck when the Pi ran hot enough to throttle without its fan.

[`run.sh`](run.sh) also saves each run's output in `logs/` in the checkout, as `tailcam-<start time>.log`, and keeps the newest 10. Set `TAILCAM_LOG_KEEP` in `.env` to keep a different number, or `0` to save none. The journal gets the same lines, but the files are written as tailcam runs, so they keep a run that ended when the Pi lost power, e.g. to unstick the Arducam. A journal kept only in memory loses that. The files are named by the Pi's clock, which can be wrong until it syncs after boot, so `ls -t logs` lists them in the order they were last written.

Rerun it to switch modes, or after moving the checkout or reinstalling `uv`. After a `git pull`, `sudo systemctl restart tailcam` is enough. The script stops if the OS is 32-bit, the checkout path has unusual characters, or `gate` mode finds no `gpio` group, and warns if `ffmpeg` is missing.

## Local dev

```sh
uv run --with-requirements requirements.txt -m tailcam
```

On Linux, your user needs to be in the `video` group to open the cameras, and audio needs a system `ffmpeg`. Each camera's mic is found through sysfs, as the sound card on the same USB device. Stop the container first, since only one process can stream from a camera.

On macOS, each mic is matched to its camera by name ("MacBook Air Camera" → "MacBook Air Microphone") and captured with a pip-bundled ffmpeg, so nothing needs installing. The first time you listen, macOS asks for microphone permission for your terminal. Camera controls (focus and LEDs) are Linux-only, so there's no focus slider on macOS.
