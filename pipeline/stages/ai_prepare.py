"""Prepare AI analysis pack for Grok Build (no external API)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..ai.schemas import TAG_VOCAB
from ..utils import ensure_dir, write_json, write_text


def _agent_prompt(tag_vocab: str) -> str:
    return f"""# Grok Build · 旅拍口播 AI 分析任务

你在 **当前 Grok Build 会话** 中完成分析（不要调外部 API）。
读 `work/ai/pack/` 里的材料，把结果写回 `work/ai/` 下三个 JSON（可只写有把握的）。

## 输入

| 文件 | 内容 |
|------|------|
| `pack/voiceover.txt` | 精剪后口播全文 |
| `pack/voiceover_segments.json` | 分句 + 时间轴 |
| `pack/broll_index.json` | B-roll 列表（name/duration/thumb） |
| `pack/thumbs/*.jpg` | 每条 B-roll 缩略图（请用 Read 看图） |
| `pack/current_tags.json` | 规则启发式标签（可作参考） |

## 输出（写到 work/ai/）

### 1) `broll_vlm.json` — 真标签 + 高光分

对每条 B-roll 填写 items，字段：name, tags, score(0-10), shake(0-1), face(bool), note。

- `tags` 尽量用词表：{tag_vocab}
- `score`：构图清晰、稳定、信息量大、适合盖画 → 高；糊/抖/无主体 → 低
- 人脸自拍为主 → face=true，tags 含 自拍/出镜

### 2) `narrative_plan.json` — 叙事与露脸

字段：target_face_ratio, force_face_texts[], prefer_broll_texts[], sequence_suggestion[], notes。

- force_face_texts：钩子、金句、CTA（子串匹配）
- prefer_broll_texts：景物描写句
- sequence_suggestion 可选；不写则沿用 project.yaml

### 3) `highlight_scores.json` — 可选

若 score 已写在 broll_vlm 可省略。格式：scores 映射 文件名→分数。

## 完成后

```bash
python run_pipeline.py run <项目> --from-stage apply_ai
```
"""


def run_ai_prepare(
    project_dir: Path,
    work_dir: Path,
    cfg: dict[str, Any],
    *,
    asset_index: dict[str, Any] | None = None,
    broll_tags: dict[str, Any] | None = None,
    voiceover: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from ..utils import read_json

    ai_dir = ensure_dir(work_dir / "ai")
    pack = ensure_dir(ai_dir / "pack")
    thumbs_dst = ensure_dir(pack / "thumbs")

    if asset_index is None and (work_dir / "asset_index.json").exists():
        asset_index = read_json(work_dir / "asset_index.json")
    if broll_tags is None and (work_dir / "broll_tags.json").exists():
        broll_tags = read_json(work_dir / "broll_tags.json")
    if voiceover is None and (work_dir / "voiceover.json").exists():
        voiceover = read_json(work_dir / "voiceover.json")

    asset_index = asset_index or {}
    broll_tags = broll_tags or {}
    voiceover = voiceover or {}

    # voiceover text + segments
    vo_text = voiceover.get("full_text") or ""
    if not vo_text and (work_dir / "vo" / "voiceover.txt").exists():
        vo_text = (work_dir / "vo" / "voiceover.txt").read_text(encoding="utf-8")
    write_text(pack / "voiceover.txt", vo_text)

    segs = []
    for s in voiceover.get("timeline") or []:
        segs.append(
            {
                "text": s.get("text"),
                "timeline_start": s.get("timeline_start"),
                "timeline_end": s.get("timeline_end"),
                "duration": s.get("duration"),
                "src_name": s.get("src_name"),
            }
        )
    write_json(pack / "voiceover_segments.json", {"segments": segs})

    # broll index + copy thumbs
    broll_list = broll_tags.get("broll") or asset_index.get("broll") or []
    index = []
    for b in broll_list:
        name = b.get("name")
        thumb = Path(b.get("thumb") or "")
        thumb_rel = ""
        if thumb.exists():
            dest = thumbs_dst / f"{Path(name).stem[:40]}.jpg"
            try:
                shutil.copy2(thumb, dest)
                thumb_rel = str(dest.relative_to(pack))
            except Exception:
                pass
        index.append(
            {
                "name": name,
                "path": b.get("path"),
                "duration": b.get("duration"),
                "width": b.get("width"),
                "height": b.get("height"),
                "tags_heuristic": b.get("tags") or [],
                "thumb": thumb_rel,
            }
        )
    write_json(pack / "broll_index.json", {"count": len(index), "items": index})
    write_json(
        pack / "current_tags.json",
        {
            "items": [
                {"name": b.get("name"), "tags": b.get("tags") or []} for b in broll_list
            ]
        },
    )

    # empty templates if missing
    for fname, template in [
        (
            "broll_vlm.json",
            {
                "version": 1,
                "source": "grok-build",
                "items": [
                    {
                        "name": it["name"],
                        "tags": [],
                        "score": None,
                        "shake": None,
                        "face": None,
                        "note": "",
                    }
                    for it in index
                ],
                "_todo": "Grok Build: 看 pack/thumbs 填 tags/score",
            },
        ),
        (
            "narrative_plan.json",
            {
                "version": 1,
                "source": "grok-build",
                "target_face_ratio": (cfg.get("match") or {}).get(
                    "target_face_ratio", 0.2
                ),
                "force_face_texts": [],
                "prefer_broll_texts": [],
                "sequence_suggestion": [],
                "notes": "",
                "_todo": "Grok Build: 根据 voiceover.txt 填 force_face / prefer_broll",
            },
        ),
        (
            "highlight_scores.json",
            {
                "version": 1,
                "source": "grok-build",
                "scores": {},
                "_todo": "可选；score 已写在 broll_vlm 则可忽略",
            },
        ),
    ]:
        path = ai_dir / fname
        if not path.exists():
            write_json(path, template)

    prompt = _agent_prompt(tag_vocab="、".join(TAG_VOCAB))
    write_text(ai_dir / "AGENT_PROMPT.md", prompt)
    write_text(
        ai_dir / "README.md",
        f"""# AI 分析包（Grok Build）

1. 在 Grok Build 里打开并阅读 `AGENT_PROMPT.md`
2. 查看 `pack/thumbs/` 缩略图 + `pack/voiceover.txt`
3. 填写：
   - `broll_vlm.json`
   - `narrative_plan.json`
   - （可选）`highlight_scores.json`
4. 运行：

```bash
python run_pipeline.py run {project_dir.name} --from-stage apply_ai
```

当前 B-roll 数量：{len(index)}
口播句数：{len(segs)}
""",
    )

    result = {
        "ai_dir": str(ai_dir),
        "pack_dir": str(pack),
        "broll_count": len(index),
        "vo_segments": len(segs),
        "agent_prompt": str(ai_dir / "AGENT_PROMPT.md"),
        "outputs": {
            "broll_vlm": str(ai_dir / "broll_vlm.json"),
            "narrative_plan": str(ai_dir / "narrative_plan.json"),
            "highlight_scores": str(ai_dir / "highlight_scores.json"),
        },
    }
    write_json(work_dir / "ai_prepare.json", result)
    print(f"  AI pack ready: {ai_dir}")
    print(f"  → Grok Build 请读: {ai_dir / 'AGENT_PROMPT.md'}")
    print(f"  → 填完后: python run_pipeline.py run {project_dir.name} --from-stage apply_ai")
    return result
