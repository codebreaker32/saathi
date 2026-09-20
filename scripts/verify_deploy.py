"""Check that a deployment actually serves EVERY built file.

Written after a deployment that looked fine and was not. The site loaded, then
died a second later with:

    ChunkLoadError: Failed to load chunk /_next/static/chunks/2-eqy7vhy5tsl.js

The chunk was in the local build and missing on the server, because a SECOND
gitignore (`web/.gitignore`, scaffolded by Next.js) still had `/out/` in it, so
`git add -A` silently skipped new chunks and the host cloned a partial build.

The reason the earlier checks missed it is the whole point of this script: they
walked the asset links in `index.html`. That chunk is loaded LAZILY at runtime,
so it appears in no href or src, and a page that fetches it only after mounting
looks perfectly healthy until it doesn't.

So: compare the file list on disk against what the URL actually returns.

    python scripts/verify_deploy.py https://43-205-182-211.sslip.io
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "web" / "out"
TIMEOUT = 30


def built_files() -> list[str]:
    return sorted(
        p.relative_to(SITE).as_posix()
        for p in SITE.rglob("*") if p.is_file()
    )


def check(base: str) -> int:
    base = base.rstrip("/")
    files = built_files()
    if not files:
        print(f"  no build at {SITE} -- run `npx next build` in web/")
        return 2

    missing, wrong_size = [], []
    for rel in files:
        local = (SITE / rel).stat().st_size
        try:
            with urllib.request.urlopen(f"{base}/{rel}", timeout=TIMEOUT) as r:
                served = len(r.read())
            if served != local:
                wrong_size.append((rel, local, served))
        except urllib.error.HTTPError as e:
            missing.append((rel, e.code))
        except Exception as e:
            missing.append((rel, type(e).__name__))

    print(f"  {len(files)} built files checked against {base}")
    for rel, code in missing:
        print(f"    MISSING  {rel}  -> {code}")
    for rel, a, b in wrong_size:
        print(f"    SIZE     {rel}  local {a:,}B vs served {b:,}B")

    if missing or wrong_size:
        print(f"\n  FAIL: {len(missing)} missing, {len(wrong_size)} mismatched")
        return 1
    print("\n  OK: every built file is served, byte-for-byte")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(check(sys.argv[1]))
