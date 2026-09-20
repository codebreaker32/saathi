"""SSE stream + per-utterance audio, on the stdlib.

Server-Sent Events rather than a WebSocket: the call is a one-way stream of
frames and the controls are four buttons that fire a handful of times. SSE is
that shape exactly, needs no dependency, reconnects for free on a flaky mobile
connection, and can be inspected with `curl -N`.

Pacing lives here rather than in the engine. The whole timeline is computed up
front, so playback speed is a property of how frames are released -- which is
why the UI can show a true call clock next to an honest speed badge instead of
pretending an eleven-minute hold took eleven minutes.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saathi import voice
from saathi.frames import audio_id, build
from saathi.simulate import SCENARIO_DIR, load, run

AUDIO_DIR = Path(__file__).resolve().parent.parent / ".voice-cache" / "wav"
CONTROLS: list[dict] = []
_render_lock = threading.Lock()


def prerender(frames: list[dict], profile: str | None = None) -> dict:
    """Synthesise every utterance once, up front. Cached across runs."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    made = skipped = failed = 0
    for f in frames:
        aid, text, who = f.get("audio"), f.get("text"), f.get("speaker")
        if not aid or not text or not who:
            continue
        dest = AUDIO_DIR / f"{aid}.wav"
        if dest.exists():
            skipped += 1
            continue
        try:
            with _render_lock:
                voice.write_wav(dest, [voice.synthesize(text, who, profile=profile)])
            made += 1
        except Exception as e:                      # keep the call playable
            print(f"  [voice] {who}: {e}", file=sys.stderr)
            failed += 1
    return {"made": made, "cached": skipped, "failed": failed}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):                      # quiet
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path == "/api/scenarios":
            items = []
            for p in sorted(SCENARIO_DIR.glob("*.yaml")):
                sc = load(p.stem)
                items.append({
                    "id": p.stem, "org": sc.get("org"), "goal": sc.get("goal"),
                    "truth": sc.get("ground_truth", {}).get("truth"),
                })
            return self._json(items)

        if u.path.startswith("/api/audio/"):
            return self._wav(AUDIO_DIR / Path(u.path).name)

        if u.path == "/api/events":
            name = (q.get("scenario") or ["real_rep"])[0]
            speed = float((q.get("speed") or ["40"])[0])
            return self._sse(name, speed)

        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/control":
            return self.send_error(404)
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        body["at"] = time.time()
        CONTROLS.append(body)
        print(f"  [control] {body.get('action')}", file=sys.stderr)
        return self._json({"ok": True, "action": body.get("action")})

    # -- responses --------------------------------------------------------- #

    def _json(self, obj):
        raw = json.dumps(obj).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _wav(self, path: Path):
        if not path.exists() or path.suffix != ".wav":
            return self.send_error(404)
        raw = path.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "public, max-age=31536000")
        self.end_headers()
        self.wfile.write(raw)

    def _sse(self, name: str, speed: float):
        try:
            sc = load(name)
        except FileNotFoundError:
            return self.send_error(404, "no such scenario")

        out = run(sc, sc.get("goal", ""))
        frames = build(out, sc, speed=speed)
        stats = prerender(frames)
        print(f"  [voice] {name}: {stats}", file=sys.stderr)

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        # close, not keep-alive: the stream is finite and a client that cannot
        # tell "finished" from "quiet" will sit there forever
        self.send_header("Connection", "close")
        self.close_connection = True
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        t0 = time.monotonic()
        try:
            for f in frames:
                due = t0 + (f["call_ms"] / 1000.0) / speed
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, 20.0))
                payload = json.dumps(f).encode()
                self.wfile.write(b"data: " + payload + b"\n\n")
                self.wfile.flush()
            self.wfile.write(b"event: done\ndata: {}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                                     # viewer closed the tab


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"saathi stream on http://127.0.0.1:{port}")
    print(f"  scenarios  http://127.0.0.1:{port}/api/scenarios")
    print(f"  stream     http://127.0.0.1:{port}/api/events?scenario=real_rep&speed=40")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
