"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8787";

/* The detector's families, said the way a person would say them. Nobody
   outside this codebase knows what CONTINGENCY means. */
const CHECKS = [
  { id: "SYNTHESIS",   name: "Real voice",     asks: "Does the voice sound recorded by a person, or generated?" },
  { id: "IDENTITY",    name: "Same person",    asks: "Is this still whoever was speaking a moment ago?" },
  { id: "REPETITION",  name: "Said it before", asks: "Have we heard this exact sentence on an earlier call?" },
  { id: "CONTINGENCY", name: "Replying to us", asks: "Does their answer depend on what Saathi actually said?" },
  { id: "DUPLEX",      name: "Takes turns",    asks: "Do they stop when interrupted, the way people do?" },
];
const SCRIPTED = new Set(["SYNTHESIS", "IDENTITY"]);

const VERDICT: Record<string, { say: string; cls: string }> = {
  FETCH:     { say: "That's a person", cls: "done" },
  MACHINE:   { say: "Still a machine", cls: "held" },
  UNDECIDED: { say: "Not sure yet",    cls: "" },
  LISTENING: { say: "Listening",       cls: "held" },
};

const VOICE: Record<string, string> = {
  menu: "Recorded menu", queue: "Recorded message",
  bot: "Ava", rep: "Agent", saathi: "Saathi",
};

const EXPLAIN: Record<string, string> = {
  Dialling: "Ringing the company now.",
  "Working through the menu": "Listening to the whole menu before pressing anything — the option you want is often last.",
  "On hold": "Waiting. You can close this tab; Saathi will tell you when a person picks up.",
  "Someone is speaking": "Someone answered. Saathi is working out whether it's a person or a recording.",
  "Fetching you": "A person is on the line. This is your call now.",
  "Handed to you": "They asked to verify your identity. Only you can answer that.",
  "Back on hold": "You said that wasn't a person. Saathi is waiting again and won't interrupt you twice.",
};

const FOLDERS = [
  { id: "all",       name: "All calls",  match: () => true },
  { id: "live",      name: "Happening now", match: (c: any) => c.state === "live" || c.state === "queued" },
  { id: "needs_you", name: "Needs you",  match: (c: any) => c.state === "needs_you" },
  { id: "done",      name: "Reached a person", match: (c: any) => c.state === "done" },
  { id: "held",      name: "Held (was a machine)", match: (c: any) => c.state === "held" },
];

type Frame = Record<string, any>;
const clock = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};
const ago = (t?: number) => {
  if (!t) return "";
  const m = Math.floor((Date.now() / 1000 - t) / 60);
  return m < 1 ? "just now" : m < 60 ? `${m}m ago` : `${Math.floor(m / 60)}h ago`;
};

