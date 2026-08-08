"""AI B-roll video generation helpers (Seedance / Dreamina CLI)."""

from .seedance import (
    apply_manifest_to_plan,
    default_ai_video_dir,
    load_manifest,
    save_manifest,
    scaffold_manifest_from_plan,
)

__all__ = [
    "apply_manifest_to_plan",
    "default_ai_video_dir",
    "load_manifest",
    "save_manifest",
    "scaffold_manifest_from_plan",
]
