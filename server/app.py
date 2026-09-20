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
import os
import sys
import dataclasses
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saathi import voice
from saathi.frames import audio_id, build
from saathi.simulate import SCENARIO_DIR, load, run

AUDIO_DIR = Path(__file__).resolve().parent.parent / ".voice-cache" / "wav"
SITE_DIR = Path(__file__).resolve().parent.parent / "web" / "out"
CONTROLS: list[dict] = []
SESSIONS: dict[str, dict] = {}
_render_lock = threading.Lock()
_seq = [0]


def _mandate_from(d) -> "Mandate":
    """Rebuild a Mandate from a request body, through a field whitelist.

    The whitelist stops a client inventing attributes; it does NOT stop the
    client choosing generous values, because on this single-user local build
    the client IS the user and the mandate is their own grant to make. On a
    shared deployment that stops being true and this needs a server-side token
    issued by /api/mandate -- see AGENTS.md.
    """
    from saathi.types import Mandate
    if not isinstance(d, dict):
        return Mandate.empty()
    allowed = {f.name for f in dataclasses.fields(Mandate)}
    try:
        return Mandate(**{k: v for k, v in d.items() if k in allowed})
    except TypeError:
        return Mandate.empty()


def _speech_ms(frames: list[dict]) -> int | None:
    """How much of the call was actually speech, from the rendered clips.

    Returns None when no audio exists, so the summary says nothing about hold
    rather than counting speech as waiting.
    """
    total, seen = 0.0, 0
    for f in frames:
        aid = f.get("audio")
        if not aid:
            continue
        p = AUDIO_DIR / f"{aid}.wav"
        if not p.exists():
            continue
        try:
            with wave.open(str(p)) as w:
                total += w.getnframes() / w.getframerate()
                seen += 1
        except Exception:
            continue
    return int(total * 1000) if seen else None


# The live transport, created lazily and only if credentials exist. Absent
# credentials this stays None and the webhook answers with empty TwiML, which
# is what an unconfigured box should do: nothing, loudly enough to notice.
_TRANSPORT = [None]


def _twilio_webhook(path: str, form: dict) -> str:
    from saathi.transport import twiml as T
    from saathi.transport.twilio_transport import TwilioConfig, TwilioTransport
    if _TRANSPORT[0] is None:
        if not TwilioConfig.available():
            print(f"  [twilio] {path} ignored: not configured", file=sys.stderr)
            return T.document()
        _TRANSPORT[0] = TwilioTransport(TwilioConfig.from_env())
    try:
        return _TRANSPORT[0].on_webhook(path, form)
    except Exception as e:                      # never 500 at a carrier
        print(f"  [twilio] {path}: {e}", file=sys.stderr)
        return T.document()


def annotate_audio_ms(frames: list[dict]) -> list[dict]:
    """Stamp each audio-bearing frame with how long its clip ACTUALLY runs.

    Read from the rendered wav rather than the scenario's speak_ms, because the
    yaml figures are estimates and Polly is the ground truth. The pacing loop
    uses this to stop a clip being cut off by the frame after it.
    """
    for f in frames:
        aid = f.get("audio")
        if not aid:
            continue
        p = AUDIO_DIR / f"{aid}.wav"
        if not p.exists():
            # DROP the reference rather than advertise a clip we cannot serve.
            # Clip ids are content-hashed, so a user-typed problem produces a
            # disclosure line no cache can already contain, and the deployed
            # host has no Polly credentials to render one (instance profiles are
            # denied on this account). Leaving the id in place made the browser
            # fetch it and log `GET /api/audio/....wav 404`, which reads as a
            # broken deployment rather than as a line with no recording.
            f.pop("audio", None)
            f["audio_missing"] = True
            continue
        try:
            with wave.open(str(p)) as w:
                f["audio_ms"] = int(w.getnframes() / w.getframerate() * 1000)
        except Exception:
            f.pop("audio", None)
            f["audio_missing"] = True
    return frames


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
    out = _by_line(line)
    if out:
        return out
    # An unknown company has no scenarios of its own. Offering every
    # counterparty we can simulate is more useful than an empty list, and the
    # UI says which line each one belongs to.
    return _by_line(None)