export default function Page() {
  const [folder, setFolder] = useState("all");
  const [calls, setCalls] = useState<any[]>([]);
  const [open, setOpen] = useState<string | null>(null);   // session id, or null = compose

  // compose
  const [problem, setProblem] = useState(
    "My Zomato order was two hours late and they charged me full price, order 4471, rs 499");
  const [understood, setUnderstood] = useState<any>(null);
  const [scenario, setScenario] = useState("");
  const [mandateText, setMandateText] = useState("");
  const [mandate, setMandate] = useState<any>(null);
  const [speed, setSpeed] = useState(10);
  const [busy, setBusy] = useState(false);

  // live call
  const [frames, setFrames] = useState<Frame[]>([]);
  const [callMs, setCallMs] = useState(0);
  const [det, setDet] = useState<Frame | null>(null);
  const [phase, setPhase] = useState("Dialling");
  const [summoned, setSummoned] = useState(false);
  const [mode, setMode] = useState<string | null>(null);
  const [ended, setEnded] = useState<Frame | null>(null);
  const [demoted, setDemoted] = useState(false);
  const [details, setDetails] = useState(false);

  const es = useRef<EventSource | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const muted = useRef(false);
  const thread = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(() => {
    fetch(`${API}/api/calls`).then(r => r.json()).then(setCalls).catch(() => {});
  }, []);
  useEffect(() => { refresh(); const t = setInterval(refresh, 4000); return () => clearInterval(t); }, [refresh]);
  useEffect(() => { thread.current?.scrollTo({ top: 1e9, behavior: "smooth" }); }, [frames]);
  useEffect(() => () => { es.current?.close(); audio.current?.pause(); }, []);

  const say = useCallback((id: string) => {
    if (muted.current) return;
    audio.current?.pause();
    const a = new Audio(`${API}/api/audio/${id}.wav`);
    audio.current = a; a.play().catch(() => {});
  }, []);

  async function understand() {
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/understand`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: problem }),
      }).then(x => x.json());
      setUnderstood(r);
      if (r.scenarios?.length) setScenario(r.scenarios[0].id);
    } finally { setBusy(false); }
  }

  async function checkMandate() {
    setBusy(true);
    try {
      setMandate(await fetch(`${API}/api/mandate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: mandateText }),
      }).then(x => x.json()));
    } finally { setBusy(false); }
  }

  async function placeCall() {
    es.current?.close();
    setFrames([]); setDet(null); setEnded(null); setSummoned(false);
    setMode(null); setDemoted(false); setCallMs(0); setPhase("Dialling");
    muted.current = false;

    const { session } = await fetch(`${API}/api/call`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario, speed, playbook: understood?.playbook,
        company: understood?.company, problem,
        facts: { registered_phone: "98765 41182", ...(understood?.facts ?? {}) },
        mandate: mandate?.mandate ?? {},
      }),
    }).then(r => r.json()).catch(() => ({ session: "" }));

    setOpen(session); refresh();
    const src = new EventSource(`${API}/api/events?session=${session}`);
    es.current = src;
    src.onmessage = e => {
      const f: Frame = JSON.parse(e.data);
      setCallMs(f.call_ms); setFrames(p => [...p, f]);
      if (f.type === "detector") setDet(f);
      if (f.type === "menu" || f.type === "dtmf") setPhase("Working through the menu");
      if (f.type === "hold" || f.type === "announcement") setPhase("On hold");
      if (f.type === "utterance" || f.type === "probe") setPhase("Someone is speaking");
      if (f.type === "summon") { setSummoned(true); setPhase("Fetching you"); }
      if (f.type === "handoff") { setSummoned(true); setPhase("Handed to you"); }
      if (f.type === "ended") { setEnded(f); src.close(); refresh(); }
      if (f.audio) say(f.audio);
    };
    src.onerror = () => src.close();
  }

  function control(action: string) {
    fetch(`${API}/api/control`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, scenario, call_ms: callMs }),
    }).catch(() => {});
    if (action === "not_a_person") {
      audio.current?.pause(); muted.current = true;
      setDemoted(true); setSummoned(false); setMode(null); setPhase("Back on hold");
    } else {
      setMode(action);
      muted.current = action === "take_over";
      if (muted.current) audio.current?.pause();
    }
  }

  const shown = calls.filter(FOLDERS.find(f => f.id === folder)!.match);
  const voted: string[] = det?.voted ?? [];
  const verdict = VERDICT[det?.decision ?? "LISTENING"] ?? VERDICT.LISTENING;

  return (
    <div className="flex h-screen overflow-hidden">
      {/* ---- rail ---- */}
      <nav className="rail w-[210px] shrink-0 flex flex-col">
        <div className="px-4 py-4">
          <div className="text-white font-bold text-[15px]">Saathi</div>
          <div className="text-[11px] opacity-70">your call companion</div>
        </div>
        <button className="btn primary mx-4 mb-3" onClick={() => { setOpen(null); setEnded(null); }}>
          New call
        </button>
        <div className="mt-1">
          {FOLDERS.map(f => (
            <button key={f.id} className={`folder ${folder === f.id ? "on" : ""}`}
              onClick={() => setFolder(f.id)}>
              {f.name}
              <span className="n">{calls.filter(f.match).length}</span>
            </button>
          ))}
        </div>
        <div className="mt-auto px-4 py-3 text-[11px]" style={{ opacity: .55 }}>
          Practice line. No real company is dialled.
        </div>
      </nav>

      {/* ---- list ---- */}
      <aside className="w-[300px] shrink-0 border-r overflow-y-auto"
        style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
        <div className="px-4 py-3 border-b text-[12px] font-bold uppercase tracking-wide"
          style={{ borderColor: "var(--line)", color: "var(--ink-faint)" }}>
          {FOLDERS.find(f => f.id === folder)!.name}
        </div>
        {shown.length === 0 && (
          <div className="p-4 text-[13px]" style={{ color: "var(--ink-faint)" }}>
            Nothing here yet. Start one with <strong>New call</strong>.
          </div>
        )}
        {shown.map(c => (
          <button key={c.id} className={`row ${open === c.id ? "on" : ""}`}
            onClick={() => setOpen(c.id)}>
            <div className="flex items-center gap-2">
              <span className="font-bold text-[13px]">{c.company}</span>
              <span className="meta ml-auto">{ago(c.started_at)}</span>
            </div>
            <div className="text-[13px] mt-0.5 truncate" style={{ color: "var(--ink-soft)" }}>
              {c.problem || "—"}
            </div>
            <div className="mt-1.5"><StatePill c={c} /></div>
          </button>
        ))}
      </aside>

      {/* ---- main ---- */}
      <main className="flex-1 min-w-0 overflow-y-auto">
        {open === null ? (
          <Compose {...{ problem, setProblem, understood, understand, busy, scenario,
            setScenario, mandateText, setMandateText, mandate, checkMandate, speed,
            setSpeed, placeCall }} />
        ) : (
          <div className="flex h-full">
            <section className="flex-1 min-w-0 flex flex-col">
              <header className="px-5 py-3 border-b flex items-center gap-3 flex-wrap"
                style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
                <div className="min-w-0">
                  <div className="font-bold text-[15px]">{understood?.company ?? "Call"}</div>
                  <div className="text-[12px]" style={{ color: "var(--ink-soft)" }}>{phase}</div>
                </div>
                <span className="mono text-[15px] ml-auto">{clock(callMs)}</span>
                <span className="pill">{speed}× faster</span>
                <span className={`pill ${verdict.cls}`}><i className="dot" />{verdict.say}</span>
              </header>

              <div className="px-5 py-2.5 text-[13px] border-b"
                style={{ borderColor: "var(--line)", color: "var(--ink-soft)", background: "#fbfcfd" }}>
                {EXPLAIN[phase] ?? ""}
              </div>

              <div ref={thread} className="flex-1 overflow-y-auto"
                style={{ background: "var(--panel)" }}>
                {frames.map(f => <Thread key={f.seq} f={f} />)}
                {frames.length === 0 && (
                  <div className="p-5 text-[13px]" style={{ color: "var(--ink-faint)" }}>
                    This call has finished. Its transcript isn&apos;t kept after the page
                    reloads — only the outcome is.
                  </div>
                )}
              </div>

              <footer className="px-5 py-3 border-t flex items-center gap-2 flex-wrap"
                style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
                {!summoned && !ended && (
                  <span className="text-[12px]" style={{ color: "var(--ink-faint)" }}>
                    These wake up when a person answers. Until then you can walk away.
                  </span>
                )}
                {(summoned || ended) && <>
                  <button className="btn" disabled={!summoned} onClick={() => control("listen")}
                    style={mode === "listen" ? { borderColor: "var(--accent)" } : {}}>Listen in</button>
                  <button className="btn" disabled={!summoned} onClick={() => control("join")}
                    style={mode === "join" ? { borderColor: "var(--accent)" } : {}}>Join</button>
                  <button className="btn" disabled={!summoned} onClick={() => control("take_over")}
                    style={mode === "take_over" ? { borderColor: "var(--accent)" } : {}}>Take over</button>
                  <button className="btn danger" disabled={!summoned}
                    onClick={() => control("not_a_person")}>That isn&apos;t a person</button>
                </>}
              </footer>
            </section>

            {/* ---- context sidebar ---- */}
            <aside className="w-[264px] shrink-0 border-l overflow-y-auto p-4"
              style={{ borderColor: "var(--line)" }}>
              <Block title="Is this a person?">
                <div className="text-[12px] mb-2.5" style={{ color: "var(--ink-soft)" }}>
                  Saathi won&apos;t interrupt you until <strong>three</strong> of these agree.
                  Any one alone can be faked.
                </div>
                {CHECKS.map(c => {
                  const on = voted.includes(c.id);
                  const sc = SCRIPTED.has(c.id);
                  return (
                    <div key={c.id} className="mb-2" title={c.asks}>
                      <div className="flex items-center gap-1.5 text-[12px]">
                        <span style={{ color: on ? "var(--green)" : "var(--ink-faint)" }}>
                          {on ? "✓" : "·"}
                        </span>
                        <span style={{ color: on ? "var(--ink)" : "var(--ink-faint)" }}>{c.name}</span>
                        {sc && <span className="ml-auto text-[10px]" style={{ color: "var(--amber)" }}
                          title="Not measured yet — a stand-in while the real model is built">
                          not measured
                        </span>}
                      </div>
                      <div className="bar mt-1">
                        <i style={{ width: on ? "100%" : "0%",
                          background: sc ? "var(--amber)" : "var(--green)" }} />
                      </div>
                    </div>
                  );
                })}
                {demoted && <div className="text-[12px] mt-2" style={{ color: "var(--red)" }}>
                  You said that wasn&apos;t a person. It won&apos;t interrupt again.
                </div>}
                <button className="btn mt-2 w-full" style={{ fontSize: 12, padding: "5px 10px" }}
                  onClick={() => setDetails(d => !d)}>
                  {details ? "Hide" : "Show"} technical detail
                </button>
                {details && (
                  <div className="mt-2 p-2 rounded text-[11px] rise"
                    style={{ background: "var(--line-soft)", color: "var(--ink-soft)" }}>
                    <div className="mono">{det?.decision ?? "—"} · score{" "}
                      {det?.score > 0 ? "+" : ""}{det?.score?.toFixed?.(2) ?? "—"}</div>
                    <div className="mt-1">{det?.reason ?? "gathering evidence"}</div>
                  </div>
                )}
              </Block>

              {understood && <Block title="What Saathi carries">
                <Kv k="Company" v={understood.company} />
                {Object.entries(understood.facts ?? {}).map(([k, v]) => (
                  <Kv key={k} k={k.replace(/_/g, " ")} v={String(v)} />
                ))}
                <div className="text-[11px] mt-2 pt-2 border-t" style={{ borderColor: "var(--line)", color: "var(--ink-faint)" }}>
                  <strong style={{ color: "var(--ink-soft)" }}>Never has:</strong>{" "}
                  {(understood.will_be_asked_for ?? []).join(", ").replace(/_/g, " ")}.
                  They&apos;ll ask you directly.
                </div>
              </Block>}

              {mandate?.readback && <Block title="May agree to">
                <div className="text-[12px]">{mandate.readback}</div>
              </Block>}

              {ended && <Block title="Outcome">
                <Kv k="Was" v={ended.truth === "bot" ? "a machine" : "a person"} />
                <Kv k="Fetched you" v={ended.fetched ? clock(ended.fetched_at_ms) : "no"} />
                <Kv k="Probes used" v={String(ended.probes)} />
                {ended.false_fetch && <div className="text-[12px] mt-1" style={{ color: "var(--red)" }}>
                  It interrupted you for a machine. That&apos;s the failure that matters.
                </div>}
              </Block>}
            </aside>
          </div>
        )}
      </main>
    </div>
  );
}

