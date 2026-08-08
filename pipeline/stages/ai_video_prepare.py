"""Stage: prepare AI B-roll video generation package (Seedance / Dreamina)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..ai_video.seedance import default_ai_video_dir, scaffold_manifest_from_plan
from ..utils import write_json


def run_ai_video_prepare(
    work_dir: Path,
    cfg: dict[str, Any],
    picture_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scaffold work/ai_video from picture_plan for text-to-video B-roll fill."""
    from ..utils import read_json

    work_dir = Path(work_dir)
    if picture_plan is None:
        plan_path = work_dir / "picture_plan.json"
        if not plan_path.exists():
            raise FileNotFoundError(
                f"Missing {plan_path}; run match stage first"
            )
        picture_plan = read_json(plan_path)

    ai_dir = default_ai_video_dir(work_dir)
    av = cfg.get("ai_video") or {}
    piece_indices = av.get("piece_indices")
    text_contains = av.get("text_contains")

    manifest = scaffold_manifest_from_plan(
        picture_plan,
        ai_dir,
        cfg=cfg,
        piece_indices=piece_indices,
        text_contains=text_contains,
    )

    result = {
        "ai_video_dir": str(ai_dir),
        "manifest": str(ai_dir / "manifest.yaml"),
        "clip_count": len(manifest.get("clips") or []),
        "model_version": manifest.get("model_version"),
        "next": [
            f"Edit prompts under {ai_dir / 'prompts'}",
            f"Prune clips in {ai_dir / 'manifest.yaml'} (1 clip = 1 piece, no reuse)",
            "dreamina login && python scripts/seedance_t2v.py --work " + str(work_dir),
            "python run_pipeline.py run <project> --from-stage ai_video_apply",
        ],
    }
    write_json(work_dir / "ai_video_prepare.json", result)
    print(f"  ai_video package: {ai_dir}")
    print(f"  clips scaffolded: {result['clip_count']}")
    print("  next: edit structured prompts → scripts/seedance_t2v.py → ai_video_apply")
    return result
