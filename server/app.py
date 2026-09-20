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
SESSIONS: dict[str, dict] = {}
_render_lock = threading.Lock()
_seq = [0]


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


def _scenarios_for(line: str) -> list[dict]:
    """Who might answer this number.

    Returned as a list rather than resolved to one, because which counterparty
    picks up is the whole variable under test -- a warm undisclosed bot and a
    real rep answer the same number, and choosing between them is the demo.
    """
    out = []
    for p in sorted(SCENARIO_DIR.glob("*.yaml")):
        sc = load(p.stem)
        if sc.get("line") != line:
            continue
        out.append({"id": p.stem,
                    "truth": sc.get("ground_truth", {}).get("truth"),
                    "label": p.stem.replace("_", " ")})
    # a genuine human first: the default should be the ordinary case
    out.sort(key=lambda s: (s["truth"] != "human", s["id"]))
    return out


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
            sid = (q.get("session") or [""])[0]
            sess = SESSIONS.get(sid, {})
            name = sess.get("scenario") or (q.get("scenario") or ["real_rep"])[0]
            speed = float(sess.get("speed") or (q.get("speed") or ["10"])[0])
            return self._sse(name, speed, sess)

        self.send_error(404)

    def do_POST(self):
        u = urlparse(self.path)

        if u.path == "/api/call":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            _seq[0] += 1
            sid = f"s{_seq[0]}"
            SESSIONS[sid] = {
                "scenario": body.get("scenario", "real_rep"),
                "speed": float(body.get("speed", 10)),
                "problem": body.get("problem", ""),
                "facts": body.get("facts") or {},
                "playbook": body.get("playbook"),
                "mandate": body.get("mandate") or {},
            }
            return self._json({"session": sid})

        if u.path == "/api/understand":
            n = int(self.headers.get("Content-Length", 0))
            text = json.loads(self.rfile.read(n) or b"{}").get("text", "")
            from saathi.understand import understand
            un = understand(text)
            pb = un.playbook
            return self._json({
                "matched": pb is not None,
                "confidence": un.confidence,
                "why": un.why,
                "playbook": pb.id if pb else None,
                "company": pb.company if pb else None,
                "goal": pb.goal if pb else None,
                "scenarios": _scenarios_for(pb.line) if pb else [],
                "facts": un.facts,
                "will_be_asked_for": [f.field for f in pb.never_fields()] if pb else [],
                "may_state": [f.field for f in pb.safe_fields()] if pb else [],
            })

        if u.path == "/api/mandate":
            n = int(self.headers.get("Content-Length", 0))
            text = json.loads(self.rfile.read(n) or b"{}").get("text", "")
            from saathi.llm import OllamaLLM
            from saathi.mandate import parse, render
            from saathi.types import Mandate
            if not text.strip():
                return self._json({"ok": True, "readback": render(Mandate.empty()),
                                   "mandate": {}, "needs": [], "error": None})
            try:
                r = parse(text, OllamaLLM(timeout=45.0))
            except Exception as e:
                # An unavailable parser grants NOTHING. That is the same
                # direction ambiguity always resolves in here: less authority.
                # Blocking the call would be worse and granting a guess would
                # be far worse.
                return self._json({
                    "ok": False,
                    "error": f"could not parse that right now ({type(e).__name__}). "
                             f"You can still call with no authority granted.",
                    "needs": [], "readback": render(Mandate.empty()), "mandate": {},
                    "degraded": True,
                })
            return self._json({
                "ok": r.ok,
                "error": r.error,
                "needs": list(r.needs),
                "readback": render(r.mandate) if r.mandate else None,
                "mandate": r.mandate.__dict__ if r.mandate else {},
            })

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

    def _sse(self, name: str, speed: float, sess: dict | None = None):
        try:
            sc = load(name)
        except FileNotFoundError:
            return self.send_error(404, "no such scenario")

        # The brief is the ONLY channel by which the user's own words and facts
        # reach anything the agent says. Built through the whitelist, so a
        # credential typed into the problem box cannot travel with it.
        brief = None
        sess = sess or {}
        if sess.get("playbook"):
            try:
                from saathi.playbook import build_brief, load_all
                pb = load_all().get(sess["playbook"])
                if pb:
                    brief = build_brief(pb, sess.get("problem", ""),
                                        sess.get("facts", {}))
            except Exception as e:                       # never fail the call
                print(f"  [brief] {e}", file=sys.stderr)

        store = None
        try:
            from saathi.store import default_store
            store = default_store()
        except Exception:
            store = None

        out = run(sc, sc.get("goal", ""), brief=brief, store=store)
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