function StatePill({ c }: { c: any }) {
  const m: Record<string, [string, string]> = {
    live: ["live", "Happening now"], queued: ["live", "Dialling"],
    needs_you: ["you", "Needs you"], done: ["done", "Reached a person"],
    held: ["held", "Was a machine"],
  };
  const [cls, label] = m[c.state] ?? ["held", c.state];
  return <span className={`pill ${cls}`}><i className="dot" />{label}</span>;
}

function Block({ title, children }: any) {
  return (
    <div className="mb-4">
      <div className="text-[11px] font-bold uppercase tracking-wide mb-2"
        style={{ color: "var(--ink-faint)" }}>{title}</div>
      {children}
    </div>
  );
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2 text-[12px] py-0.5">
      <span style={{ color: "var(--ink-faint)" }}>{k}</span>
      <span className="ml-auto text-right break-words">{v}</span>
    </div>
  );
}

function Thread({ f }: { f: Frame }) {
  if (f.type === "hello" || f.type === "detector" || f.type === "ended") return null;

  if (f.type === "hold")
    return <div className="px-5 py-2 text-[12px] breathe" style={{ color: "var(--ink-faint)" }}>
      ♪ hold music
    </div>;

  if (f.type === "dtmf")
    return <div className="px-5 py-1.5 text-[12px]" style={{ color: "var(--accent)" }}>
      {f.text} <span style={{ color: "var(--ink-faint)" }}>— {f.note}</span>
    </div>;

  if (f.type === "summon" || f.type === "handoff") {
    const danger = f.type === "handoff";
    return (
      <div className="px-5 py-3 rise" style={{
        background: danger ? "#fdf4f3" : "#f3fbf7",
        borderLeft: `3px solid ${danger ? "var(--red)" : "var(--green)"}`,
      }}>
        <div className="font-bold text-[13px]" style={{ color: danger ? "var(--red)" : "var(--green)" }}>
          {danger ? "They asked you to verify — handing over" : "A person is on the line"}
        </div>
        <div className="text-[12px] mt-0.5" style={{ color: "var(--ink-soft)" }}>{f.text}</div>
      </div>
    );
  }

  const us = f.speaker === "saathi";
  return (
    <div className={`thread ${us ? "us" : ""} rise`}>
      <div className="flex items-center gap-2 mb-1 flex-wrap">
        <span className="who">{VOICE[f.speaker] ?? f.speaker}</span>
        {f.note?.includes("[disclosure]") && <span className="pill">says it is an AI</span>}
        {f.type === "probe" && <span className="pill" style={{ color: "var(--amber)" }}>asking to check</span>}
      </div>
      <div className="text-[13.5px]">{f.text}</div>
    </div>
  );
}

