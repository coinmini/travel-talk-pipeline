"""Apply Grok Build AI JSON into tags / match hints (no external API)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils import read_json, write_json


def _load_ai(ai_dir: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("broll_vlm.json", "narrative_plan.json", "highlight_scores.json"):
        p = ai_dir / name
        if p.exists():
            try:
                data = read_json(p)
                # skip pure templates that were never filled
                out[name] = data
            except Exception as e:
                print(f"  [warn] skip {name}: {e}")
    return out


def _vlm_filled(vlm: dict) -> bool:
    items = vlm.get("items") or []
    if not items:
        return False
    # filled if any item has tags or numeric score
    for it in items:
        if it.get("tags"):
            return True
        if isinstance(it.get("score"), (int, float)):
            return True
    return False


def run_ai_apply(
    work_dir: Path,
    cfg: dict[str, Any],
    *,
    broll_tags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ai_dir = work_dir / "ai"
    if not ai_dir.exists():
        print("  [skip] no work/ai/ — run ai_prepare first")
        return {"applied": False, "reason": "no_ai_dir"}

    if broll_tags is None:
        broll_tags = read_json(work_dir / "broll_tags.json")

    ai = _load_ai(ai_dir)
    vlm = ai.get("broll_vlm.json") or {}
    narrative = ai.get("narrative_plan.json") or {}
    highlights = ai.get("highlight_scores.json") or {}

    scores: dict[str, float] = {}
    if isinstance(highlights.get("scores"), dict):
        for k, v in highlights["scores"].items():
            try:
                scores[str(k)] = float(v)
            except (TypeError, ValueError):
                pass

    vlm_by_name: dict[str, dict] = {}
    if _vlm_filled(vlm):
        for it in vlm.get("items") or []:
            name = it.get("name")
            if name:
                vlm_by_name[name] = it
                if isinstance(it.get("score"), (int, float)):
                    scores[name] = float(it["score"])
    else:
        print("  [info] broll_vlm.json 尚未填写有效 tags/score，跳过 VLM 标签合并")

    # merge into broll list
    merged = []
    for b in broll_tags.get("broll") or []:
        name = b.get("name")
        heur = list(b.get("tags_auto") or b.get("tags") or [])
        it = vlm_by_name.get(name) or {}
        # VLM 已填则完全采用 VLM 标签（避免启发式误标「自拍」污染河马/空镜）
        if it.get("tags"):
            tags = []
            for t in it["tags"]:
                if t and t not in tags:
                    tags.append(t)
            if it.get("face") is True:
                for t in ("自拍", "出镜", "人脸"):
                    if t not in tags:
                        tags.append(t)
            rec_face = bool(it.get("face"))
        else:
            tags = list(heur)
            rec_face = "自拍" in tags or "出镜" in tags
        rec = dict(b)
        rec["tags"] = tags
        rec["tags_vlm"] = list(it.get("tags") or [])
        rec["tags_heuristic"] = heur
        rec["face"] = rec_face
        if name in scores:
            rec["highlight_score"] = scores[name]
        elif isinstance(it.get("score"), (int, float)):
            rec["highlight_score"] = float(it["score"])
        if it.get("note"):
            rec["ai_note"] = it["note"]
        if it.get("shake") is not None:
            rec["shake"] = it["shake"]
        merged.append(rec)

    broll_tags = dict(broll_tags)
    broll_tags["broll"] = merged
    broll_tags["ai_applied"] = {
        "vlm": bool(vlm_by_name),
        "scores": len(scores),
        "narrative": bool(
            narrative.get("force_face_texts") or narrative.get("prefer_broll_texts")
        ),
    }
    write_json(work_dir / "broll_tags.json", broll_tags)

    # narrative hints for match
    hints = {
        "source": "grok-build",
        "target_face_ratio": narrative.get("target_face_ratio"),
        "force_face_texts": list(narrative.get("force_face_texts") or []),
        "prefer_broll_texts": list(narrative.get("prefer_broll_texts") or []),
        "sequence_suggestion": list(narrative.get("sequence_suggestion") or []),
        "notes": narrative.get("notes") or "",
        "highlight_scores": scores,
    }
    # strip empty template
    if hints["force_face_texts"] or hints["prefer_broll_texts"] or scores or vlm_by_name:
        write_json(work_dir / "ai_match_hints.json", hints)
        applied = True
    else:
        print("  [warn] AI JSON 仍是空模板，未产生有效 hints")
        applied = bool(vlm_by_name)

    # optional: write sequence suggestion sidecar for user to copy into project.yaml
    if hints["sequence_suggestion"]:
        write_json(work_dir / "ai" / "sequence_suggestion_only.json", {
            "talk": {"sequence": hints["sequence_suggestion"]},
            "_note": "可复制到 project.yaml 的 talk.sequence",
        })

    result = {
        "applied": applied,
        "vlm_items": len(vlm_by_name),
        "scores": len(scores),
        "force_face_texts": len(hints["force_face_texts"]),
        "prefer_broll_texts": len(hints["prefer_broll_texts"]),
        "broll_tags": str(work_dir / "broll_tags.json"),
        "hints": str(work_dir / "ai_match_hints.json")
        if (work_dir / "ai_match_hints.json").exists()
        else "",
    }
    write_json(work_dir / "ai_apply.json", result)
    print(
        f"  applied AI: vlm={result['vlm_items']} scores={result['scores']} "
        f"force_face={result['force_face_texts']} prefer_broll={result['prefer_broll_texts']}"
    )
    return result
