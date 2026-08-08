"""Stage 1: scan folder, classify talk / broll / final, write asset_index."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    classify_asset_name,
    ensure_dir,
    extract_thumbnail,
    media_info,
    talk_sort_key,
    write_json,
)


SKIP_DIR_NAMES = {"_analysis", "work", "export", "package", "__pycache__", ".git"}


def _iter_media(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIR_NAMES or part.startswith(".") for part in p.parts):
            continue
        if p.suffix.lower() in VIDEO_EXTS | IMAGE_EXTS:
            files.append(p)
    return files


def run_ingest(project_dir: Path, work_dir: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    ensure_dir(work_dir)
    thumb_dir = ensure_dir(work_dir / "thumbs")

    talk_keywords = [k.lower() for k in cfg.get("talk", {}).get("name_keywords", [])]

    assets: list[dict[str, Any]] = []
    for path in _iter_media(project_dir):
        rel = str(path.relative_to(project_dir))
        role = classify_asset_name(path.name)
        # also respect config keywords
        name_l = path.name.lower()
        if role == "broll" and any(k in name_l or k in path.stem.lower() for k in talk_keywords):
            # Chinese keywords may not lower well; check original too
            role = "talk"
        if any(k in path.name for k in (cfg.get("talk", {}).get("name_keywords") or [])):
            if role != "final":
                role = "talk"

        info = media_info(path)
        info["role"] = role
        info["relpath"] = rel
        thumb = thumb_dir / f"{role}_{path.stem[:30]}.jpg"
        try:
            extract_thumbnail(path, thumb)
            info["thumb"] = str(thumb)
        except Exception as e:
            info["thumb"] = ""
            info["thumb_error"] = str(e)
        info["tags"] = list((cfg.get("broll", {}).get("tags_override") or {}).get(path.name, []))
        assets.append(info)

    talks = sorted([a for a in assets if a["role"] == "talk"], key=lambda a: talk_sort_key(a["name"]))
    brolls = [a for a in assets if a["role"] == "broll"]
    finals = [a for a in assets if a["role"] == "final"]

    order = cfg.get("talk", {}).get("order") or []
    if order:
        by_name = {a["name"]: a for a in talks}
        by_stem = {a["stem"]: a for a in talks}
        ordered = []
        for key in order:
            if key in by_name:
                ordered.append(by_name[key])
            elif key in by_stem:
                ordered.append(by_stem[key])
        # append remaining
        used = {a["name"] for a in ordered}
        for a in talks:
            if a["name"] not in used:
                ordered.append(a)
        talks = ordered

    result = {
        "project_dir": str(project_dir.resolve()),
        "title": cfg.get("title") or project_dir.name,
        "counts": {
            "talk": len(talks),
            "broll": len(brolls),
            "final": len(finals),
            "total": len(assets),
        },
        "talk_total_sec": round(sum(a["duration"] for a in talks), 2),
        "broll_total_sec": round(sum(a["duration"] for a in brolls if a["kind"] == "video"), 2),
        "talk": talks,
        "broll": brolls,
        "final": finals,
        "all": assets,
    }
    write_json(work_dir / "asset_index.json", result)
    return result
