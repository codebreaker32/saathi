"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8787";
/* The detector's families, said the way a person would say them. Nobody
   outside this codebase knows what CONTINGENCY means, and a bar labelled
   with it tells a user nothing about whether to trust the thing. */
const CHECKS = [
  { id: "SYNTHESIS",   name: "Real voice",     asks: "Does the voice sound recorded by a person, or generated?" },
  { id: "IDENTITY",    name: "Same person",    asks: "Is this still whoever was speaking a moment ago?" },
  { id: "REPETITION",  name: "Said it before", asks: "Have we heard this exact sentence on an earlier call?" },
  { id: "CONTINGENCY", name: "Replying to us", asks: "Does their answer depend on what Saathi actually said?" },
  { id: "DUPLEX",      name: "Takes turns",    asks: "Do they stop when interrupted, the way people do?" },
];
const SCRIPTED = new Set(["SYNTHESIS", "IDENTITY"]);

const VERDICT: Record<string, { say: string; tone: string }> = {
  FETCH:     { say: "That's a person",  tone: "var(--success)" },
  MACHINE:   { say: "Still a machine",  tone: "var(--faint)" },
  UNDECIDED: { say: "Not sure yet",     tone: "var(--foreground)" },
  LISTENING: { say: "Listening",        tone: "var(--faint)" },
};

type Frame = Record<string, any>;
type Stage = "brief" | "understanding" | "permission" | "calling" | "summary";

