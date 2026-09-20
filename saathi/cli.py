"""python -m saathi.cli run --scenario voicebot_warm"""

from __future__ import annotations

import argparse
import sys

from saathi.playbook import load_all
from saathi.metrics import summarise
from saathi.simulate import SCENARIO_DIR, load, run

ACTOR_W = 8


def hhmmss(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def show(name: str, goal: str | None = None) -> int:
    scenario = load(name)
    goal = goal or scenario.get("goal", "")
    out = run(scenario, goal)
    print(f"\n\033[1m{name}\033[0m   goal: {goal}\n" + "-" * 78)
    for b in out.beats:
        label, colour = {"saathi": ("saathi", "36"), "them": ("them", "33"),
                         "detector": ("detect", "35"),
                         "system": ("system", "90")}.get(b.actor, (b.actor, "0"))
        actor = f"\033[{colour}m{label}\033[0m"
        pad = " " * max(1, ACTOR_W - len(label))
        note = f"  \033[90m{b.note}\033[0m" if b.note else ""
        print(f"  {hhmmss(b.t_ms)}  {actor}{pad}{b.text}{note}")

    print("-" * 78)
    verdict = ("fetched at " + hhmmss(out.fetched_at_ms) +
               f" on {', '.join(out.fetch_families)}"
               if out.fetched_at_ms else "never fetched")
    print(f"  truth: {out.truth:6s}  {verdict}")
    if out.handed_off_at_ms:
        print(f"  handed over to the user at {hhmmss(out.handed_off_at_ms)}")
    if out.time_to_fetch_ms is not None:
        print(f"  time from the human's first word: "
              f"{out.time_to_fetch_ms / 1000:.1f}s")
    if out.false_fetch:
        print("  \033[31mFALSE FETCH -- this is the failure that breaks the product\033[0m")
        return 1
    if out.missed_human:
        print("  \033[33mmissed a human (recoverable: the rep hangs up, we retry)\033[0m")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="saathi")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one scenario end to end")
    r.add_argument("--scenario", required=True)
    r.add_argument("--goal", default=None)

    a = sub.add_parser("all", help="run every scenario")
    a.add_argument("--goal", default=None)

    t = sub.add_parser("table", help="run every scenario and print the scoreboard")
    t.add_argument("--goal", default=None)

    v = sub.add_parser("voice", help="render a call to a .wav you can listen to")
    v.add_argument("--scenario", required=True)
    v.add_argument("--out", default=None)
    v.add_argument("--profile", default="saathi")

    sub.add_parser("playbooks", help="list playbooks and what they will be asked for")

    args = ap.parse_args(argv)

    if args.cmd == "run":
        return show(args.scenario, args.goal)
    if args.cmd == "voice":
        from pathlib import Path as _P
        from saathi import voice as V
        if not V.available(args.profile):
            print(f"AWS profile '{args.profile}' is not reachable; run: aws login")
            return 2
        sc = load(args.scenario)
        out = run(sc, sc.get("goal", ""))
        print(f"\n  synthesising {args.scenario} ...", flush=True)
        chunks, meta = V.render_call(out, profile=args.profile)
        dest = _P(args.out or f"{args.scenario}.wav")
        V.write_wav(dest, chunks)
        print(f"  wrote {dest}  ({meta['seconds']}s of audio)")
        print(f"  call clock     {meta['call_clock_s']}s of simulated call")
        if meta["real_wait_s"]:
            ratio = meta["real_wait_s"] / max(meta["rendered_wait_s"], 0.1)
            print(f"  waiting        {meta['real_wait_s']}s endured, "
                  f"{meta['rendered_wait_s']}s played  ({ratio:.0f}x compressed)")
        drift = meta["accounted_s"] - meta["call_clock_s"]
        if abs(drift) > 2:
            print(f"  speech drift   real speech runs {drift:+.1f}s vs the scenario's"
                  f" estimate  (Polly is ground truth, the yaml is a guess)")
        print("  every spoken word plays at full length; only waiting is compressed.\n")
        return 0

    if args.cmd == "table":
        named = []
        for path in sorted(SCENARIO_DIR.glob("*.yaml")):
            sc = load(path.stem)
            named.append((path.stem, run(sc, args.goal or sc.get("goal", ""))))
        summary = summarise(named)
        print("\n\033[1mSaathi -- is there a human on this line?\033[0m\n")
        print(summary.render())
        print()
        return 1 if summary.false_fetches else 0

    if args.cmd == "all":
        rc = 0
        for path in sorted(SCENARIO_DIR.glob("*.yaml")):
            rc |= show(path.stem, args.goal)
        return rc
    for pb in load_all().values():
        print(f"\n\033[1m{pb.id}\033[0m  ({pb.company})  -- {pb.problem}")
        print(f"  finds your account by: {pb.lookup_key}")
        print(f"  may state : {', '.join(f.field for f in pb.safe_fields())}")
        print(f"  never has : {', '.join(f.field for f in pb.never_fields())}"
              "   <- you will be asked for these yourself")
    return 0


if __name__ == "__main__":
    sys.exit(main())
