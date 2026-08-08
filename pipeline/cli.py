"""CLI: run travel-talk pipeline stages."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .config import init_project_yaml, load_project_config
from .utils import ensure_dir, read_json, write_json


# 核心阶段 + 可选 AI
# - ai_prepare / apply_ai: Grok Build 会话填 JSON（标签/叙事）
# - ai_video_prepare / ai_video_apply: Seedance/Dreamina CLI 文生视频盖 B-roll
STAGES = [
    "ingest",
    "transcribe",
    "clean_vo",
    "tag_broll",
    "ai_prepare",  # 导出分析包
    "apply_ai",  # 消费 work/ai/*.json
    "match",
    "ai_video_prepare",  # 导出 work/ai_video 结构化 prompt 包
    "ai_video_apply",  # 把生成视频写回 picture_plan（禁止复用）
    "assemble",
    "package",
]

STAGE_FILES = {
    "ingest": "asset_index.json",
    "transcribe": "transcripts.json",
    "clean_vo": "voiceover.json",
    "tag_broll": "broll_tags.json",
    "ai_prepare": "ai_prepare.json",
    "apply_ai": "ai_apply.json",
    "match": "picture_plan.json",
    "ai_video_prepare": "ai_video_prepare.json",
    "ai_video_apply": "ai_video_apply.json",
    "assemble": "assemble.json",
    "package": "package.json",
}

# 默认全流程不自动跑的可选阶段
OPTIONAL_STAGES = {
    "ai_prepare",
    "apply_ai",
    "ai_video_prepare",
    "ai_video_apply",
}


def _work_dir(project_dir: Path, work: str | None) -> Path:
    if work:
        return ensure_dir(Path(work))
    return ensure_dir(project_dir / "work")


def _load_stage(work_dir: Path, name: str) -> Any:
    path = work_dir / STAGE_FILES[name]
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}; run prior stages first")
    return read_json(path)


def _ai_json_ready(work_dir: Path) -> bool:
    """True if Grok Build has filled at least one meaningful AI file."""
    ai = work_dir / "ai"
    vlm = ai / "broll_vlm.json"
    plan = ai / "narrative_plan.json"
    if not ai.exists():
        return False
    if vlm.exists():
        data = read_json(vlm)
        for it in data.get("items") or []:
            if it.get("tags") or isinstance(it.get("score"), (int, float)):
                return True
    if plan.exists():
        data = read_json(plan)
        if data.get("force_face_texts") or data.get("prefer_broll_texts"):
            return True
    return False


def run_pipeline(
    project_dir: Path,
    *,
    stages: list[str] | None = None,
    config_path: Path | None = None,
    work: str | None = None,
    force_transcribe: bool = False,
    with_ai: bool = False,
) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project dir not found: {project_dir}")

    cfg = load_project_config(project_dir, config_path)
    work_dir = _work_dir(project_dir, work)
    write_json(work_dir / "resolved_config.json", cfg)

    if with_ai and stages is None:
        # 默认全流程 + 标签 AI：prepare 后若已有填写则 apply，否则停在 prepare 提示
        # （不含 Seedance 视频生成；视频用 --from-stage ai_video_prepare）
        stages = [s for s in STAGES if s not in ("ai_video_prepare", "ai_video_apply")]
    selected = stages or [s for s in STAGES if s not in OPTIONAL_STAGES]

    for s in selected:
        if s not in STAGES:
            raise ValueError(f"Unknown stage: {s}. Choose from {STAGES}")

    results: dict[str, Any] = {"project_dir": str(project_dir), "work_dir": str(work_dir)}
    n_total = len(selected)
    step = 0

    def banner(title: str) -> None:
        nonlocal step
        step += 1
        print(f"\n==> [{step}/{n_total}] {title}")

    if "ingest" in selected:
        from .stages.ingest import run_ingest

        banner("ingest")
        results["ingest"] = run_ingest(project_dir, work_dir, cfg)

    if "transcribe" in selected:
        from .stages.transcribe import run_transcribe

        banner("transcribe")
        asset_index = results.get("ingest") or _load_stage(work_dir, "ingest")
        results["transcribe"] = run_transcribe(
            asset_index, work_dir, cfg, force=force_transcribe
        )

    if "clean_vo" in selected:
        from .stages.clean_vo import run_clean_vo

        banner("clean_vo")
        transcripts = results.get("transcribe") or _load_stage(work_dir, "transcribe")
        results["clean_vo"] = run_clean_vo(transcripts, work_dir, cfg)

    if "tag_broll" in selected:
        from .stages.tag_broll import run_tag_broll

        banner("tag_broll")
        asset_index = results.get("ingest") or _load_stage(work_dir, "ingest")
        results["tag_broll"] = run_tag_broll(asset_index, work_dir, cfg)

    if "ai_prepare" in selected:
        from .stages.ai_prepare import run_ai_prepare

        banner("ai_prepare (Grok Build pack)")
        results["ai_prepare"] = run_ai_prepare(
            project_dir,
            work_dir,
            cfg,
            asset_index=results.get("ingest")
            or (
                _load_stage(work_dir, "ingest")
                if (work_dir / "asset_index.json").exists()
                else None
            ),
            broll_tags=results.get("tag_broll")
            or (
                _load_stage(work_dir, "tag_broll")
                if (work_dir / "broll_tags.json").exists()
                else None
            ),
            voiceover=results.get("clean_vo")
            or (
                _load_stage(work_dir, "clean_vo")
                if (work_dir / "voiceover.json").exists()
                else None
            ),
        )
        # --ai 全流程：若还没填 JSON，停在这里等 Grok Build
        if with_ai and not _ai_json_ready(work_dir):
            print(
                "\n⏸  AI 分析包已生成。请在 **当前 Grok Build 会话** 中：\n"
                f"   1. 阅读 {work_dir / 'ai' / 'AGENT_PROMPT.md'}\n"
                f"   2. 看图填写 {work_dir / 'ai' / 'broll_vlm.json'}\n"
                f"   3. 填写 {work_dir / 'ai' / 'narrative_plan.json'}\n"
                f"   4. 然后运行：\n"
                f"      python run_pipeline.py run {project_dir.name} --from-stage apply_ai\n"
            )
            # 若后续还有 apply_ai/match...，截断
            rest = ["apply_ai", "match", "assemble", "package"]
            if any(s in selected for s in rest):
                print("   （已跳过 apply_ai 及之后阶段，等 AI JSON 填好再继续）")
                print("\n✓ pipeline paused at ai_prepare")
                return results

    if "apply_ai" in selected:
        from .stages.ai_apply import run_ai_apply

        banner("apply_ai")
        results["apply_ai"] = run_ai_apply(
            work_dir,
            cfg,
            broll_tags=results.get("tag_broll")
            or (
                _load_stage(work_dir, "tag_broll")
                if (work_dir / "broll_tags.json").exists()
                else None
            ),
        )

    if "match" in selected:
        from .stages.match import run_match

        banner("match")
        voiceover = results.get("clean_vo") or _load_stage(work_dir, "clean_vo")
        broll_tags = results.get("tag_broll") or _load_stage(work_dir, "tag_broll")
        # apply_ai 可能已更新 broll_tags.json
        if (work_dir / "broll_tags.json").exists():
            broll_tags = read_json(work_dir / "broll_tags.json")
        results["match"] = run_match(voiceover, broll_tags, work_dir, cfg)

    if "ai_video_prepare" in selected:
        from .stages.ai_video_prepare import run_ai_video_prepare

        banner("ai_video_prepare (Seedance pack)")
        picture_plan = None
        if (work_dir / "picture_plan.json").exists():
            picture_plan = read_json(work_dir / "picture_plan.json")
        elif results.get("match"):
            picture_plan = results["match"]
        results["ai_video_prepare"] = run_ai_video_prepare(
            work_dir, cfg, picture_plan=picture_plan
        )
        print(
            "\n⏸  已导出 AI 视频包。请：\n"
            f"   1. 编辑 {work_dir / 'ai_video' / 'manifest.yaml'}（一条 clip 对应一个 piece，禁止复用）\n"
            f"   2. 按 skill 结构写好 {work_dir / 'ai_video' / 'prompts'}/*.txt\n"
            f"   3. dreamina login && python scripts/seedance_t2v.py --work {work_dir}\n"
            f"   4. python run_pipeline.py run {project_dir.name} --from-stage ai_video_apply\n"
        )

    if "ai_video_apply" in selected:
        from .stages.ai_video_apply import run_ai_video_apply

        banner("ai_video_apply")
        picture_plan = (
            read_json(work_dir / "picture_plan.json")
            if (work_dir / "picture_plan.json").exists()
            else results.get("match")
        )
        results["ai_video_apply"] = run_ai_video_apply(
            work_dir, cfg, picture_plan=picture_plan
        )

    if "assemble" in selected:
        from .stages.assemble import run_assemble

        banner("assemble")
        # 优先磁盘上的 picture_plan（可能已被 ai_video_apply 改写）
        if (work_dir / "picture_plan.json").exists():
            picture_plan = read_json(work_dir / "picture_plan.json")
        else:
            picture_plan = results.get("match") or _load_stage(work_dir, "match")
        voiceover = results.get("clean_vo") or _load_stage(work_dir, "clean_vo")
        results["assemble"] = run_assemble(picture_plan, voiceover, work_dir, cfg)

    if "package" in selected:
        from .stages.export_package import run_export_package

        banner("package")
        results["package"] = run_export_package(
            project_dir,
            work_dir,
            cfg,
            asset_index=results.get("ingest") or _load_stage(work_dir, "ingest"),
            voiceover=results.get("clean_vo") or _load_stage(work_dir, "clean_vo"),
            picture_plan=(
            read_json(work_dir / "picture_plan.json")
            if (work_dir / "picture_plan.json").exists()
            else results.get("match") or _load_stage(work_dir, "match")
        ),
            assemble=results.get("assemble")
            or (
                _load_stage(work_dir, "assemble")
                if (work_dir / "assemble.json").exists()
                else None
            ),
            broll_tags=read_json(work_dir / "broll_tags.json")
            if (work_dir / "broll_tags.json").exists()
            else None,
        )

    print("\n✓ pipeline done")
    if results.get("assemble"):
        print(f"  roughcut: {results['assemble'].get('roughcut')}")
    if results.get("package"):
        print(f"  package:  {results['package'].get('package_dir')}")
    return results


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="travel-talk",
        description="旅拍口播流水线：口播+B-roll → 粗剪MP4 + 剪映工程包",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="运行流水线")
    run_p.add_argument("project", type=str, help="项目目录（含口播/B-roll 素材）")
    run_p.add_argument(
        "--stage",
        action="append",
        dest="stages",
        choices=STAGES,
        help="只跑指定阶段，可重复；默认全流程（不含 AI）",
    )
    run_p.add_argument(
        "--from-stage",
        choices=STAGES,
        help="从某阶段跑到结束（会加载先前产物）",
    )
    run_p.add_argument(
        "--ai",
        action="store_true",
        help="启用 Grok Build AI 层：prepare→(填JSON)→apply→match…",
    )
    run_p.add_argument("--config", type=str, default=None, help="额外 yaml 配置")
    run_p.add_argument("--work", type=str, default=None, help="工作目录，默认 <project>/work")
    run_p.add_argument(
        "--force-transcribe",
        action="store_true",
        help="强制重新转写口播",
    )

    init_p = sub.add_parser("init", help="在目录写入 project.yaml 模板")
    init_p.add_argument("project", type=str)
    init_p.add_argument("--title", type=str, default=None)

    sub.add_parser("stages", help="列出阶段")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "stages":
        for i, s in enumerate(STAGES, 1):
            mark = ""
            if s in ("ai_prepare", "apply_ai"):
                mark = " (Grok Build 标签/叙事 AI)"
            elif s in ("ai_video_prepare", "ai_video_apply"):
                mark = " (Seedance/Dreamina 文生视频 B-roll)"
            print(f"{i}. {s}{mark}")
        return 0

    if args.cmd == "init":
        path = init_project_yaml(Path(args.project), title=args.title)
        print(f"wrote {path}")
        return 0

    if args.cmd == "run":
        project = Path(args.project)
        stages = args.stages
        if args.from_stage:
            idx = STAGES.index(args.from_stage)
            stages = STAGES[idx:]
        try:
            run_pipeline(
                project,
                stages=stages,
                config_path=Path(args.config) if args.config else None,
                work=args.work,
                force_transcribe=bool(args.force_transcribe),
                with_ai=bool(args.ai),
            )
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
