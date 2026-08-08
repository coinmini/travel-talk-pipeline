"""Stage 7: CapCut-friendly package — clips, timeline.md, srt, asset index."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import Any

from ..utils import ensure_dir, run, ts_clock, ts_srt, write_json, write_text


def run_export_package(
    project_dir: Path,
    work_dir: Path,
    cfg: dict[str, Any],
    *,
    asset_index: dict[str, Any],
    voiceover: dict[str, Any],
    picture_plan: dict[str, Any],
    assemble: dict[str, Any] | None,
    broll_tags: dict[str, Any] | None,
) -> dict[str, Any]:
    title = cfg.get("title") or project_dir.name
    pkg = ensure_dir(work_dir / "package")
    clips_dir = ensure_dir(pkg / "clips")
    # 清空旧 clips，避免与新时间线（开口对齐后）混在一起
    for old in clips_dir.glob("*.mp4"):
        try:
            old.unlink()
        except OSError:
            pass
    out_cfg = cfg.get("output") or {}
    width = int(out_cfg.get("width") or 1080)
    height = int(out_cfg.get("height") or 1920)
    fps = int(out_cfg.get("fps") or 30)

    pieces = picture_plan.get("picture_timeline") or []
    clip_rows = []
    # 与 roughcut 同源：优先复制 assemble 已渲染的 v_XXXX.mp4
    # （含词级开口 src_start、静态图 Ken Burns）
    parts_dir = work_dir / "assemble_parts"
    copied_from_assemble = 0

    for i, piece in enumerate(pieces):
        label = f"{i+1:02d}_{piece['type']}_{Path(piece.get('src_name') or 'clip').stem[:20]}"
        # sanitize
        safe = "".join(c if c.isalnum() or c in "-_." or "\u4e00" <= c <= "\u9fff" else "_" for c in label)
        out = clips_dir / f"{safe}.mp4"
        part = parts_dir / f"v_{i:04d}.mp4"
        used_assemble = False
        if part.exists() and part.stat().st_size > 1000:
            try:
                shutil.copy2(part, out)
                used_assemble = True
                copied_from_assemble += 1
            except OSError:
                used_assemble = False

        if not used_assemble:
            ss = float(piece.get("src_start") or 0)
            to = float(piece.get("src_end") or ss + float(piece["duration"]))
            dur = float(piece["duration"])
            src = piece["src"]
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={fps},setsar=1,format=yuv420p"
            )
            if Path(src).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
                # 回退渲染：尽量与 assemble 一致做 Ken Burns
                try:
                    from .assemble import _image_motion_vf, _pick_image_motion

                    motion = _pick_image_motion(piece, i, None)
                    vf = _image_motion_vf(width, height, fps, dur, motion)
                except Exception:
                    pass
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-loop",
                        "1",
                        "-i",
                        src,
                        "-t",
                        f"{dur:.3f}",
                        "-vf",
                        vf,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "20",
                        "-an",
                        str(out),
                    ]
                )
            else:
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        f"{ss:.3f}",
                        "-i",
                        src,
                        "-t",
                        f"{dur:.3f}",
                        "-vf",
                        vf,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "20",
                        "-an",
                        str(out),
                    ]
                )
        clip_rows.append(
            {
                **piece,
                "clip_file": out.name,
                "clip_path": str(out),
                "from_assemble_part": used_assemble,
            }
        )
    if copied_from_assemble:
        print(
            f"  package clips: {copied_from_assemble}/{len(pieces)} "
            f"copied from assemble_parts (match roughcut)"
        )

    # copy master VO + srt
    vo_src = Path(voiceover.get("voiceover_wav") or "")
    if vo_src.exists():
        shutil.copy2(vo_src, pkg / "voiceover_clean.wav")
    srt_src = Path(voiceover.get("captions_zh_srt") or "")
    if srt_src.exists():
        shutil.copy2(srt_src, pkg / "captions_zh.srt")

    # English stub: same timing, placeholder text note
    if (cfg.get("export") or {}).get("include_english_stub", True) and srt_src.exists():
        zh = srt_src.read_text(encoding="utf-8")
        # keep Chinese as reference; mark as need translation
        en = zh  # user/CapCut can re-translate; structure preserved
        write_text(pkg / "captions_en_TODO.srt", en)
        write_text(
            pkg / "captions_en_NOTE.txt",
            "captions_en_TODO.srt 目前与中文字幕同轴，请在剪映或翻译后替换英文内容。\n",
        )

    # copy roughcut if present
    if assemble and assemble.get("roughcut"):
        rc = Path(assemble["roughcut"])
        if rc.exists():
            shutil.copy2(rc, pkg / "roughcut.mp4")

    # asset_index.csv
    csv_path = pkg / "asset_index.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["role", "name", "duration", "width", "height", "tags", "path"])
        for a in asset_index.get("talk") or []:
            w.writerow(["talk", a["name"], a.get("duration"), a.get("width"), a.get("height"), "", a.get("path")])
        brolls = (broll_tags or {}).get("broll") or asset_index.get("broll") or []
        for a in brolls:
            w.writerow(
                [
                    "broll",
                    a["name"],
                    a.get("duration"),
                    a.get("width"),
                    a.get("height"),
                    "|".join(a.get("tags") or []),
                    a.get("path"),
                ]
            )

    # timeline.md — primary CapCut guide
    lines = [
        f"# {title} · 剪映时间线",
        "",
        f"- 目标画幅：{width}×{height} 竖屏",
        f"- 口播总长：{voiceover.get('total_duration', 0):.1f}s",
        f"- 画面片段：{len(clip_rows)} 段（见 `clips/`）",
        f"- 人声主轴：`voiceover_clean.wav`",
        f"- 中文字幕：`captions_zh.srt`",
        f"- 粗剪成片：`roughcut.mp4`（若已生成）",
        "",
        "## 剪映导入步骤",
        "",
        "1. 新建竖屏项目（1080×1920）",
        "2. 导入 `clips/` 全部片段 + `voiceover_clean.wav`",
        "3. 按下方序号把 clips 依次放进 **视频轨**（已按顺序命名）",
        "4. 把 `voiceover_clean.wav` 放进 **音频轨**，对齐 0 秒",
        "5. 导入 `captions_zh.srt` 或使用识别字幕后替换文案",
        "6. 加 BGM（音量约 -18~-24dB）、地点贴纸、封面",
        "7. 导出同一竖屏成片，多平台分发",
        "",
        "## 时间线明细",
        "",
        "| # | 成片时间 | 类型 | 片段文件 | 源素材 | 源入出点 | 口播/说明 |",
        "|---|----------|------|----------|--------|----------|-----------|",
    ]
    for i, p in enumerate(clip_rows, 1):
        t0 = ts_clock(float(p["timeline_start"]))
        t1 = ts_clock(float(p["timeline_end"]))
        text = (p.get("text") or "").replace("|", "\\|").replace("\n", " ")
        if len(text) > 40:
            text = text[:40] + "…"
        src_io = f"{p.get('src_start', 0):.1f}-{p.get('src_end', 0):.1f}s"
        lines.append(
            f"| {i} | {t0}–{t1} | {p['type']} | `{p['clip_file']}` | {p.get('src_name','')} | {src_io} | {text} |"
        )

    lines.extend(
        [
            "",
            "## 口播全文",
            "",
            "```",
            voiceover.get("full_text") or "",
            "```",
            "",
            "## B-roll 使用次数",
            "",
        ]
    )
    usage = picture_plan.get("broll_usage") or {}
    if usage:
        for name, n in sorted(usage.items(), key=lambda x: -x[1]):
            lines.append(f"- {name}: {n} 次")
    else:
        lines.append("- （无）")

    lines.extend(
        [
            "",
            "## 建议你在剪映精修的部分",
            "",
            "- 网感转场 / 卡点（本流水线只做硬切）",
            "- 双语字幕样式（中英双行、关键词高亮）",
            "- 地点胶囊贴纸、动物贴纸",
            "- 版权 BGM 与音量包络",
            "- 封面三选一 + 标题花字",
            "",
        ]
    )
    write_text(pkg / "timeline.md", "\n".join(lines))

    # machine-readable timeline
    write_json(
        pkg / "timeline.json",
        {
            "title": title,
            "width": width,
            "height": height,
            "voiceover_duration": voiceover.get("total_duration"),
            "clips": clip_rows,
            "vo_segments": voiceover.get("timeline"),
        },
    )

    # README
    write_text(
        pkg / "README.md",
        f"""# {title} · 交付包

