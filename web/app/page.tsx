"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8787";
const FAMILIES = ["SYNTHESIS", "IDENTITY", "REPETITION", "CONTINGENCY", "DUPLEX"];

type Frame = Record<string, any>;
type Scenario = { id: string; org: string; goal: string; truth: string };

const clock = (ms: number) => {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

const VOICE: Record<string, { label: string; color: string }> = {
  menu:   { label: "IVR",    color: "var(--text-03)" },
  queue:  { label: "queue",  color: "var(--text-03)" },
  bot:    { label: "Ava",    color: "var(--warning)" },
  rep:    { label: "agent",  color: "var(--success)" },
  saathi: { label: "Saathi", color: "var(--accept)" },
};

export default function Page() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [pick, setPick] = useState("real_rep");
  const [speed, setSpeed] = useState(40);
  const [live, setLive] = useState(false);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [callMs, setCallMs] = useState(0);
  const [det, setDet] = useState<Frame | null>(null);
  const [phase, setPhase] = useState("idle");
  const [summoned, setSummoned] = useState(false);
  const [mode, setMode] = useState<string | null>(null);
  const [ended, setEnded] = useState<Frame | null>(null);
  const [demoted, setDemoted] = useState(false);

  const es = useRef<EventSource | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const log = useRef<HTMLDivElement | null>(null);
  const muted = useRef(false);

  useEffect(() => {
    fetch(`${API}/api/scenarios`).then(r => r.json()).then(setScenarios).catch(() => {});
  }, []);
  useEffect(() => { log.current?.scrollTo({ top: 1e9, behavior: "smooth" }); }, [frames]);

  const say = useCallback((id: string) => {
    if (muted.current) return;
    audio.current?.pause();
    const a = new Audio(`${API}/api/audio/${id}.wav`);
    audio.current = a;
    a.play().catch(() => {});
  }, []);

  const start = () => {
    es.current?.close();
    setFrames([]); setDet(null); setEnded(null); setSummoned(false);
    setMode(null); setDemoted(false); setCallMs(0); setPhase("dialing");
    muted.current = false; setLive(true);

    const src = new EventSource(`${API}/api/events?scenario=${pick}&speed=${speed}`);
    es.current = src;
    src.onmessage = e => {
      const f: Frame = JSON.parse(e.data);
      setCallMs(f.call_ms);
      setFrames(p => [...p, f]);
      if (f.type === "detector") setDet(f);
      if (f.type === "menu" || f.type === "dtmf") setPhase("navigating the menu");
      if (f.type === "hold" || f.type === "announcement") setPhase("on hold");
      if (f.type === "utterance" || f.type === "probe") setPhase("someone is speaking");
      if (f.type === "summon") { setSummoned(true); setPhase("fetching you"); }
      if (f.type === "handoff") { setSummoned(true); setPhase("handed to you"); }
      if (f.type === "ended") { setEnded(f); setLive(false); setPhase("call ended"); src.close(); }
      if (f.audio) say(f.audio);
    };
    src.onerror = () => { setLive(false); src.close(); };
  };

  const control = (action: string) => {
    fetch(`${API}/api/control`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, scenario: pick, call_ms: callMs }),
    }).catch(() => {});
    if (action === "not_a_person") {
      audio.current?.pause(); muted.current = true;
      setDemoted(true); setSummoned(false); setMode(null); setPhase("back on hold");
    } else {
      setMode(action);
      // Taking over strips the agent of the floor: it stops mid-word.
      if (action === "take_over") { audio.current?.pause(); muted.current = true; }
      else muted.current = false;
    }
  };

  const voted: string[] = det?.voted ?? [];
  const scripted: string[] = det?.scripted ?? [];
  const decision = det?.decision ?? "—";
  const fetching = decision === "FETCH";

  return (
    <main className="min-h-screen flex flex-col max-w-6xl mx-auto px-4">
      {/* ---- header: honest clock + speed badge ---- */}
      <header className="flex items-center gap-3 py-4 flex-wrap">
        <div className="text-base font-semibold">Saathi</div>
        <div style={{ color: "var(--text-03)" }} className="text-xs">
          is there a human on this line?
        </div>
        <div className="flex-1" />
        <div className="mono text-2xl" style={{ color: live ? "var(--text-01)" : "var(--text-03)" }}>
          {clock(callMs)}
        </div>
        <div className="text-[10px] px-2 py-1 rounded surface3 mono" title="playback is compressed; the clock is real call time">
          {speed}× speed
        </div>
      </header>

      {/* ---- setup ---- */}
      <section className="surface rounded-xl p-3 flex gap-2 items-center flex-wrap">
        <select value={pick} onChange={e => setPick(e.target.value)} disabled={live}
          className="surface3 rounded px-2 py-2 text-sm outline-none" style={{ color: "var(--text-01)" }}>
          {scenarios.map(s => (
            <option key={s.id} value={s.id}>{s.id} — {s.org} ({s.truth})</option>
          ))}
        </select>
        <select value={speed} onChange={e => setSpeed(+e.target.value)} disabled={live}
          className="surface3 rounded px-2 py-2 text-sm outline-none" style={{ color: "var(--text-01)" }}>
          {[10, 20, 40, 80, 160].map(v => <option key={v} value={v}>{v}× speed</option>)}
        </select>
        <button onClick={start} disabled={live}
          className="px-4 py-2 rounded font-medium disabled:opacity-40"
          style={{ background: "var(--accept)" }}>
          {live ? "calling…" : "Place the call"}
        </button>
        <div className="text-xs" style={{ color: "var(--text-02)" }}>{phase}</div>
      </section>

      <div className="grid md:grid-cols-[1fr_300px] gap-4 mt-4 flex-1 min-h-0">
        {/* ---- transcript ---- */}
        <div ref={log} className="surface rounded-xl p-4 overflow-y-auto" style={{ maxHeight: "56vh" }}>
          {frames.length === 0 && (
            <div style={{ color: "var(--text-03)" }} className="text-sm">
              Pick a scenario and place the call. Audio plays in the browser —
              there is no audio device on the host.
            </div>
          )}
          {frames.map(f => <Line key={f.seq} f={f} />)}
        </div>

        {/* ---- detector ---- */}
        <aside className="surface rounded-xl p-4 h-fit">
          <div className="flex items-baseline justify-between">
            <span className="text-xs" style={{ color: "var(--text-02)" }}>evidence</span>
            <span className="mono text-xs" style={{ color: "var(--text-03)" }}>
              {voted.length}/3 agree
            </span>
          </div>
          <div className="mono text-3xl mt-1"
            style={{ color: fetching ? "var(--success)" : det?.veto ? "var(--danger)" : "var(--text-01)" }}>
            {decision}
          </div>
          {det && <div className="mono text-xs" style={{ color: "var(--text-03)" }}>
            score {det.score > 0 ? "+" : ""}{det.score?.toFixed?.(2)}
          </div>}

          <div className="mt-4 space-y-2">
            {FAMILIES.map(fam => {
              const on = voted.includes(fam);
              const isScripted = scripted.includes(fam);
              return (
                <div key={fam}>
                  <div className="flex justify-between text-[11px]">
                    <span style={{ color: on ? "var(--text-01)" : "var(--text-03)" }}>
                      {fam.toLowerCase()}
                      {isScripted && (
                        <span title="scripted stand-in, not a measured model"
                          style={{ color: "var(--warning)" }}> ◦</span>
                      )}
                    </span>
                    <span className="mono" style={{ color: "var(--text-03)" }}>{on ? "voted" : "—"}</span>
                  </div>
                  <div className="bar mt-1">
                    <i style={{ width: on ? "100%" : "0%",
                                background: isScripted ? "var(--warning)" : "var(--success)" }} />
                  </div>
                </div>
              );
            })}
          </div>

          <p className="text-[11px] mt-3 leading-snug" style={{ color: "var(--text-03)" }}>
            {det?.reason ?? "Three independent families must agree before it fetches you."}
          </p>
          {scripted.length > 0 && (
            <p className="text-[11px] mt-2 leading-snug" style={{ color: "var(--warning)" }}>
              ◦ marked families are scripted stand-ins, not measured models.
            </p>
          )}
          {demoted && (
            <p className="text-[11px] mt-2" style={{ color: "var(--danger)" }}>
              You said that wasn&apos;t a person. It will not ring you again this call.
            </p>
          )}
        </aside>
      </div>

      {/* ---- toolbar ---- */}
      <div className="flex items-center justify-center gap-3 py-5">
        <Tool label="Listen" active={mode === "listen"} disabled={!summoned}
          onClick={() => control("listen")} icon="◎" />
        <Tool label="Join" active={mode === "join"} disabled={!summoned}
          onClick={() => control("join")} icon="◉" />
        <Tool label="Take over" active={mode === "take_over"} disabled={!summoned}
          onClick={() => control("take_over")} icon="▣" />
        <Tool label="Not a person" danger disabled={!summoned}
          onClick={() => control("not_a_person")} icon="✕" />
      </div>

      {ended && (
        <div className="surface rounded-xl p-4 mb-6 slidein">
          <div className="flex gap-6 flex-wrap text-sm">
            <Stat k="truth" v={ended.truth} />
            <Stat k="fetched" v={ended.fetched ? clock(ended.fetched_at_ms) : "never"} />
            <Stat k="families" v={ended.families?.join(", ") || "—"} />
            <Stat k="probes" v={String(ended.probes)} />
            <Stat k="handed over" v={ended.handed_off ? "yes" : "no"} />
          </div>
          <div className="mt-2 text-sm"
            style={{ color: ended.false_fetch ? "var(--danger)" : "var(--success)" }}>
            {ended.false_fetch
              ? "FALSE FETCH — this is the failure that breaks the product."
              : ended.truth === "bot"
                ? "Correctly held. It was a machine and you were never disturbed."
                : ended.fetched ? "Correct. A person answered and you were fetched."
                  : "Missed a human — recoverable: the rep hangs up, we retry."}
          </div>
        </div>
      )}
    </main>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div>
      <div className="text-[10px]" style={{ color: "var(--text-03)" }}>{k}</div>
      <div className="mono">{v}</div>
    </div>
  );
}

