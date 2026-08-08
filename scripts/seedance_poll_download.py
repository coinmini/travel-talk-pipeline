#!/usr/bin/env python3
"""Poll dreamina query_result and download mp4s by id_map.txt.

id_map lines: <submit_id> <clip_id>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def query(sid: str, download_dir: Path) -> tuple[dict | None, str]:
    cmd = [
        "dreamina",
        "query_result",
        f"--submit_id={sid}",
        f"--download_dir={download_dir}",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    text = (p.stdout or "") + (p.stderr or "")
    data = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("{"):
            try:
                data = json.loads(s)
            except Exception:
                pass
    return data, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", required=True, help="id_map.txt lines: submit_id name")
    ap.add_argument("--out", required=True, help="download dir")
    ap.add_argument("--rounds", type=int, default=36)
    ap.add_argument("--sleep", type=int, default=20)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    by_name: dict[str, str] = {}
    for line in Path(args.map).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        sid, name = parts
        by_name[name] = sid  # last wins
    jobs = [(sid, name) for name, sid in by_name.items()]
    if not jobs:
        print("no jobs in map")
        return 1

    for r in range(1, args.rounds + 1):
        print(f"===== round {r} {time.strftime('%H:%M:%S')} =====")
        done = 0
        for sid, name in jobs:
            target = out / f"{name}.mp4"
            if target.exists() and target.stat().st_size > 1000:
                print(f"{name}: ok ({target.stat().st_size})")
                done += 1
                continue
            data, _text = query(sid, out)
            gen = "?"
            if data:
                gen = str(data.get("gen_status") or data.get("status") or "?")
            print(f"{name}: {gen}")
            for c in sorted(out.glob(f"*{sid}*"), key=lambda p: p.stat().st_mtime, reverse=True):
                if c.suffix.lower() in {".mp4", ".mov", ".webm"} and not target.exists():
                    c.rename(target)
                    print(f"  renamed {c.name} -> {target.name}")
                    break
            if target.exists() and target.stat().st_size > 1000:
                done += 1
            elif gen.lower() in {"fail", "failed", "error"}:
                print("  FAIL:", json.dumps(data, ensure_ascii=False)[:400] if data else "")
        print(f"completed {done}/{len(jobs)}")
        if done >= len(jobs):
            break
        time.sleep(args.sleep)

    print("===== final =====")
    for p in sorted(out.glob("*.mp4")):
        print(p.name, p.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
