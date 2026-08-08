"""Seedance / Dreamina CLI integration helpers (no API keys stored).

Auth is handled by the local `dreamina` CLI (`dreamina login`).
This module only builds manifests, patches picture_plan, and shells out
when scripts explicitly call generation.
"""

from __future__ import annotations

import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..utils import ensure_dir, media_info, read_json, write_json

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


STRUCTURED_PROMPT_SKELETON = """【基础设定】
画幅：竖屏 9:16，手机竖拍构图
拍摄感：手机主摄随手拍的 Vlog / 现场 B-roll，轻微手持呼吸感，真实景深，自然曝光
时代感：2020 年代当下现实，不要未来感、不要科幻、不要电影棚大片光
用途：口播盖画空镜，画面里的人不要对镜头说话，不要口播口型
场景：{scene}

【氛围与画质】
风格核心：纪实、朴素、可信的现实场景
视觉基调：手机直出偏自然
色彩与影调：真实白平衡，不泛霓虹蓝紫，不赛博
光线：现场真实光源

【画面内容】
分镜 0:00-0:05
景别：中景
构图：主体清晰，前景可有遮挡增强实拍感
运镜手法：手持轻微推进或横移，不要剧烈甩镜
画面内容：{action}

【负面要求】
不要全息投影、悬浮屏幕、发光数据流、赛博朋克、科幻未来馆、
不要字幕条、大标题文字、对镜头说话、剧烈甩镜。
{extra_negative}
"""


def default_ai_video_dir(work_dir: Path) -> Path:
    return ensure_dir(work_dir / "ai_video")


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML required to load yaml manifest")
        data = yaml.safe_load(text) or {}
    else:
        data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a mapping: {path}")
    return data


def save_manifest(path: Path, data: dict[str, Any]) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML required to write yaml manifest")
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    else:
        write_json(path, data)


def _slug(text: str, n: int = 24) -> str:
    s = re.sub(r"\s+", "_", (text or "").strip())
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", s)
    return (s[:n] or "clip").strip("_")


def scaffold_manifest_from_plan(
    picture_plan: dict[str, Any],
    ai_dir: Path,
    *,
    cfg: dict[str, Any] | None = None,
    piece_indices: list[int] | None = None,
    text_contains: list[str] | None = None,
) -> dict[str, Any]:
    """Create work/ai_video skeleton: manifest + prompt stubs for selected pieces.

    Selection priority:
    1) explicit piece_indices
    2) text_contains substrings
    3) config ai_video.select
    4) all broll pieces (user should prune)
    """
    cfg = cfg or {}
    av = cfg.get("ai_video") or {}
    ai_dir = ensure_dir(ai_dir)
    prompts_dir = ensure_dir(ai_dir / "prompts")
    videos_dir = ensure_dir(ai_dir / "videos")

    timeline = list(picture_plan.get("picture_timeline") or [])
    selected: list[int] = []

    if piece_indices is not None:
        selected = [i for i in piece_indices if 0 <= i < len(timeline)]
    elif text_contains:
        needles = [t for t in text_contains if t]
        for i, p in enumerate(timeline):
            text = str(p.get("text") or "")
            if any(n in text for n in needles):
                selected.append(i)
    else:
        sel = av.get("select") or {}
        if sel.get("piece_indices"):
            selected = [int(i) for i in sel["piece_indices"] if 0 <= int(i) < len(timeline)]
        elif sel.get("text_contains"):
            needles = list(sel["text_contains"])
            for i, p in enumerate(timeline):
                text = str(p.get("text") or "")
                if any(n in text for n in needles):
                    selected.append(i)
        else:
            # default: broll only, for manual pruning
            selected = [i for i, p in enumerate(timeline) if p.get("type") == "broll"]

    # unique preserve order
    seen: set[int] = set()
    ordered: list[int] = []
    for i in selected:
        if i not in seen:
            seen.add(i)
            ordered.append(i)

    clips: list[dict[str, Any]] = []
    for n, idx in enumerate(ordered, start=1):
        p = timeline[idx]
        text = str(p.get("text") or "")
        cid = f"t{n:02d}_{_slug(text)}"
        prompt_rel = f"prompts/{cid}.txt"
        prompt_path = ai_dir / prompt_rel
        if not prompt_path.exists():
            scene = f"与口播语义相关的真实场景：{text}" if text else "真实可拍的现场空镜"
            action = f"围绕「{text}」做 5 秒可裁切的手机实拍动作" if text else "自然现场动作"
            extra = ""
            if av.get("style_negative"):
                extra = "额外禁止：" + str(av["style_negative"])
            prompt_path.write_text(
                STRUCTURED_PROMPT_SKELETON.format(
                    scene=scene, action=action, extra_negative=extra
                ),
                encoding="utf-8",
            )
        clips.append(
            {
                "id": cid,
                "piece_index": idx,
                "timeline_start": p.get("timeline_start"),
                "timeline_end": p.get("timeline_end"),
                "duration": p.get("duration"),
                "vo_text": text,
                "prompt_file": prompt_rel,
                "output_file": f"videos/{cid}.mp4",
                "status": "prompt_ready",
            }
        )

    manifest = {
        "provider": av.get("provider") or "seedance_cli",
        "model_version": av.get("model_version") or "seedance2.5",
        "ratio": av.get("ratio") or "9:16",
        "video_resolution": av.get("video_resolution") or "720p",
        "duration": int(av.get("duration") or 5),
        "style": av.get("style")
        or "phone_documentary_realism",  # 手机实拍 / 现实向
        "max_reuse": 1,
        "notes": (
            "1) 编辑 prompts/*.txt 为 skill 结构（【基础设定】…）\n"
            "2) python scripts/seedance_t2v.py --work <project>/work\n"
            "3) python run_pipeline.py run <project> --from-stage ai_video_apply"
        ),
        "clips": clips,
        "paths": {
            "prompts_dir": str(prompts_dir),
            "videos_dir": str(videos_dir),
        },
    }
    save_manifest(ai_dir / "manifest.yaml", manifest)
    # also json for tools that prefer json
    save_manifest(ai_dir / "manifest.json", manifest)

    readme = ai_dir / "README.md"
    readme.write_text(
        "# AI Video B-roll（Seedance / Dreamina CLI）\n\n"
        "本目录由 `ai_video_prepare` 生成。\n\n"
        "1. 精简 `manifest.yaml` 的 `clips`（一条 clip 对应 picture_plan 一个 piece，**禁止复用**）\n"
        "2. 按 skill 写好 `prompts/*.txt` 结构化 prompt\n"
        "3. `dreamina login` 后运行：\n"
        "   `python scripts/seedance_t2v.py --work <本项目 work 目录>`\n"
        "4. `python run_pipeline.py run <项目> --from-stage ai_video_apply`\n"
        "5. 再 `assemble` / `package`\n\n"
        "登录与密钥由本机 `dreamina` CLI 管理，**不要**把 cookie/token 写进仓库。\n",
        encoding="utf-8",
    )
    return manifest