function Tool({ label, icon, onClick, disabled, active, danger }: any) {
  return (
    <div className="flex flex-col items-center gap-1">
      <button onClick={onClick} disabled={disabled} title={label}
        className={`tbtn ${danger ? "danger" : ""} ${active ? "on" : ""}`}>
        <span style={{ fontSize: 18 }}>{icon}</span>
      </button>
      <span className="text-[10px]" style={{ color: disabled ? "var(--text-03)" : "var(--text-02)" }}>
        {label}
      </span>
    </div>
  );
}

function Line({ f }: { f: Frame }) {
  if (f.type === "hello")
    return <div className="text-xs mb-3 slidein" style={{ color: "var(--text-03)" }}>
      Calling {f.org} — {f.goal}
    </div>;

  if (f.type === "detector") return null;

  if (f.type === "hold")
    return <div className="text-xs my-2 pulse slidein" style={{ color: "var(--text-03)" }}>
      ♪ hold music
    </div>;

  if (f.type === "dtmf")
    return <div className="text-xs my-1 slidein" style={{ color: "var(--accept)" }}>
      ⌨ {f.text} <span style={{ color: "var(--text-03)" }}>— {f.note}</span>
    </div>;

  if (f.type === "summon" || f.type === "handoff")
    return <div className="my-3 p-3 rounded-lg slidein" style={{ background: "var(--surface-03)" }}>
      <div style={{ color: f.type === "handoff" ? "var(--danger)" : "var(--success)" }}
        className="text-sm font-medium">
        {f.type === "handoff" ? "Verification asked — handing over to you" : "Fetching you now"}
      </div>
      <div className="text-xs" style={{ color: "var(--text-02)" }}>{f.text}</div>
    </div>;

  const v = VOICE[f.speaker] ?? { label: f.speaker ?? "", color: "var(--text-02)" };
  const isProbe = f.type === "probe";
  return (
    <div className="mb-3 slidein">
      <div className="text-[10px] mb-0.5 flex gap-2 items-center">
        <span style={{ color: v.color }}>{v.label}</span>
        {f.note?.includes("[disclosure]") && (
          <span className="px-1 rounded" style={{ background: "var(--surface-03)", color: "var(--text-03)" }}>
            discloses it is an AI
          </span>
        )}
        {isProbe && (
          <span className="px-1 rounded" style={{ background: "var(--surface-03)", color: "var(--warning)" }}>
            probe
          </span>
        )}
      </div>
      <div className="text-sm" style={{ color: "var(--text-01)" }}>{f.text}</div>
    </div>
  );
}
