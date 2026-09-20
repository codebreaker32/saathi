"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API ?? "";

/* Fixed order, always all six positions, so a dark dot means "this has not
   fired" rather than "this does not exist". */
const CHECKS = [
  { id: "SYNTHESIS",   name: "The voice sounds like a person, not a generated one" },
  { id: "IDENTITY",    name: "Still the same voice as a moment ago" },
  { id: "REPETITION",  name: "They haven't said this exact line on a previous call" },
  { id: "CONTINGENCY", name: "Their answer depends on what Saathi actually said" },
  { id: "DUPLEX",      name: "They stop when interrupted, the way people do" },
];
/* Verified across all 8 scenarios: IDENTITY never fires, and SYNTHESIS is a
   scripted stand-in. Rendering either as a normal dot would be a quiet lie. */
const UNMEASURED = new Set(["SYNTHESIS", "IDENTITY"]);

const SPEAKER: Record<string, string> = {
  menu: "Recorded menu", queue: "Recorded message",
  bot: "Whoever answered", rep: "Whoever answered", saathi: "Saathi",
};

const STATUS: Record<string, string> = {
  dialling: "Dialling.",
  menu: "Going through the menu.",
  hold: "On hold.",
  assessing: "Someone is speaking. Checking whether they're a person.",
  person: "A person is on the line.",
  verify: "They want to verify your identity. That part is yours.",
  ended: "Call finished.",
};

