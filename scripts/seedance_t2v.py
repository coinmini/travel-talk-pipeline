#!/usr/bin/env python3
"""Submit Seedance 2.5 text-to-video jobs from work/ai_video manifest.

Requires local Dreamina CLI (`dreamina`) after `dreamina login`.
Does NOT store or read any API keys from the repo.

Usage:
  python scripts/seedance_t2v.py --work path/to/project/work
  python scripts/seedance_t2v.py --work path/to/project/work --only t01
  python scripts/seedance_t2v.py --work path/to/project/work --poll 0 --force
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _load_manifest(ai_dir: Path) -> dict:
    for name in ("manifest.yaml", "manifest.yml", "manifest.json"):
        p = ai_dir / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in {".yaml", ".yml"}:
            import yaml

            return yaml.safe_load(text) or {}
        return json.loads(text)
    raise FileNotFoundError(f"No manifest under {ai_dir}")


def _which_dreamina() -> str:
    from shutil import which

    exe = which("dreamina")
    if not exe:
        raise SystemExit(
            "dreamina not found. Install: curl -fsSL https://jimeng.jianying.com/cli | bash\n"
            "Then: export PATH=\"$HOME/.local/bin:$PATH\" && dreamina login"
        )
    return exe


def _parse_submit_id(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("{"):
            try:
                d = json.loads(s)
                sid = d.get("submit_id") or ""
                if sid:
                    return sid
            except Exception:
                pass
    m = re.search(r"submit_id[\"\s:=]+([0-9a-fA-F-]{8,})", text)
    return m.group(1) if m else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Seedance text2video from ai_video manifest")
    ap.add_argument("--work", required=True, help="project work directory")
    ap.add_argument("--only", default="", help="only clips whose id contains this string")
    ap.add_argument("--poll", type=int, default=0, help="dreamina --poll seconds (0=submit only)")
    ap.add_argument("--force", action="store_true", help="resubmit even if output mp4 exists")
    ap.add_argument("--model", default="", help="override model_version")
    ap.add_argument("--res", default="", help="override video_resolution")
    ap.add_argument("--duration", type=int, default=0, help="override duration")
    ap.add_argument("--ratio", default="", help="override ratio")
    args = ap.parse_args()

    work = Path(args.work).resolve()
    ai_dir = work / "ai_video"
    if not ai_dir.is_dir():
        raise SystemExit(f"Missing {ai_dir}. Run: python run_pipeline.py run <proj> --stage ai_video_prepare")

    manifest = _load_manifest(ai_dir)
    model = args.model or manifest.get("model_version") or "seedance2.5"
    res = args.res or manifest.get("video_resolution") or "720p"
    dur = int(args.duration or manifest.get("duration") or 5)
    ratio = args.ratio or manifest.get("ratio") or "9:16"

    dreamina = _which_dreamina()
    videos_dir = ai_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    jobs_log = ai_dir / "jobs.jsonl"
    id_map = ai_dir / "id_map.txt"

    # credit check (non-fatal if fails)
    try:
        subprocess.run([dreamina, "user_credit"], check=False)
    except Exception:
        pass

    clips = list(manifest.get("clips") or [])
    if not clips:
        raise SystemExit("manifest.clips is empty")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with jobs_log.open("a", encoding="utf-8") as jl:
        jl.write(
            json.dumps(
                {
                    "event": "batch_start",
                    "ts": ts,
                    "model": model,
                    "res": res,
                    "dur": dur,
                    "ratio": ratio,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    submitted = 0
    for clip in clips:
        cid = str(clip.get("id") or "")
        if args.only and args.only not in cid:
            continue
        prompt_rel = clip.get("prompt_file") or f"prompts/{cid}.txt"
        prompt_path = ai_dir / prompt_rel
        out_rel = clip.get("output_file") or f"videos/{cid}.mp4"
        out_path = ai_dir / out_rel
        if out_path.exists() and not args.force:
            print(f"SKIP exists: {out_path}")
            continue
        if not prompt_path.exists():
            print(f"SKIP missing prompt: {prompt_path}", file=sys.stderr)
            continue

        prompt = prompt_path.read_text(encoding="utf-8").strip()
        if not prompt:
            print(f"SKIP empty prompt: {prompt_path}", file=sys.stderr)
            continue

        print(f"\n======== {cid} (text2video structured) ========")
        print(f"prompt_file: {prompt_path}")
        print(f"prompt_chars: {len(prompt)}")
        cmd = [
            dreamina,
            "text2video",
            f"--prompt={prompt}",
            f"--duration={dur}",
            f"--ratio={ratio}",
            f"--video_resolution={res}",
            f"--model_version={model}",
            f"--poll={args.poll}",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        out = (p.stdout or "") + (p.stderr or "")
        print(out)
        sid = _parse_submit_id(out)
        with jobs_log.open("a", encoding="utf-8") as jl:
            jl.write(
                json.dumps(
                    {
                        "event": "submit",
                        "id": cid,
                        "submit_id": sid,
                        "rc": p.returncode,
                        "prompt_file": str(prompt_rel),
                        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        if sid:
            with id_map.open("a", encoding="utf-8") as mf:
                mf.write(f"{sid} {cid}\n")
            submitted += 1
            # try immediate download rename if poll completed
            if args.poll > 0:
                subprocess.run(
                    [
                        dreamina,
                        "query_result",
                        f"--submit_id={sid}",
                        f"--download_dir={videos_dir}",
                    ],
                    check=False,
                )
                for f in videos_dir.glob(f"*{sid}*"):
                    if f.suffix.lower() in {".mp4", ".mov", ".webm"}:
                        target = videos_dir / f"{cid}.mp4"
                        if not target.exists():
                            f.rename(target)
                            print(f"saved {target}")

    print(f"\nSubmitted: {submitted}")
    print(f"id_map: {id_map}")
    print(
        "Poll/download:\n"
        f"  python scripts/seedance_poll_download.py --map {id_map} --out {videos_dir}"
    )
    print(
        "Then apply:\n"
        f"  python run_pipeline.py run <project> --from-stage ai_video_apply"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
