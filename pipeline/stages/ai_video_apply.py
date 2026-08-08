"""Stage: apply generated AI B-roll videos onto picture_plan."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ai_video.seedance import (
    apply_manifest_to_plan,
    default_ai_video_dir,
    load_manifest,
)
from ..utils import write_json


def run_ai_video_apply(
    work_dir: Path,
    cfg: dict[str, Any],
    picture_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ..utils import read_json

    work_dir = Path(work_dir)
    plan_path = work_dir / "picture_plan.json"
    if picture_plan is None:
        if not plan_path.exists():
            raise FileNotFoundError(f"Missing {plan_path}; run match first")
        picture_plan = read_json(plan_path)

    ai_dir = default_ai_video_dir(work_dir)
    man_yaml = ai_dir / "manifest.yaml"
    man_json = ai_dir / "manifest.json"
    if man_yaml.exists():
        manifest = load_manifest(man_yaml)
    elif man_json.exists():
        manifest = load_manifest(man_json)
    else:
        raise FileNotFoundError(
            f"Missing manifest under {ai_dir}. Run ai_video_prepare first."
        )

    # backup plan once
    bak = work_dir / "picture_plan.before_ai_video.json"
    if not bak.exists():
        write_json(bak, picture_plan)

    max_reuse = int((cfg.get("ai_video") or {}).get("max_reuse") or manifest.get("max_reuse") or 1)
    new_plan, report = apply_manifest_to_plan(
        picture_plan, manifest, ai_dir, max_reuse=max_reuse
    )
    write_json(plan_path, new_plan)
    out = {
        "picture_plan": str(plan_path),
        "backup": str(bak),
        "applied_count": len(report.get("applied") or []),
        "skipped_count": len(report.get("skipped") or []),
        "report": report,
    }
    write_json(work_dir / "ai_video_apply.json", out)

    print(f"  applied: {out['applied_count']}  skipped: {out['skipped_count']}")
    for a in report.get("applied") or []:
        print(
            f"    [{a['piece_index']}] {a.get('clip_id')} -> {Path(a['src']).name} "
            f"({a['src_start']}-{a['src_end']})"
        )
    for s in report.get("skipped") or []:
        print(f"    skip {s.get('clip')}: {s.get('reason')}")
    if out["applied_count"] == 0:
        print("  warn: nothing applied — generate videos then re-run ai_video_apply")
    return out