def _by_line(line: str | None) -> list[dict]:
    out = []
    for p in sorted(SCENARIO_DIR.glob("*.yaml")):
        sc = load(p.stem)
        if line is not None and sc.get("line") != line:
            continue
        # A plain name from the scenario itself. The filename was being shown
        # raw -- "generic rep", "bot settles refund" -- which is shorthand for
        # whoever wrote the file, not something a caller would ever say.
        truth = sc.get("ground_truth", {}).get("truth")
        out.append({"id": p.stem,
                    "truth": truth,
                    "who": "a person" if truth == "human" else "a machine",
                    "label": sc.get("display") or p.stem.replace("_", " ")})
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

        # The built UI, served from the SAME ORIGIN as the API. That is the
        # whole point: an HTTPS page cannot call an HTTP API, and a Cloudflare
        # quick tunnel buffers SSE no matter what headers you send (measured:
        # every frame arriving in one instant at 17.5s, so the call view sat on
        # "Dialling" and then jumped to the summary). One origin over plain
        # HTTP has neither problem, and the stream arrives frame by frame.
        if SITE_DIR.is_dir():
            rel = u.path.lstrip("/") or "index.html"
            if not u.path.startswith(("/api/", "/twilio/")):
                cand = (SITE_DIR / rel).resolve()
                if not str(cand).startswith(str(SITE_DIR.resolve())):
                    return self.send_error(403)      # no path traversal
                if cand.is_dir():
                    cand = cand / "index.html"
                if not cand.exists() and not cand.suffix:
                    cand = cand.with_suffix(".html")
                if cand.exists() and cand.is_file():
                    return self._static(cand)

        if u.path in ("/", "/health"):
            # A root page, because the bare 404 that used to live here looked
            # exactly like a broken deployment. It is not an API; it exists so a
            # human who opens the engine URL can see what is running.
            from saathi.transport.twilio_transport import TwilioConfig
            live = TwilioConfig.available()
            body = (
                "<!doctype html><meta charset=utf-8><title>Saathi engine</title>"
                "<style>body{font:14px system-ui;margin:40px;max-width:640px;"
                "line-height:1.6}code{background:#f4f4f5;padding:2px 6px;"
                "border-radius:4px}</style>"
                "<h1>Saathi engine</h1><p>Running. This is the API, not the app.</p>"
                f"<p>Live telephony: <strong>{'yes' if live else 'no'}</strong></p>"
                "<p>Endpoints:</p><ul>"
                "<li><code>/api/scenarios</code></li>"
                "<li><code>/api/telephony</code></li>"
                "<li><code>/api/events?session=...</code></li>"
                "<li><code>/twilio/answer</code>, <code>/twilio/status</code> (POST)</li>"
                "</ul>"
            ).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if u.path == "/api/telephony":
            # The UI must be able to say truthfully whether a number will be
            # dialled or simulated. Guessing here is how a demo ends up implying
            # it placed a call it never placed.
            from saathi.transport.twilio_transport import TwilioConfig
            live = TwilioConfig.available()
            return self._json({
                "live": live,
                "from_number": (TwilioConfig.from_env().from_number if live else None),
                "why": ("ready to dial" if live else
                        "no Twilio credentials, so a number here is recorded but "
                        "the call runs against the simulated line"),
            })

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

        # Twilio posts here at every decision point on a live call. Kept on the
        # stdlib server rather than pulling in a framework: the whole surface is
        # form-encoded in and TwiML out.
        #
        # NOTE: Twilio cannot reach localhost. SAATHI_PUBLIC_URL has to be a
        # tunnel (ngrok, Cloudflare) or a deployed box, or none of this fires.
        if u.path.startswith("/twilio/"):
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
            form = {k: v[0] for k, v in parse_qs(raw).items()}
            body = _twilio_webhook(u.path, form).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if u.path == "/api/testcall":
            # The smallest honest thing a number field can do today: place a
            # real call, announce what Saathi is, and hang up. It does NOT run
            # the detector -- driving a live call through run() is the Phase 0
            # transport refactor and is not done. Saying "test call" rather than
            # "call" is the difference between a demo and a lie.
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            to = (body.get("to_number") or "").strip()
            from saathi.transport.twilio_transport import (
                TwilioConfig, TwilioNotConfigured, TwilioTransport)
            if not to:
                return self._json({"placed": False, "why": "no number given"})
            try:
                cfg = TwilioConfig.from_env()
            except TwilioNotConfigured as e:
                return self._json({"placed": False, "why": str(e)})
            try:
                t = TwilioTransport(cfg)
                sid = t.dial(to, session_id="testcall")
                return self._json({"placed": True, "sid": sid,
                                   "why": f"dialling {to} from {cfg.from_number}"})
            except Exception as e:
                return self._json({"placed": False, "why": f"{type(e).__name__}: {e}"})

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
                "to_number": (body.get("to_number") or "").strip(),
                "company": (body.get("company") or "").strip(),
            }
            return self._json({"session": sid})

        if u.path == "/api/understand":
            n = int(self.headers.get("Content-Length", 0))
            _b = json.loads(self.rfile.read(n) or b"{}")
            text = _b.get("text", "")
            from saathi.understand import understand
            un = understand(text, company=_b.get("company"))
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

    _MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
             ".css": "text/css", ".svg": "image/svg+xml", ".ico": "image/x-icon",
             ".json": "application/json", ".txt": "text/plain; charset=utf-8",
             ".woff2": "font/woff2", ".png": "image/png"}

    def _static(self, path: Path):
        raw = path.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type",
                         self._MIME.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(raw)))
        # Hashed asset names, so they can be cached hard; the HTML cannot.
        self.send_header("Cache-Control",
                         "public, max-age=31536000" if "/_next/" in path.as_posix()
                         else "no-cache")
        self.end_headers()
        self.wfile.write(raw)

    def _chunk(self, payload: bytes) -> None:
        """One HTTP chunk, flushed immediately.

        An empty payload writes the terminating zero-length chunk that ends a
        chunked body.
        """
        self.wfile.write(f"{len(payload):X}\r\n".encode("ascii"))
        if payload:
            self.wfile.write(payload)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

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
                    # The generic playbook is company-agnostic by design, so the
                    # name the user typed is what makes the brief say who is
                    # being called. Without this the call still ran, but every
                    # unrecognised company was "the company".
                    typed = sess.get("company")
                    if typed:
                        pb = dataclasses.replace(pb, company=typed)
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

        out = run(sc, sc.get("goal", ""), brief=brief, store=store,
                  mandate=_mandate_from(sess.get("mandate")))
        frames = build(out, sc, speed=speed)
        stats = prerender(frames)
        # Rebuilt once the clips exist, so the summary can report hold time from
        # measured audio instead of guessing. build() is pure and cheap; run()
        # is not re-run, so the timeline is identical.
        frames = annotate_audio_ms(
            build(out, sc, speed=speed, speech_ms=_speech_ms(frames)))
        print(f"  [voice] {name}: {stats}", file=sys.stderr)

        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        # no-transform tells intermediaries not to compress. A proxy that
        # compresses has to buffer to do it, and buffering an SSE stream
        # defeats the entire point of one.
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Content-Encoding", "identity")
        # CHUNKED, and this is load-bearing rather than tidy. Without it the
        # body is delimited only by the connection closing, so every
        # intermediary has to buffer the WHOLE stream to learn its length.
        # Measured through a Cloudflare tunnel: first frame arrived at 20.5s
        # and all twelve landed in the same instant, so the call view sat on
        # "Dialling 00:00" for the entire call and then jumped straight to the
        # summary. Locally, where nothing proxies, the first frame took 0.05s
        # -- which is exactly why this was invisible in development.
        self.send_header("Transfer-Encoding", "chunked")
        # close, not keep-alive: the stream is finite and a client that cannot
        # tell "finished" from "quiet" will sit there forever
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        t0 = time.monotonic()
        # SPEECH PLAYS AT FULL LENGTH; ONLY WAITING IS COMPRESSED. The scenario's
        # speak_ms are estimates and real Polly runs longer, so pacing purely on
        # the compressed call clock hands a clip less time than it needs and the
        # next frame cuts it off mid-word. Measured on real_rep: the disclosure
        # got 4.0s of budget for 13.1s of audio, and two rep lines got 0.0s and
        # 5.6s for 3.1s and 7.1s.
        #
        # `floor` is the earliest the NEXT frame may be sent: after a frame that
        # carries audio, nothing follows until that audio has actually finished.
        # Hold and menus still compress, which is the point of the speed control
        # -- twenty minutes of queue in a few seconds, every spoken word intact.
        floor = 0.0
        try:
            for f in frames:
                due = max(t0 + (f["call_ms"] / 1000.0) / speed, floor)
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, 20.0))
                payload = json.dumps(f).encode()
                self._chunk(b"data: " + payload + b"\n\n")
                if f.get("audio_ms"):
                    floor = time.monotonic() + f["audio_ms"] / 1000.0
            self._chunk(b"event: done\ndata: {}\n\n")
            self._chunk(b"")          # terminating zero-length chunk
        except (BrokenPipeError, ConnectionResetError):
            pass                                     # viewer closed the tab


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    # Loopback by DEFAULT, because a dev box should not publish itself to the
    # network by accident. On a deployed host nothing outside the machine can
    # reach a loopback socket, so SAATHI_BIND=0.0.0.0 is required there -- the
    # service comes up, the port refuses every external connection, and it
    # looks exactly like a crashed process.
    host = os.environ.get("SAATHI_BIND", "127.0.0.1").strip() or "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"saathi stream on http://{host}:{port}")
    print(f"  scenarios  http://127.0.0.1:{port}/api/scenarios")
    print(f"  stream     http://127.0.0.1:{port}/api/events?scenario=real_rep&speed=40")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