## 内容

| 文件 | 说明 |
|------|------|
| `roughcut.mp4` | AI 粗剪成片（可预览/可发，建议再精修） |
| `timeline.md` | 剪映操作指南 + 分镜表 |
| `timeline.json` | 机器可读时间线 |
| `clips/` | 已按时间线切好的竖屏片段 |
| `voiceover_clean.wav` | 精剪口播人声主轴 |
| `captions_zh.srt` | 中文字幕 |
| `asset_index.csv` | 素材清单与标签 |

## 快速使用

1. 预览 `roughcut.mp4`
2. 要网感包装 → 打开 `timeline.md`，在剪映里导入 `clips/` + 人声
3. 改标签后重跑匹配：编辑项目根目录 `project.yaml` 的 `broll.tags_override`
""",
    )

    result = {
        "package_dir": str(pkg),
        "clip_count": len(clip_rows),
        "timeline_md": str(pkg / "timeline.md"),
        "roughcut": str(pkg / "roughcut.mp4") if (pkg / "roughcut.mp4").exists() else "",
    }
    write_json(work_dir / "package.json", result)
    print(f"  package ready: {pkg}")
    return result


def _safe_pkg_name(stem: str) -> str:
    s = "".join(
        c if c.isalnum() or c in "-_." or "\u4e00" <= c <= "\u9fff" else "_"
        for c in stem
    )
    return s.strip("_") or "part"


def run_export_split_by_talk(
    project_dir: Path,
    work_dir: Path,
    cfg: dict[str, Any],
    *,
    asset_index: dict[str, Any],
    voiceover: dict[str, Any],
    picture_plan: dict[str, Any],
    assemble: dict[str, Any] | None = None,
    broll_tags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按口播源文件拆成多个独立 package（各自 clips + VO + roughcut）。

    例如 sequence 含 口播1 + 口播2 → work/package_by_talk/越南口播1/ 与 …/越南口播2/
    """
    out_cfg = cfg.get("output") or {}
    width = int(out_cfg.get("width") or 1080)
    height = int(out_cfg.get("height") or 1920)
    fps = int(out_cfg.get("fps") or 30)
    v_br = out_cfg.get("video_bitrate") or "6M"
    a_br = out_cfg.get("audio_bitrate") or "192k"
    title_base = cfg.get("title") or project_dir.name

    vo_timeline = list(voiceover.get("timeline") or [])
    pieces = list(picture_plan.get("picture_timeline") or [])
    if not vo_timeline or not pieces:
        raise RuntimeError("voiceover/picture_plan empty; run clean_vo + match first")

    # 每个口播文件在整条时间线上的区间
    groups: dict[str, list[dict[str, Any]]] = {}
    for seg in vo_timeline:
        name = str(seg.get("src_name") or "talk")
        groups.setdefault(name, []).append(seg)

    vo_wav = Path(voiceover.get("voiceover_wav") or "")
    if not vo_wav.exists():
        raise RuntimeError(f"missing voiceover wav: {vo_wav}")

    parts_dir = work_dir / "assemble_parts"
    root = ensure_dir(work_dir / "package_by_talk")
    packages: list[dict[str, Any]] = []

    # 全局 piece index → assemble part
    for talk_name, segs in groups.items():
        t0 = float(segs[0]["timeline_start"])
        t1 = float(segs[-1]["timeline_end"])
        stem = Path(talk_name).stem
        pkg_name = _safe_pkg_name(stem)
        pkg = ensure_dir(root / pkg_name)
        clips_dir = ensure_dir(pkg / "clips")
        for old in clips_dir.glob("*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass

        # 属于该口播时段的画面（按成片时间重叠）
        local_pieces: list[tuple[int, dict[str, Any]]] = []
        for gi, p in enumerate(pieces):
            ps = float(p.get("timeline_start") or 0)
            pe = float(p.get("timeline_end") or 0)
            if pe <= t0 + 1e-6 or ps >= t1 - 1e-6:
                continue
            local_pieces.append((gi, p))

        if not local_pieces:
            print(f"  [warn] no pieces for {talk_name}, skip")
            continue

        # 偏移到本集 0 点
        clip_rows: list[dict[str, Any]] = []
        part_paths: list[Path] = []
        for li, (gi, p) in enumerate(local_pieces):
            np = dict(p)
            np["timeline_start"] = round(float(p["timeline_start"]) - t0, 3)
            np["timeline_end"] = round(float(p["timeline_end"]) - t0, 3)
            np["duration"] = round(
                float(np["timeline_end"]) - float(np["timeline_start"]), 3
            )
            label = (
                f"{li+1:02d}_{np['type']}_"
                f"{Path(np.get('src_name') or 'clip').stem[:20]}"
            )
            safe = "".join(
                c
                if c.isalnum() or c in "-_." or "\u4e00" <= c <= "\u9fff"
                else "_"
                for c in label
            )
            out = clips_dir / f"{safe}.mp4"
            part = parts_dir / f"v_{gi:04d}.mp4"
            if part.exists() and part.stat().st_size > 1000:
                shutil.copy2(part, out)
            else:
                # 无 assemble 部件则跳过复制（仍写 timeline 提示需先 assemble）
                print(f"  [warn] missing assemble part {part.name} for {talk_name}")
            np["clip_file"] = out.name
            np["clip_path"] = str(out)
            clip_rows.append(np)
            if out.exists():
                part_paths.append(out)

        # 本集口播轨：从总 VO 裁出 [t0, t1)
        local_vo = pkg / "voiceover_clean.wav"
        dur = max(0.05, t1 - t0)
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(vo_wav),
                "-af",
                f"atrim=start={t0:.6f}:duration={dur:.6f},asetpts=PTS-STARTPTS",
                "-ac",
                "1",
                "-ar",
                "44100",
                str(local_vo),
            ]
        )

        # 字幕：时间轴归零
        srt_lines = []
        for i, seg in enumerate(segs, 1):
            ls = float(seg["timeline_start"]) - t0
            le = float(seg["timeline_end"]) - t0
            text = (seg.get("text") or "").strip()
            srt_lines.append(
                f"{i}\n{ts_srt(ls)} --> {ts_srt(le)}\n{text}\n"
            )
        write_text(pkg / "captions_zh.srt", "\n".join(srt_lines))
        write_text(
            pkg / "voiceover.txt",
            "\n".join((s.get("text") or "").strip() for s in segs),
        )

        local_vo_meta = {
            "total_duration": round(dur, 3),
            "segment_count": len(segs),
            "voiceover_wav": str(local_vo),
            "captions_zh_srt": str(pkg / "captions_zh.srt"),
            "timeline": [
                {
                    **s,
                    "timeline_start": round(float(s["timeline_start"]) - t0, 3),
                    "timeline_end": round(float(s["timeline_end"]) - t0, 3),
                }
                for s in segs
            ],
            "full_text": "\n".join((s.get("text") or "").strip() for s in segs),
            "split_from": talk_name,
            "global_timeline_range": [round(t0, 3), round(t1, 3)],
        }
        write_json(pkg / "voiceover.json", local_vo_meta)

        local_plan = {
            "total_duration": round(dur, 3),
            "slot_count": len(clip_rows),
            "picture_count": len(clip_rows),
            "picture_timeline": clip_rows,
            "split_from": talk_name,
            "parent_title": title_base,
        }
        write_json(pkg / "picture_plan.json", local_plan)

        # 本集 roughcut
        rough = pkg / "roughcut.mp4"
        if part_paths:
            concat_list = pkg / "concat.txt"
            write_text(
                concat_list,
                "\n".join(f"file '{p.resolve()}'" for p in part_paths) + "\n",
            )
            video_only = pkg / "picture_track.mp4"
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-b:v",
                    v_br,
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(video_only),
                ]
            )
            # 以人声为准：画面短则冻尾帧补齐，绝不 -shortest 裁掉末字
            from ..utils import media_info as _media_info

            v_dur = float(_media_info(video_only).get("duration") or 0)
            a_dur = float(_media_info(local_vo).get("duration") or 0)
            vpad = max(0.0, a_dur - v_dur + 0.05)
            if vpad > 0.04:
                fc = f"[0:v]tpad=stop_mode=clone:stop_duration={vpad:.3f}[v]"
                map_v = "[v]"
                filt = ["-filter_complex", fc, "-map", map_v, "-map", "1:a:0"]
            else:
                filt = ["-map", "0:v:0", "-map", "1:a:0"]
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video_only),
                    "-i",
                    str(local_vo),
                    *filt,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-b:v",
                    v_br,
                    "-c:a",
                    "aac",
                    "-b:a",
                    a_br,
                    "-t",
                    f"{max(a_dur, v_dur):.3f}",
                    "-movflags",
                    "+faststart",
                    str(rough),
                ]
            )
            try:
                video_only.unlink()
                concat_list.unlink()
            except OSError:
                pass

        part_title = f"{title_base} · {stem}"
        # timeline.md
        lines = [
            f"# {part_title} · 剪映时间线（独立成片）",
            "",
            f"- 目标画幅：{width}×{height} 竖屏",
            f"- 本集口播源：`{talk_name}`",
            f"- 本集时长：{dur:.1f}s（原整片 {t0:.1f}–{t1:.1f}s）",
            f"- 画面片段：{len(clip_rows)} 段（见 `clips/`）",
            f"- 人声主轴：`voiceover_clean.wav`",
            f"- 中文字幕：`captions_zh.srt`",
            f"- 粗剪成片：`roughcut.mp4`",
            "",
            "## 剪映导入步骤",
            "",
            "1. 新建竖屏项目（1080×1920）",
            "2. 导入本目录 `clips/` + `voiceover_clean.wav`",
            "3. 按序号把 clips 依次放进视频轨，人声对齐 0 秒",
            "4. 导入 `captions_zh.srt`，加 BGM / 贴纸 / 封面后导出",
            "",
            "## 时间线明细",
            "",
            "| # | 成片时间 | 类型 | 片段文件 | 源素材 | 源入出点 | 口播/说明 |",
            "|---|----------|------|----------|--------|----------|-----------|",
        ]
        for i, p in enumerate(clip_rows, 1):
            text = (p.get("text") or "").replace("|", "\\|").replace("\n", " ")
            if len(text) > 40:
                text = text[:40] + "…"
            src_io = f"{p.get('src_start', 0):.1f}-{p.get('src_end', 0):.1f}s"
            lines.append(
                f"| {i} | {ts_clock(float(p['timeline_start']))}–"
                f"{ts_clock(float(p['timeline_end']))} | {p['type']} | "
                f"`{p['clip_file']}` | {p.get('src_name','')} | {src_io} | {text} |"
            )
        lines.extend(
            [
                "",
                "## 口播全文",
                "",
                "```",
                local_vo_meta.get("full_text") or "",
                "```",
                "",
            ]
        )
        write_text(pkg / "timeline.md", "\n".join(lines))
        write_json(
            pkg / "timeline.json",
            {
                "title": part_title,
                "talk_source": talk_name,
                "width": width,
                "height": height,
                "voiceover_duration": dur,
                "clips": clip_rows,
                "vo_segments": local_vo_meta["timeline"],
            },
        )
        write_text(
            pkg / "README.md",
            f"""# {part_title} · 独立交付包

从整片按口播源拆分：`{talk_name}`（{dur:.1f}s）。

| 文件 | 说明 |
|------|------|
| `roughcut.mp4` | 本集粗剪成片 |
| `clips/` | 本集竖屏片段（与 roughcut 同源） |
| `voiceover_clean.wav` | 本集人声 |
| `captions_zh.srt` | 本集字幕（时间从 0 开始） |
| `timeline.md` | 剪映导入说明 |

与姐妹包同一父项目：`{title_base}`，目录 `work/package_by_talk/`。
""",
        )

        info = {
            "talk_source": talk_name,
            "package_dir": str(pkg),
            "clip_count": len(clip_rows),
            "duration": round(dur, 3),
            "roughcut": str(rough) if rough.exists() else "",
            "global_range": [round(t0, 3), round(t1, 3)],
        }
        packages.append(info)
        print(
            f"  split package: {pkg_name}/  clips={len(clip_rows)}  "
            f"dur={dur:.1f}s  roughcut={'yes' if rough.exists() else 'no'}"
        )

    result = {
        "package_by_talk_dir": str(root),
        "packages": packages,
        "count": len(packages),
    }
    write_json(work_dir / "package_by_talk.json", result)
    print(f"  package_by_talk ready: {root} ({len(packages)} packages)")
    return result