const clock = (ms: number) => {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

const VOICE: Record<string, { label: string; tone: string }> = {
  menu:   { label: "Recorded menu",   tone: "var(--faint)" },
  queue:  { label: "Recorded message", tone: "var(--faint)" },
  bot:    { label: "Ava",             tone: "var(--warning)" },
  rep:    { label: "Agent",           tone: "var(--success)" },
  saathi: { label: "Saathi",          tone: "var(--primary)" },
};

/* One plain sentence for whatever is happening, so the screen is readable
   without knowing anything about how it works. */
const EXPLAIN: Record<string, string> = {
  Dialling: "Ringing the company now.",
  "Working through the menu": "Listening to the whole menu before pressing anything — the option you want is often last.",
  "On hold": "Waiting. You can put your phone down; Saathi will ring you when a person picks up.",
  "Someone is speaking": "Someone answered. Saathi is checking whether it's a person or a recording.",
  "Fetching you": "A person is on the line and Saathi is calling you now.",
  "Handed to you": "They asked to verify your identity, so Saathi stepped back. Only you can answer that.",
  "Back on hold": "You said that wasn't a person. Saathi is waiting again and won't ring you twice.",
};

export default function Page() {
  const [stage, setStage] = useState<Stage>("brief");
  const [problem, setProblem] = useState(
    "My Zomato order was two hours late and they charged me full price, order 4471, rs 499",
  );
  const [understood, setUnderstood] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [scenario, setScenario] = useState("");
  const [speed, setSpeed] = useState(10);

  const [mandateText, setMandateText] = useState("");
  const [mandate, setMandate] = useState<any>(null);

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
  const log = useRef<HTMLDivElement | null>(null);

  useEffect(() => { log.current?.scrollTo({ top: 1e9, behavior: "smooth" }); }, [frames]);
  useEffect(() => () => { es.current?.close(); audio.current?.pause(); }, []);

  const say = useCallback((id: string) => {
    if (muted.current) return;
    audio.current?.pause();
    const a = new Audio(`${API}/api/audio/${id}.wav`);
    audio.current = a;
    a.play().catch(() => {});
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
      setStage("understanding");
    } finally { setBusy(false); }
  }

  async function checkMandate() {
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/mandate`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: mandateText }),
      }).then(x => x.json());
      setMandate(r);
    } finally { setBusy(false); }
  }

  async function placeCall() {
    es.current?.close();
    setFrames([]); setDet(null); setEnded(null); setSummoned(false);
    setMode(null); setDemoted(false); setCallMs(0); setPhase("Dialling");
    muted.current = false;
    setStage("calling");

    // Everything the user typed travels as ONE brief, built through the
    // whitelist. It is the only channel into what Saathi says out loud.
    const { session } = await fetch(`${API}/api/call`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario, speed,
        playbook: understood?.playbook,
        problem,
        facts: { registered_phone: "98765 41182", ...(understood?.facts ?? {}) },
        mandate: mandate?.mandate ?? {},
      }),
    }).then(r => r.json()).catch(() => ({ session: "" }));

    const src = new EventSource(`${API}/api/events?session=${session}`);
    es.current = src;
    src.onmessage = e => {
      const f: Frame = JSON.parse(e.data);
      setCallMs(f.call_ms);
      setFrames(p => [...p, f]);
      if (f.type === "detector") setDet(f);
      if (f.type === "menu" || f.type === "dtmf") setPhase("Working through the menu");
      if (f.type === "hold" || f.type === "announcement") setPhase("On hold");
      if (f.type === "utterance" || f.type === "probe") setPhase("Someone is speaking");
      if (f.type === "summon") { setSummoned(true); setPhase("Fetching you"); }
      if (f.type === "handoff") { setSummoned(true); setPhase("Handed to you"); }
      if (f.type === "ended") { setEnded(f); setStage("summary"); src.close(); }
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
      if (action === "take_over") { audio.current?.pause(); muted.current = true; }
      else muted.current = false;
    }
  }

  function reset() {
    es.current?.close(); audio.current?.pause();
    setStage("brief"); setUnderstood(null); setMandate(null); setMandateText("");
  }

  const voted: string[] = det?.voted ?? [];
  const decision = det?.decision ?? "LISTENING";

  return (
    <main className="min-h-screen mx-auto max-w-3xl px-5 pb-16">
      <header className="flex items-center gap-3 pt-8 pb-6">
        <div className="text-[15px] font-extrabold tracking-tight">Saathi</div>
        <div className="text-[13px]" style={{ color: "var(--faint)" }}>
          your call companion
        </div>
        <div className="flex-1" />
        <Steps stage={stage} />
      </header>

      {stage === "brief" && (
        <Frame eyebrow="Step 1 of 3" title="Let Saathi handle the call"
          sub="Saathi rings the company, sits through the menu and the hold music, and calls you the moment a real person picks up. You do the talking.">
          <div className="grid sm:grid-cols-3 gap-2 mb-5">
            <Beat n="1" t="You describe it" d="In your own words, once." />
            <Beat n="2" t="Saathi waits" d="Menus, hold music, all of it." />
            <Beat n="3" t="You take over" d="Only when a person answers." />
          </div>
          <label className="eyebrow">What went wrong?</label>
          <textarea className="mt-2" rows={4} value={problem}
            onChange={e => setProblem(e.target.value)} />
          <p className="text-[12px] mt-3" style={{ color: "var(--faint)" }}>
            No card numbers or one-time codes — Saathi never needs them, and if you
            type one it gets removed before anything is said out loud.
          </p>
          <div className="flex gap-2 mt-5">
            <button className="btn primary" onClick={understand} disabled={busy || !problem.trim()}>
              {busy ? "Reading…" : "Continue"}
            </button>
          </div>
        </Frame>
      )}

      {stage === "understanding" && understood && (
        <Frame eyebrow="Step 2 of 3" title="Is this right?"
          sub="Check what Saathi picked up before it dials. You can go back and reword it.">
          {!understood.matched ? (
            <div className="text-[14px]" style={{ color: "var(--warning)" }}>
              {understood.why}
            </div>
          ) : (
            <>
              <div className="grid sm:grid-cols-2 gap-3">
                <Field k="Company" v={understood.company} />
                <Field k="Goal" v={understood.goal} />
                {Object.entries(understood.facts ?? {}).map(([k, v]) => (
                  <Field key={k} k={k.replace(/_/g, " ")} v={String(v)} />
                ))}
              </div>
              <div className="mt-4 text-[12px]" style={{ color: "var(--faint)" }}>
                matched because it {understood.why}
              </div>

              <div className="mt-5 p-3 rounded-xl" style={{ background: "var(--surface-2)" }}>
                <div className="eyebrow mb-1">They&apos;ll ask you for these — not Saathi</div>
                <div className="text-[13px]">
                  {(understood.will_be_asked_for ?? []).join(", ").replace(/_/g, " ")}
                </div>
                <div className="text-[12px] mt-1" style={{ color: "var(--faint)" }}>
                  Saathi doesn&apos;t have them and can&apos;t get them. When the agent asks,
                  it hands the call straight to you.
                </div>
              </div>

              <div className="mt-5">
                <div className="eyebrow mb-2">Who picks up</div>
                <div className="text-[12px] mb-2" style={{ color: "var(--faint)" }}>
                  This is a practice line, so you choose who answers. Try the voice bot —
                  it sounds completely human and Saathi still shouldn&apos;t call you.
                </div>
                <div className="flex gap-2 flex-wrap">
                  {(understood.scenarios ?? []).map((s: any) => (
                    <button key={s.id} onClick={() => setScenario(s.id)}
                      className="btn" style={scenario === s.id
                        ? { borderColor: "var(--primary)", color: "var(--primary)" } : {}}>
                      {s.label} <span style={{ color: "var(--faint)" }}>· {s.truth}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex gap-2 mt-6">
                <button className="btn ghost" onClick={() => setStage("brief")}>Back</button>
                <button className="btn primary" onClick={() => setStage("permission")}>
                  Set permissions
                </button>
              </div>
            </>
          )}
        </Frame>
      )}

      {stage === "permission" && (
        <Frame eyebrow="Step 3 of 3" title="What may Saathi agree to?"
          sub="If they offer something while you're away, what can Saathi accept on your behalf?">
          <textarea rows={3} value={mandateText} placeholder="e.g. accept a refund of 400 or more, or a redelivery if they can't refund"
            onChange={e => { setMandateText(e.target.value); setMandate(null); }} />

          <p className="text-[13px] mt-3" style={{ color: "var(--muted)" }}>
            <strong>Leave it empty if you&apos;re not sure.</strong> Saathi will explain the
            problem, write down a reference number and ask them to call you back — it
            won&apos;t agree to anything at all.
          </p>
          <p className="text-[12px] mt-2" style={{ color: "var(--faint)" }}>
            Amounts mean the <em>smallest</em> offer you&apos;d accept without being asked.
            Say &ldquo;400 or more&rdquo; and a ₹200 offer comes back to you instead.
          </p>

          {mandate && (
            <div className="mt-4 p-3 rounded-xl rise" style={{ background: "var(--surface-2)" }}>
              {mandate.error && (
                <div className="text-[13px]" style={{ color: "var(--warning)" }}>{mandate.error}</div>
              )}
              {mandate.needs?.length > 0 && (
                <div className="text-[13px]" style={{ color: "var(--warning)" }}>{mandate.needs[0]}</div>
              )}
              {mandate.readback && !mandate.needs?.length && (
                <>
                  <div className="eyebrow mb-1">Saathi may</div>
                  <div className="text-[14px]">{mandate.readback}</div>
                </>
              )}
            </div>
          )}

          <div className="flex gap-2 mt-6 flex-wrap">
            <button className="btn ghost" onClick={() => setStage("understanding")}>Back</button>
            <button className="btn" onClick={checkMandate} disabled={busy || !mandateText.trim()}>
              {busy ? "Checking…" : "Read it back"}
            </button>
            <div className="flex-1" />
            <select value={speed} onChange={e => setSpeed(+e.target.value)}
              style={{ width: "auto", padding: "8px 12px" }}>
              {[10, 20, 40].map(v => <option key={v} value={v}>{v}× faster</option>)}
            </select>
            <button className="btn primary" onClick={placeCall} disabled={!scenario}>
              Place the call
            </button>
          </div>
        </Frame>
      )}

      {(stage === "calling" || stage === "summary") && (
        <>
          <div className="card p-5">
            <div className="flex items-center gap-5">
              <div className="viz">
                <i /><i /><i />
                <div className="rounded-full grid place-items-center"
                  style={{ width: 74, height: 74, background: "var(--elevated)" }}>
                  <span className="mono text-[17px]">{clock(callMs)}</span>
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <div className="eyebrow">{understood?.company ?? "Call"}</div>
                <div className="text-[19px] font-bold mt-0.5">{phase}</div>
                <div className="text-[13px] mt-1 leading-snug" style={{ color: "var(--muted)" }}>
                  {EXPLAIN[phase] ?? ""}
                </div>
                <div className="text-[11px] mt-1.5" style={{ color: "var(--faint)" }}>
                  the clock is real call time · being replayed {speed}× faster
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className="text-[18px] font-extrabold"
                  style={{ color: (VERDICT[decision] ?? VERDICT.LISTENING).tone }}>
                  {(VERDICT[decision] ?? VERDICT.LISTENING).say}
                </div>
                <div className="text-[11px]" style={{ color: "var(--faint)" }}>
                  {voted.length} of 3 signs agree
                </div>
              </div>
            </div>

            <div className="mt-5 space-y-1.5">
              <div className="text-[12px] mb-2" style={{ color: "var(--muted)" }}>
                Saathi won&apos;t call you until <strong>three different signs</strong> agree
                it&apos;s a person. Any one on its own can be faked.
              </div>
              {CHECKS.map(c => {
                const on = voted.includes(c.id);
                const scripted = SCRIPTED.has(c.id);
                return (
                  <div key={c.id} className="flex items-center gap-2.5" title={c.asks}>
                    <span style={{ width: 15, color: on ? "var(--success)" : "var(--faint)" }}>
                      {on ? "✓" : "·"}
                    </span>
                    <span className="text-[13px] w-[116px] shrink-0"
                      style={{ color: on ? "var(--foreground)" : "var(--faint)" }}>
                      {c.name}
                    </span>
                    <span className="bar flex-1">
                      <i style={{ width: on ? "100%" : "0%",
                        background: scripted ? "var(--warning)" : "var(--success)" }} />
                    </span>
                    {scripted && (
                      <span className="text-[10px] shrink-0" style={{ color: "var(--warning)" }}
                        title="Not measured yet — a stand-in value while the real model is built">
                        not measured yet
                      </span>
                    )}
                  </div>
                );
              })}
            </div>

            <button className="btn ghost mt-3" style={{ padding: "4px 0", fontSize: 12 }}
              onClick={() => setDetails(d => !d)}>
              {details ? "Hide" : "Show"} the technical detail
            </button>
            {details && (
              <div className="mt-2 p-3 rounded-xl text-[12px] rise"
                style={{ background: "var(--surface-2)", color: "var(--muted)" }}>
                <div className="mono">{decision} · score {det?.score > 0 ? "+" : ""}
                  {det?.score?.toFixed?.(2) ?? "—"}</div>
                <div className="mt-1">{det?.reason ?? "gathering evidence"}</div>
                <div className="mt-1" style={{ color: "var(--faint)" }}>
                  Evidence families: {CHECKS.map(c => c.id.toLowerCase()).join(", ")}.
                  Amber ones are scripted stand-ins, not measured models.
                </div>
              </div>
            )}
            {demoted && (
              <div className="text-[12px] mt-2" style={{ color: "var(--destructive)" }}>
                You said that wasn&apos;t a person. It won&apos;t ring you again this call.
              </div>
            )}
          </div>

          <div ref={log} className="card p-5 mt-4 overflow-y-auto" style={{ maxHeight: "42vh" }}>
            {frames.map(f => <Line key={f.seq} f={f} />)}
          </div>

          <div className="mt-5">
            {!summoned && (
              <div className="text-[12px] text-center mb-3" style={{ color: "var(--faint)" }}>
                These wake up when a person answers. Until then you can walk away.
              </div>
            )}
            <div className="flex items-center justify-center gap-2 flex-wrap">
              <Ctl label="Listen in" hint="hear it, stay silent" on={mode === "listen"}
                disabled={!summoned} onClick={() => control("listen")} />
              <Ctl label="Join" hint="both of you talk" on={mode === "join"}
                disabled={!summoned} onClick={() => control("join")} />
              <Ctl label="Take over" hint="Saathi goes quiet" on={mode === "take_over"}
                disabled={!summoned} onClick={() => control("take_over")} />
              <Ctl label="That isn't a person" hint="put me back on hold" danger
                disabled={!summoned} onClick={() => control("not_a_person")} />
            </div>
          </div>
        </>
      )}

      {stage === "summary" && ended && (
        <Frame eyebrow="Call complete" title={
          ended.false_fetch ? "It fetched you for a machine"
            : ended.truth === "bot" ? "Correctly held"
            : ended.fetched ? "A person answered and you were fetched"
            : ended.handed_off ? "Handed over for verification"
            : "Missed a human"}
          sub={ended.false_fetch
            ? "This is the failure that breaks the product."
            : ended.truth === "bot"
              ? "It was a machine the whole time, and you were never disturbed."
              : "Everything above is what actually happened on the line."}>
          <div className="grid sm:grid-cols-4 gap-3">
            <Field k="truth" v={ended.truth} />
            <Field k="fetched" v={ended.fetched ? clock(ended.fetched_at_ms) : "never"} />
            <Field k="agreed" v={(ended.families ?? []).join(", ") || "—"} />
            <Field k="probes" v={String(ended.probes)} />
          </div>
          <div className="flex gap-2 mt-6">
            <button className="btn primary" onClick={reset}>New call</button>
          </div>
        </Frame>
      )}
    </main>
  );
}

function Steps({ stage }: { stage: Stage }) {
  const order: Stage[] = ["brief", "understanding", "permission", "calling", "summary"];
  const at = order.indexOf(stage);
  return (
    <div className="flex gap-1.5 items-center">
      {order.map((s, i) => (
        <div key={s} style={{
          width: i === at ? 20 : 7, height: 7, borderRadius: 999,
          background: i <= at ? "var(--primary)" : "var(--elevated)",
          transition: "width .25s, background .25s",
        }} />
      ))}
    </div>
  );
}

function Frame({ eyebrow, title, sub, children }: any) {
  return (
    <section className="card p-6 rise">
      <div className="eyebrow">{eyebrow}</div>
      <h1 className="text-[24px] font-extrabold tracking-tight mt-1">{title}</h1>
      {sub && <p className="text-[14px] mt-1.5 mb-5" style={{ color: "var(--muted)" }}>{sub}</p>}
      {children}
    </section>
  );
}

function Beat({ n, t, d }: { n: string; t: string; d: string }) {
  return (
    <div className="p-3 rounded-xl" style={{ background: "var(--surface-2)" }}>
      <div className="eyebrow">{n}</div>
      <div className="text-[13px] font-bold mt-0.5">{t}</div>
      <div className="text-[12px]" style={{ color: "var(--faint)" }}>{d}</div>
    </div>
  );
}

function Ctl({ label, hint, on, danger, disabled, onClick }: any) {
  return (
    <div className="flex flex-col items-center gap-1">
      <button className={`btn ${danger ? "danger" : ""}`} disabled={disabled} onClick={onClick}
        style={on && !danger ? { borderColor: "var(--primary)", color: "var(--primary)" } : {}}>
        {label}
      </button>
      <span className="text-[10px]" style={{ color: "var(--faint)" }}>{hint}</span>
    </div>
  );
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <div className="p-3 rounded-xl" style={{ background: "var(--surface-2)" }}>
      <div className="eyebrow">{k}</div>
      <div className="text-[14px] mt-0.5 break-words">{v}</div>
    </div>
  );
}

function Line({ f }: { f: Frame }) {
  if (f.type === "hello" || f.type === "detector" || f.type === "ended") return null;

  if (f.type === "hold")
    return <div className="text-[12px] my-2 breathe" style={{ color: "var(--faint)" }}>
      ♪ hold music
    </div>;

  if (f.type === "dtmf")
    return <div className="text-[12px] my-1.5" style={{ color: "var(--primary)" }}>
      {f.text} <span style={{ color: "var(--faint)" }}>— {f.note}</span>
    </div>;

  if (f.type === "summon" || f.type === "handoff") {
    const danger = f.type === "handoff";
    return (
      <div className="my-3 p-3 rounded-xl rise"
        style={{ background: "var(--surface-2)",
                 borderLeft: `3px solid ${danger ? "var(--destructive)" : "var(--success)"}` }}>
        <div className="text-[14px] font-bold"
          style={{ color: danger ? "var(--destructive)" : "var(--success)" }}>
          {danger ? "They asked you to verify — handing over" : "A person is on the line"}
        </div>
        <div className="text-[12px] mt-0.5" style={{ color: "var(--muted)" }}>{f.text}</div>
      </div>
    );
  }

  const v = VOICE[f.speaker] ?? { label: f.speaker ?? "", tone: "var(--muted)" };
  return (
    <div className="mb-3 rise">
      <div className="text-[10px] mb-0.5 flex gap-2 items-center flex-wrap">
        <span style={{ color: v.tone, fontWeight: 700 }}>{v.label}</span>
        {f.note?.includes("[disclosure]") &&
          <span className="px-1.5 py-0.5 rounded" style={{ background: "var(--elevated)", color: "var(--faint)" }}>
            says it is an AI
          </span>}
        {f.type === "probe" &&
          <span className="px-1.5 py-0.5 rounded" style={{ background: "var(--elevated)", color: "var(--warning)" }}>
            probe
          </span>}
      </div>
      <div className="text-[14px] leading-snug">{f.text}</div>
    </div>
  );
}