def apply_manifest_to_plan(
    picture_plan: dict[str, Any],
    manifest: dict[str, Any],
    ai_dir: Path,
    *,
    max_reuse: int = 1,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Patch picture_plan so listed pieces use generated videos. Enforce max_reuse."""
    plan = deepcopy(picture_plan)
    timeline = list(plan.get("picture_timeline") or [])
    clips = list(manifest.get("clips") or [])
    max_reuse = int(manifest.get("max_reuse") or max_reuse or 1)

    usage: dict[str, int] = {}
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for clip in clips:
        idx = clip.get("piece_index")
        if idx is None:
            skipped.append({"clip": clip.get("id"), "reason": "missing piece_index"})
            continue
        idx = int(idx)
        if idx < 0 or idx >= len(timeline):
            skipped.append({"clip": clip.get("id"), "reason": f"piece_index out of range: {idx}"})
            continue

        out_rel = clip.get("output_file") or f"videos/{clip.get('id')}.mp4"
        out_path = (ai_dir / out_rel).resolve()
        if not out_path.exists():
            # also accept basename in videos/
            alt = ai_dir / "videos" / Path(out_rel).name
            if alt.exists():
                out_path = alt.resolve()
            else:
                skipped.append(
                    {
                        "clip": clip.get("id"),
                        "reason": f"video missing: {out_path}",
                    }
                )
                continue

        key = str(out_path)
        used = usage.get(key, 0)
        if used >= max_reuse:
            skipped.append(
                {
                    "clip": clip.get("id"),
                    "reason": f"max_reuse={max_reuse} exceeded for {out_path.name}",
                }
            )
            continue

        piece = timeline[idx]
        dur = float(piece.get("duration") or clip.get("duration") or 3.0)
        info = {}
        try:
            info = media_info(out_path)
        except Exception:
            info = {}
        src_len = float(info.get("duration") or manifest.get("duration") or 5.0)
        ss = float(clip.get("src_start") or 0.0)
        if ss + dur > src_len + 0.05:
            ss = max(0.0, src_len - dur)

        piece["type"] = "broll"
        piece["src"] = str(out_path)
        piece["src_name"] = out_path.name
        piece["src_start"] = round(ss, 3)
        piece["src_end"] = round(ss + dur, 3)
        piece["needs_freeze"] = False
        piece["ai_source"] = "seedance_cli"
        piece["ai_clip_id"] = clip.get("id")
        tags = clip.get("tags") or ["ai_video", "generated"]
        piece["tags"] = list(tags)
        piece["match_keys"] = list(clip.get("match_keys") or tags)
        piece["highlight_score"] = float(clip.get("highlight_score") or 9.0)

        usage[key] = used + 1
        applied.append(
            {
                "piece_index": idx,
                "clip_id": clip.get("id"),
                "src": str(out_path),
                "src_start": piece["src_start"],
                "src_end": piece["src_end"],
                "vo_text": piece.get("text"),
            }
        )

    plan["picture_timeline"] = timeline
    report = {
        "applied": applied,
        "skipped": skipped,
        "usage": {Path(k).name: v for k, v in usage.items()},
        "max_reuse": max_reuse,
    }
    return plan, report


def which_dreamina() -> str | None:
    return shutil.which("dreamina")