function Compose(p: any) {
  return (
    <div className="max-w-[620px] mx-auto p-6">
      <h1 className="text-[20px] font-bold">Start a call</h1>
      <p className="text-[13px] mt-1 mb-5" style={{ color: "var(--ink-soft)" }}>
        Saathi rings the company, sits through the menu and the hold music, and tells you
        the moment a real person picks up. You do the talking.
      </p>

      <div className="panel p-4 mb-4">
        <label className="text-[12px] font-bold">What went wrong?</label>
        <textarea className="mt-2" rows={3} value={p.problem}
          onChange={(e: any) => p.setProblem(e.target.value)} />
        <div className="text-[12px] mt-2" style={{ color: "var(--ink-faint)" }}>
          No card numbers or one-time codes — Saathi never needs them, and anything like
          that is stripped before a word is said out loud.
        </div>
        <button className="btn primary mt-3" onClick={p.understand}
          disabled={p.busy || !p.problem.trim()}>
          {p.busy ? "Reading…" : "Check what it understood"}
        </button>
      </div>

      {p.understood && (p.understood.matched ? (
        <div className="panel p-4 mb-4 rise">
          <div className="text-[12px] font-bold mb-2">Saathi understood</div>
          <Kv k="Company" v={p.understood.company} />
          <Kv k="Goal" v={p.understood.goal} />
          {Object.entries(p.understood.facts ?? {}).map(([k, v]: any) => (
            <Kv key={k} k={k.replace(/_/g, " ")} v={String(v)} />
          ))}
          <div className="text-[12px] mt-2 pt-2 border-t" style={{ borderColor: "var(--line)", color: "var(--ink-faint)" }}>
            They&apos;ll ask <em>you</em> for {(p.understood.will_be_asked_for ?? []).join(", ").replace(/_/g, " ")}.
            Saathi doesn&apos;t have them and can&apos;t get them.
          </div>

          <div className="text-[12px] font-bold mt-4 mb-1">Who picks up</div>
          <div className="text-[12px] mb-2" style={{ color: "var(--ink-faint)" }}>
            A practice line, so you choose. Try the voice bot — it sounds completely
            human and Saathi still shouldn&apos;t interrupt you.
          </div>
          <div className="flex gap-2 flex-wrap">
            {(p.understood.scenarios ?? []).map((s: any) => (
              <button key={s.id} className="btn" onClick={() => p.setScenario(s.id)}
                style={p.scenario === s.id ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}}>
                {s.label} · {s.truth}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="panel p-4 mb-4" style={{ color: "var(--amber)" }}>{p.understood.why}</div>
      ))}

      {p.understood?.matched && (
        <div className="panel p-4 mb-4 rise">
          <div className="text-[12px] font-bold">What may Saathi agree to?</div>
          <div className="text-[12px] mt-1 mb-2" style={{ color: "var(--ink-soft)" }}>
            <strong>Leave it empty if you&apos;re not sure.</strong> It will explain the problem,
            take a reference number and ask them to call you back — agreeing to nothing.
          </div>
          <textarea rows={2} value={p.mandateText}
            placeholder="e.g. accept a refund of 400 or more, or a redelivery if they can't"
            onChange={(e: any) => p.setMandateText(e.target.value)} />
          <div className="text-[12px] mt-2" style={{ color: "var(--ink-faint)" }}>
            Amounts mean the <em>smallest</em> offer you&apos;d take without being asked.
          </div>
          {p.mandate && (
            <div className="mt-2 p-2 rounded text-[12px] rise" style={{ background: "var(--line-soft)" }}>
              {p.mandate.error || p.mandate.needs?.[0] || p.mandate.readback}
            </div>
          )}
          <div className="flex gap-2 mt-3 items-center flex-wrap">
            <button className="btn" onClick={p.checkMandate} disabled={p.busy || !p.mandateText.trim()}>
              Read it back
            </button>
            <div className="flex-1" />
            <select value={p.speed} onChange={(e: any) => p.setSpeed(+e.target.value)}
              style={{ width: "auto" }}>
              {[10, 20, 40].map(v => <option key={v} value={v}>{v}× faster</option>)}
            </select>
            <button className="btn primary" onClick={p.placeCall} disabled={!p.scenario}>
              Place the call
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
