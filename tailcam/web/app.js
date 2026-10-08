// Audio is raw 16-bit mono PCM scheduled through Web Audio with a short jitter buffer.
// An <audio> element buffers seconds of a live stream and never catches up.
const RATE = Number(document.body.dataset.audioRate);
const LEAD = 0.15;      // seconds of audio queued ahead when (re)starting
const MAX_AHEAD = 0.5;  // drop chunks once we're this far ahead so delay can't creep up

const figs = [...document.querySelectorAll("figure[data-cam]")];
let selected = null;
let muted = true;
let ctx = null;
let session = null;

// iOS otherwise treats Web Audio as ambient and silences it with the ring/silent switch.
if (navigator.audioSession) navigator.audioSession.type = "playback";

function render() {
  for (const f of figs) f.classList.remove("sel", "muted", "noaudio");
  if (!selected) return;
  selected.classList.add("sel");
  if (selected.dataset.audio !== "1") selected.classList.add("noaudio");
  else if (muted) selected.classList.add("muted");
}

function stop() {
  if (!session) return;
  session.abort.abort();
  session.gain.disconnect();  // silences anything already scheduled
  session = null;
}

async function listen(cam) {
  // Created and resumed synchronously inside the tap, which is what autoplay policy requires.
  ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
  ctx.resume();
  const s = { abort: new AbortController(), gain: ctx.createGain() };
  s.gain.connect(ctx.destination);
  session = s;
  let t = 0;
  let carry = null;
  try {
    const res = await fetch(`/audio/${cam}`, { signal: s.abort.signal, cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const reader = res.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      let bytes = value;
      if (carry !== null) {
        bytes = new Uint8Array(value.length + 1);
        bytes[0] = carry;
        bytes.set(value, 1);
      }
      const n = bytes.length >> 1;
      carry = bytes.length & 1 ? bytes[bytes.length - 1] : null;
      if (n === 0) continue;
      const now = ctx.currentTime;
      if (t < now) t = now + LEAD;
      else if (t > now + MAX_AHEAD) continue;
      const pcm = new DataView(bytes.buffer, bytes.byteOffset, n * 2);
      const buf = ctx.createBuffer(1, n, RATE);
      const ch = buf.getChannelData(0);
      for (let i = 0; i < n; i++) ch[i] = pcm.getInt16(i * 2, true) / 32768;
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(s.gain);
      src.start(t);
      t += buf.duration;
    }
  } catch (e) {
    if (e.name === "AbortError") return;
  }
  // The server ends the stream if the mic stalls; show that as muted so a tap retries.
  if (session === s) {
    stop();
    muted = true;
    render();
  }
}

function apply() {
  stop();
  if (!muted && selected.dataset.audio === "1") listen(selected.dataset.cam);
  render();
}

for (const f of figs) {
  f.addEventListener("click", () => {
    if (f === selected) muted = !muted;
    else { selected = f; muted = false; }
    apply();
  });
}

// Focus: while a drag is in flight only the latest value is sent next, so a slow link
// (or traefik's rate limit) never builds a queue of stale positions.
// The selected feed's focus is polled from the server, so the slider follows the lens under
// autofocus (when it's disabled) and picks up changes made by other viewers.
const focusBars = new Map();
for (const f of figs) {
  const bar = f.querySelector(".focus");
  if (!bar) continue;
  bar.addEventListener("click", e => e.stopPropagation());  // don't toggle mute
  const slider = bar.querySelector("input[type=range]");
  const af = bar.querySelector(".af");
  const sync = () => { slider.disabled = !!af?.checked; };
  let busy = false;
  let pending = null;
  let touched = 0;  // last local change; a poll answered around then may predate it
  async function send(body) {
    touched = Date.now();
    if (busy) { pending = body; return; }
    busy = true;
    try {
      await fetch(`/focus/${f.dataset.cam}`, { method: "POST", body: new URLSearchParams(body) });
    } catch {}
    busy = false;
    if (pending) { const next = pending; pending = null; send(next); }
  }
  slider.addEventListener("input", () => send({ value: slider.value }));
  if (af) af.addEventListener("change", () => { sync(); send({ auto: af.checked ? "1" : "0" }); });
  sync();
  focusBars.set(f, { slider, af, sync, isSettled: () => !busy && Date.now() - touched > 1500 });
}

let polling = false;
setInterval(async () => {
  const bar = focusBars.get(selected);
  if (!bar || document.hidden || polling || !bar.isSettled()) return;
  polling = true;
  try {
    const res = await fetch(`/focus/${selected.dataset.cam}`, { cache: "no-store" });
    if (res.ok) {
      const focus = await res.json();
      if (bar.isSettled()) {  // the viewer may have moved the slider meanwhile
        bar.slider.value = focus.value;
        if (bar.af) bar.af.checked = focus.auto;
        bar.sync();
      }
    }
  } catch {}
  polling = false;
}, 1000);

selected = figs.find(f => f.dataset.cam === "0") || figs[0] || null;
render();

// Drop the video streams while the tab is hidden so the server can idle the cameras.
// Audio keeps playing, so a selected mic can still be listened to with the screen off.
const BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
function syncStreams() {
  for (const f of figs) {
    const img = f.querySelector("img");
    img.src = document.hidden ? BLANK : `/stream/${f.dataset.cam}?t=${Date.now()}`;
  }
}
document.addEventListener("visibilitychange", syncStreams);
if (document.hidden) syncStreams();  // opened in a background tab