const mmss = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export default function Page() {
  const [screen, setScreen] = useState<"table" | "call" | "why" | "receipt">("table");

  const [problem, setProblem] = useState(
    "My Zomato order was two hours late and they charged me full price, order 4471, rs 499");
  const [understood, setUnderstood] = useState<any>(null);
  const [scenario, setScenario] = useState("");
  const [mandateText, setMandateText] = useState("");
  const [mandate, setMandate] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const speed = 10;

  const [frames, setFrames] = useState<any[]>([]);
  const [callMs, setCallMs] = useState(0);
  const [det, setDet] = useState<any>(null);
  const [status, setStatus] = useState("dialling");
  const [summoned, setSummoned] = useState(false);
  const [ended, setEnded] = useState<any>(null);
  const [demoted, setDemoted] = useState(false);
  const [muted, setMuted] = useState(false);
  const [rang, setRang] = useState(false);

  const es = useRef<EventSource | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const mute = useRef(false);
  const tape = useRef<HTMLDivElement | null>(null);

  useEffect(() => { tape.current?.scrollTo({ top: 1e9, behavior: "smooth" }); }, [frames, screen]);
  useEffect(() => () => { es.current?.close(); audio.current?.pause(); }, []);

  /* The summon tone, synthesised rather than fetched, so [Test the ring] works
     before a call exists and needs no asset. */
  const ring = useCallback(() => {
    try {
      const C = ctx.current ?? new AudioContext();
      ctx.current = C;
      if (C.state === "suspended") C.resume();
      const t0 = C.currentTime;
      [0, 0.45].forEach(off => {
        const o = C.createOscillator(), g = C.createGain();
        o.frequency.value = 660; o.type = "sine";
        g.gain.setValueAtTime(0, t0 + off);
        g.gain.linearRampToValueAtTime(0.22, t0 + off + 0.04);
        g.gain.linearRampToValueAtTime(0, t0 + off + 0.34);
        o.connect(g).connect(C.destination); o.start(t0 + off); o.stop(t0 + off + 0.4);
      });
      if (navigator.vibrate) navigator.vibrate([90, 70, 90]);
    } catch {}
  }, []);

  const say = useCallback((id: string) => {
    if (mute.current) return;
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

  function call() {
    ctx.current = ctx.current ?? new AudioContext();   // the one resume gesture
    es.current?.close();
    setFrames([]); setDet(null); setEnded(null); setSummoned(false);
    setDemoted(false); setCallMs(0); setStatus("dialling"); setRang(false);
    mute.current = false; setMuted(false);
    setScreen("call");

    const src = new EventSource(
      `${API}/api/events?scenario=${scenario}&speed=${speed}`);
    es.current = src;
    src.onmessage = e => {
      const f = JSON.parse(e.data);
      setCallMs(f.call_ms); setFrames(p => [...p, f]);
      if (f.type === "detector") setDet(f);
      if (f.type === "menu" || f.type === "dtmf") setStatus("menu");
      if (f.type === "hold" || f.type === "announcement") setStatus("hold");
      if (f.type === "utterance" || f.type === "probe") setStatus("assessing");
      if (f.type === "summon") { setSummoned(true); setStatus("person"); ring(); }
      if (f.type === "handoff") { setSummoned(true); setStatus("verify"); ring(); }
      if (f.type === "ended") { setEnded(f); setStatus("ended"); src.close(); }
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
      audio.current?.pause(); mute.current = true; setMuted(true);
      setDemoted(true); setSummoned(false); setStatus("hold");
    }
    if (action === "take_over") { audio.current?.pause(); mute.current = true; setMuted(true); }
  }

  const voted: string[] = det?.voted ?? [];
  const spoken = frames.filter(f => f.audio).length;
  const deadMs = Math.max(0, callMs - spoken * 3200);

  // ---------------------------------------------------------------- table --
  if (screen === "table") return (
    <div className="wrap py-7">
      <div className="text-[13px]" style={{ color: "var(--ink-faint)" }}>Saathi</div>
      <h1 className="text-[26px] font-bold leading-tight mt-1">
        I&apos;ll wait on hold.<br />You take the call.
      </h1>
      <p className="text-[15px] mt-2.5" style={{ color: "var(--ink-soft)" }}>
        Tell me what went wrong. I&apos;ll ring them, sit through the menu and the hold
        music, and ring you the moment an actual person picks up.
      </p>

      <div className="card p-4 mt-5">
        <textarea rows={3} value={problem} onChange={e => setProblem(e.target.value)} />
        <p className="hair mt-2">
          Don&apos;t put a card number or a code in here. I never need one — and I
          couldn&apos;t use one, it&apos;s never loaded.
        </p>
        <button className="big mt-3" onClick={understand} disabled={busy || !problem.trim()}>
          {busy ? "Reading…" : "Continue"}
        </button>
      </div>

      {understood && (understood.matched ? (
        <div className="card p-4 mt-4 rise">
          <div className="text-[15px] font-semibold">
            {understood.company} — {understood.goal}
          </div>
          <div className="hair mt-1">
            I&apos;ll quote {Object.entries(understood.facts ?? {})
              .map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") || "what you wrote"}.
          </div>
          <div className="hair mt-2" style={{ color: "var(--ink-soft)" }}>
            They&apos;ll ask <em>you</em> for {(understood.will_be_asked_for ?? [])
              .join(" and ").replace(/_/g, " ")}. I don&apos;t have those.
          </div>

          <div className="line mt-3 pt-3">
            <div className="text-[13px] mb-1.5" style={{ color: "var(--ink-soft)" }}>
              If they offer something while you&apos;re away
            </div>
            <textarea rows={2} value={mandateText}
              placeholder="accept a refund of 400 or more, or a redelivery"
              onChange={e => { setMandateText(e.target.value); setMandate(null); }} />
            <p className="hair mt-1.5">
              Leave it blank and I&apos;ll agree to nothing — just take a reference
              number and ask them to call you back.
            </p>
            {mandate && <div className="text-[13px] mt-2 p-2.5 rounded-lg"
              style={{ background: "var(--raise-2)" }}>
              {mandate.error || mandate.needs?.[0] || mandate.readback}
            </div>}
            {mandateText.trim() && (
              <button className="quiet" onClick={checkMandate} disabled={busy}>
                {busy ? "checking…" : "read that back to me"}
              </button>
            )}
          </div>

          <div className="line mt-3 pt-3">
            <div className="text-[13px] mb-2" style={{ color: "var(--ink-soft)" }}>
              Practice line — choose who picks up
            </div>
            <select value={scenario} onChange={e => setScenario(e.target.value)}>
              {(understood.scenarios ?? []).map((s: any) => (
                <option key={s.id} value={s.id}>{s.label}</option>
              ))}
            </select>
          </div>

          <button className="big mt-4" onClick={call} disabled={!scenario}>
            Call them for me
          </button>
          <p className="hair mt-2 text-center">Nobody is actually being rung.</p>
        </div>
      ) : (
        <div className="card p-4 mt-4" style={{ color: "var(--warn)" }}>{understood.why}</div>
      ))}
    </div>
  );

  // ----------------------------------------------------------------- why ---
  if (screen === "why") return (
    <div className="wrap py-6">
      <button className="quiet" onClick={() => setScreen("call")}>‹ back to the call</button>
      <h2 className="text-[20px] font-bold mt-1">What I heard</h2>
      <p className="hair mt-1">
        Every word, in order. Two of the five checks below aren&apos;t measured in this
        build and are marked so.
      </p>
      <div ref={tape} className="mt-4">
        {frames.map(f => <Turn key={f.seq} f={f} />)}
      </div>
    </div>
  );

  // ------------------------------------------------------------- receipt ---
  if (screen === "receipt" && ended) return (
    <div className="wrap py-7">
      <h2 className="text-[22px] font-bold">
        {ended.false_fetch ? "I interrupted you for a machine."
          : ended.truth === "bot" ? "That was never a person."
          : ended.handed_off ? "They asked to verify. Over to you."
          : ended.fetched ? "You got a person." : "They hung up before I could reach anyone."}
      </h2>
      <p className="text-[15px] mt-2" style={{ color: "var(--ink-soft)" }}>
        {ended.truth === "bot"
          ? "It sounded human the whole way through. I didn't ring you, and that's the job."
          : "That's the part only you could do."}
      </p>
      <div className="card p-4 mt-4">
        <Row k="You waited" v="none of it" />
        <Row k="I waited" v={mmss(callMs)} />
        <Row k="Nobody was talking for" v={mmss(deadMs)} />
        <Row k="Checks that agreed" v={`${voted.length} of 3 needed`} />
      </div>
      <button className="ghost mt-4" onClick={() => setScreen("why")}>See what I heard</button>
      <button className="big mt-2" onClick={() => { setScreen("table"); setEnded(null); }}>
        Start another
      </button>
    </div>
  );

  // ---------------------------------------------------------------- call ---
  return (
    <div className="wrap py-6 flex flex-col min-h-screen">
      <div className="flex items-start gap-3">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold truncate">
            {understood?.company ?? "Call"}
          </div>
          {demoted && <div className="hair" style={{ color: "var(--warn)" }}>
            I won&apos;t interrupt you again on this call.
          </div>}
        </div>
        <button className="quiet ml-auto shrink-0"
          onClick={() => { mute.current = !muted; setMuted(m => !m); if (!muted) audio.current?.pause(); }}>
          {muted ? "sound off" : "sound on"}
        </button>
      </div>

      <div className="flex items-center gap-2.5 mt-5">
        <span className="breathe" style={{ width: 9, height: 9, borderRadius: 99,
          background: status === "person" ? "var(--good)" : "var(--calm)", flexShrink: 0 }} />
        <div className="text-[19px] leading-snug" role="status" aria-live="polite">
          {STATUS[status]}
        </div>
      </div>

      <div className="mt-6 flex items-baseline gap-3">
        <div className="mono text-[44px] leading-none">{mmss(callMs)}</div>
        <button className="quiet text-[12px]" style={{ color: "var(--ink-faint)" }}
          title="The clock is real call time. Playback is sped up so you can watch it.">
          {speed}× faster
        </button>
      </div>
      <div className="hair">on this call</div>

      {status !== "dialling" && (
        <div className="card p-4 mt-6">
          <div className="flex gap-2 mb-2.5">
            {CHECKS.map(c => (
              <span key={c.id} title={c.name}
                className={`dot ${voted.includes(c.id) ? "on" : ""} ${UNMEASURED.has(c.id) ? "unmeasured" : ""}`} />
            ))}
          </div>
          <div className="text-[14px]" style={{ color: "var(--ink-soft)" }}>
            {voted.length === 0 ? "Nothing yet says this is a person."
              : `${voted.length} of the 3 checks I need agree.`}
          </div>
          <button className="quiet mt-1" onClick={() => setScreen("why")}>
            what I&apos;m listening for ›
          </button>
        </div>
      )}

      {status === "hold" && !summoned && (
        <div className="card p-4 mt-4 rise">
          <div className="text-[16px] font-semibold">You can put this down.</div>
          <div className="text-[14px] mt-1" style={{ color: "var(--ink-soft)" }}>
            I&apos;ll ring the moment a person answers. Nothing needs you until then.
          </div>
          <button className="ghost mt-3" onClick={() => { ring(); setRang(true); }}>
            {rang ? "Ring it again" : "Test the ring"}
          </button>
          {rang && <div className="hair mt-1.5 text-center">
            That&apos;s the sound. It&apos;ll be that, on your phone.
          </div>}
        </div>
      )}

      <div className="mt-auto pt-6">
        {summoned && !ended && (
          <div className="rise">
            <button className="big" onClick={() => control("take_over")}>
              I&apos;ll take it
            </button>
            <button className="ghost mt-2" onClick={() => control("not_a_person")}>
              That isn&apos;t a person
            </button>
          </div>
        )}
        {ended && (
          <button className="big" onClick={() => setScreen("receipt")}>See what happened</button>
        )}
        {!summoned && !ended && (
          <button className="quiet w-full" onClick={() => setScreen("why")}>
            listen in ›
          </button>
        )}
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline gap-3 py-1.5">
      <span className="text-[14px]" style={{ color: "var(--ink-soft)" }}>{k}</span>
      <span className="mono ml-auto text-[15px]">{v}</span>
    </div>
  );
}

function Turn({ f }: { f: any }) {
  if (f.type === "hello" || f.type === "detector" || f.type === "ended") return null;
  if (f.type === "hold")
    return <div className="hair py-2 breathe">hold music</div>;
  if (f.type === "dtmf")
    return <div className="text-[13px] py-1.5" style={{ color: "var(--calm)" }}>
      {f.text} <span className="hair">— {f.note}</span>
    </div>;
  if (f.type === "summon" || f.type === "handoff")
    return <div className="py-3 my-2 px-3 rounded-xl flash"
      style={{ border: `1px solid ${f.type === "handoff" ? "var(--stop)" : "var(--good)"}` }}>
      <div className="text-[14px] font-semibold"
        style={{ color: f.type === "handoff" ? "var(--stop)" : "var(--good)" }}>
        {f.type === "handoff" ? "They want to verify you" : "A person answered"}
      </div>
    </div>;

  const us = f.speaker === "saathi";
  return (
    <div className="py-2.5 line">
      <div className="text-[11px] mb-0.5 flex gap-2"
        style={{ color: us ? "var(--calm)" : "var(--ink-faint)" }}>
        {SPEAKER[f.speaker] ?? f.speaker}
        {f.note?.includes("[disclosure]") && <span style={{ color: "var(--ink-faint)" }}>
          · told them it&apos;s an AI</span>}
      </div>
      <div className="text-[14.5px]" style={{ color: us ? "var(--ink)" : "var(--ink-soft)" }}>
        {f.text}
      </div>
    </div>
  );
}
