"""Stage 7: CapCut-friendly package — clips, timeline.md, srt, asset index."""

from __future__ import annotations

import csv
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ..utils import (
    append_outro_av,
    concat_wavs,
    ensure_dir,
    media_info,
    prepare_outro_assets,
    resolve_outro_path,
    run,
    ts_clock,
    ts_srt,
    write_json,
    write_text,
)


def _stamp_clip_order(paths: list[Path], *, base: float | None = None) -> None:
    """按序号递增写入 mtime / macOS 创建时间（素材库「按时间」排序时用）。"""
    t0 = base if base is not None else time.time() - max(len(paths), 1) - 2
    for i, p in enumerate(paths):
        if not p.exists():
            continue
        ts = t0 + i
        try:
            os.utime(p, (ts, ts))
        except OSError:
            pass
        if sys.platform == "darwin":
            try:
                lt = time.localtime(ts)
                ds = time.strftime("%m/%d/%Y %H:%M:%S", lt)
                run(["SetFile", "-d", ds, str(p)], check=False)
            except Exception:
                pass


def _write_clip_with_order_metadata(
    src: Path, dst: Path, *, index: int, keep_audio: bool = False
) -> None:
    """写出分镜文件，并写入递增 creation_time（剪映常按「拍摄时间」排序）。

    序号 i 对应 creation_time = 2000-01-01T00:00:00Z + i 秒。
    普通分镜 keep_audio=False（人声走独立 wav）；片尾 keep_audio=True（自带音）。
    """
    # 2000-01-01 起算，避免时区歧义；i 秒保证严格递增
    epoch = 946684800 + int(index)  # UTC 2000-01-01 + index seconds
    creation = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(epoch))
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-c",
        "copy",
        "-map_metadata",
        "-1",
        "-metadata",
        f"creation_time={creation}",
        "-metadata",
        f"title={dst.stem}",
        "-movflags",
        "+faststart",
    ]
    if not keep_audio:
        cmd.append("-an")  # 分镜无声，人声走独立 wav
    cmd.append(str(tmp))
    try:
        run(cmd)
        tmp.replace(dst)
    except Exception:
        # 回退：直接复制
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        shutil.copy(src, dst)


def _build_jianying_draft(
    *,
    draft_root: Path,
    draft_name: str,
    width: int,
    height: int,
    fps: int,
    clip_items: list[dict[str, Any]],
    vo_path: Path | None,
    srt_path: Path | None,
    vo_duration_sec: float | None = None,
) -> Path | None:
    """用 pyJianYingDraft 生成「轨道顺序已排好」的剪映草稿（每段仍可单独改）。

    clip_items: [{path, has_audio?}]  片尾 has_audio=True 保留音量。
    vo_duration_sec: 口播时长（不含片尾）；片尾音在末镜自带。
    """
    try:
        import pyJianYingDraft as jy
    except ImportError:
        print("  [warn] pyJianYingDraft 未安装，跳过剪映草稿生成")
        return None
    if not clip_items:
        return None
    ensure_dir(draft_root)
    # 清理旧草稿
    target = draft_root / draft_name
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        folder = jy.DraftFolder(str(draft_root))
        script = folder.create_draft(
            draft_name, width, height, fps=fps, allow_replace=True
        )
        script.append_track(jy.TrackSpec(jy.TrackType.video, name="画面"))
        script.append_track(jy.TrackSpec(jy.TrackType.audio, name="口播"))
        t = 0
        speech_end = 0
        for it in clip_items:
            p = Path(it["path"])
            has_audio = bool(it.get("has_audio"))
            mat = jy.VideoMaterial(str(p.resolve()), material_name=p.name)
            dur = int(mat.duration)
            if dur <= 0:
                continue
            vol = 1.0 if has_audio else 0.0
            seg = jy.VideoSegment(mat, jy.trange(t, dur), volume=vol)
            script.add_segment(seg, track="画面")
            t += dur
            if not has_audio:
                speech_end = t
        if vo_path and vo_path.exists() and speech_end > 0:
            am = jy.AudioMaterial(str(vo_path.resolve()))
            ad = int(am.duration)
            # 口播只盖正文；片尾用末镜自带音
            cap = int((vo_duration_sec or 0) * 1_000_000) if vo_duration_sec else ad
            use = min(x for x in (ad, speech_end, cap if cap > 0 else ad) if x > 0)
            script.add_segment(
                jy.AudioSegment(am, jy.trange(0, use)), track="口播"
            )
        if srt_path and srt_path.exists():
            try:
                script.import_srt(str(srt_path.resolve()), track_name="字幕")
            except Exception as e:
                print(f"  [warn] 草稿字幕导入失败: {e}")
        script.save()
        return target if target.exists() else None
    except Exception as e:
        print(f"  [warn] 剪映草稿生成失败: {e}")
        return None


def _safe_draft_name(title: str) -> str:
    s = "".join(
        c if c.isalnum() or c in "-_." or "\u4e00" <= c <= "\u9fff" else "_"
        for c in (title or "draft")
    )
    return (s or "draft")[:40]


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
    """导出剪映友好包（支持批量分镜 clips 进时间线）。

    顺序问题根因（实测）：
      - APFS 目录遍历序 ≠ 文件名序；整夹拖入时剪映常按遍历序/拍摄时间
      - assemble 渲染时间戳后渲的镜更新，按修改时间会倒序
      - 文件名 15_ 与 01_ 若未按「名称」排序，客房镜会跑到开场

    强制顺序的三层手段：
      1) 文件名 001.mp4…029.mp4（名称排序）
      2) 递增 creation_time 元数据 + 磁盘创建/修改时间（拍摄时间/时间排序）
      3) 生成剪映草稿（轨道已排好，每段仍可单独替换）

      package/
        剪映导入/                 ← 仅 clips/ + voiceover（勿塞字幕/说明，避免混排）
          clips/001.mp4 …
          voiceover_clean.wav
        剪映草稿/<标题>/
        工程/  timeline / srt 备查
    """
    title = cfg.get("title") or project_dir.name
    pkg = ensure_dir(work_dir / "package")
    import_dir = ensure_dir(pkg / "剪映导入")
    meta_dir = ensure_dir(pkg / "工程")
    draft_root = ensure_dir(pkg / "剪映草稿")
    clips_dir = ensure_dir(import_dir / "clips")

    # 清空旧 clips；清理历史错误结构
    for old in list(clips_dir.glob("*.mp4")) + list(clips_dir.glob("*.tmp.mp4")):
        try:
            old.unlink()
        except OSError:
            pass
    for legacy in (pkg / "clips", meta_dir / "clips"):
        if legacy.is_dir():
            shutil.rmtree(legacy, ignore_errors=True)
    for stale_name in (
        "voiceover_clean.wav",
        "captions_zh.srt",
        "captions_en_TODO.srt",
        "captions_en_NOTE.txt",
        "timeline.md",
        "timeline.json",
        "asset_index.csv",
        "画面轨_已按序.mp4",
        "00_画面轨_已按序.mp4",
        "导入说明.txt",
    ):
        for folder in (pkg, import_dir, meta_dir):
            sp = folder / stale_name
            try:
                if sp.is_file():
                    sp.unlink()
            except OSError:
                pass
    for old_rc in pkg.glob("roughcut*.mp4"):
        try:
            old_rc.unlink()
        except OSError:
            pass
    # 导入层禁止残留非目标文件（字幕/说明曾导致整夹混排）
    for sp in import_dir.iterdir():
        if sp.name in {"clips", "voiceover_clean.wav"}:
            continue
        try:
            if sp.is_file():
                sp.unlink()
            elif sp.is_dir():
                shutil.rmtree(sp, ignore_errors=True)
        except OSError:
            pass

    out_cfg = cfg.get("output") or {}
    width = int(out_cfg.get("width") or 1080)
    height = int(out_cfg.get("height") or 1920)
    fps = int(out_cfg.get("fps") or 30)

    pieces = picture_plan.get("picture_timeline") or []
    clip_rows = []
    parts_dir = work_dir / "assemble_parts"
    copied_from_assemble = 0
    # 临时目录放 assemble 原始 part，再写入带 metadata 的正式 clips
    raw_dir = ensure_dir(work_dir / "package_raw_clips")
    for old in raw_dir.glob("*.mp4"):
        try:
            old.unlink()
        except OSError:
            pass

    for i, piece in enumerate(pieces):
        seq = f"{i+1:03d}"
        # 纯数字文件名：名称排序 = 时间线顺序（最稳）
        out = clips_dir / f"{seq}.mp4"
        part = parts_dir / f"v_{i:04d}.mp4"
        raw = raw_dir / f"{seq}.mp4"
        used_assemble = False
        if part.exists() and part.stat().st_size > 1000:
            try:
                shutil.copy(part, raw)
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
                        str(raw),
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
                        str(raw),
                    ]
                )
        # 写入递增 creation_time + 纯数字文件名
        _write_clip_with_order_metadata(raw, out, index=i + 1, keep_audio=False)
        clip_rows.append(
            {
                **piece,
                "clip_file": out.name,
                "clip_path": str(out),
                "seq": seq,
                "label": f"{seq}_{piece.get('type')}_{Path(piece.get('src_name') or '').stem[:16]}",
                "has_audio": False,
                "from_assemble_part": used_assemble,
            }
        )

    # 固定片尾：最后一镜（有声，人声轨不叠片尾音）
    outro_meta: dict[str, Any] | None = None
    outro_src = resolve_outro_path(project_dir, cfg)
    # 嵌套项目目录：再试仓库根（project/../..）
    if outro_src is None:
        root_try = project_dir.parent.parent / "视频结尾.MP4"
        if root_try.exists():
            outro_src = root_try.resolve()
    if outro_src is not None:
        try:
            v_br = str(out_cfg.get("video_bitrate") or "6M")
            a_br = str(out_cfg.get("audio_bitrate") or "192k")
            assets = prepare_outro_assets(
                outro_src,
                work_dir,
                width=width,
                height=height,
                fps=fps,
                v_br=v_br,
                a_br=a_br,
            )
            n = len(clip_rows) + 1
            seq = f"{n:03d}"
            out = clips_dir / f"{seq}.mp4"
            full = Path(assets["full"])
            _write_clip_with_order_metadata(full, out, index=n, keep_audio=True)
            od = float(assets.get("duration") or 0)
            t_end = float(clip_rows[-1]["timeline_end"]) if clip_rows else 0.0
            clip_rows.append(
                {
                    "type": "outro",
                    "timeline_start": round(t_end, 3),
                    "timeline_end": round(t_end + od, 3),
                    "duration": round(od, 3),
                    "src": assets["src"],
                    "src_name": Path(assets["src"]).name,
                    "src_start": 0.0,
                    "src_end": round(od, 3),
                    "text": "[片尾·有声]",
                    "has_audio": True,
                    "clip_file": out.name,
                    "clip_path": str(out),
                    "seq": seq,
                    "label": f"{seq}_outro_视频结尾",
                    "from_assemble_part": False,
                }
            )
            outro_meta = {
                "file": out.name,
                "duration": round(od, 3),
                "src": assets["src"],
                "note": "末镜自带声音；voiceover_clean.wav 仅口播正文",
            }
            print(f"  [outro] 固定片尾 → clips/{out.name} ({od:.2f}s, 有声)")
        except Exception as e:
            print(f"  [warn] 片尾导出失败: {e}")
    else:
        print("  [outro] skip: 未找到 视频结尾.MP4")

    clip_paths = [Path(r["clip_path"]) for r in clip_rows]
    _stamp_clip_order(clip_paths)
    if copied_from_assemble:
        print(
            f"  package clips: {copied_from_assemble}/{len(pieces)} 正文 + "
            f"{'1 片尾' if outro_meta else '无片尾'} "
            f"→ 剪映导入/clips/001.mp4…"
        )

    # 剪映导入/ 仅两样：clips/ + voiceover（字幕后期再加，不放这里以免混排）
    vo_src = Path(voiceover.get("voiceover_wav") or "")
    vo_dst = import_dir / "voiceover_clean.wav"
    if vo_src.exists():
        shutil.copy(vo_src, vo_dst)
    srt_src = Path(voiceover.get("captions_zh_srt") or "")
    # 字幕仅备查，放工程/
    srt_meta = meta_dir / "captions_zh.srt"
    if srt_src.exists():
        shutil.copy(srt_src, srt_meta)

    speech_dur = float(voiceover.get("total_duration") or 0)
    # 剪映草稿：画面 + 口播（不加字幕轨）；片尾末镜有声
    draft_name = _safe_draft_name(title)
    draft_path = _build_jianying_draft(
        draft_root=draft_root,
        draft_name=draft_name,
        width=width,
        height=height,
        fps=fps,
        clip_items=[
            {"path": r["clip_path"], "has_audio": bool(r.get("has_audio"))}
            for r in clip_rows
        ],
        vo_path=vo_dst if vo_dst.exists() else None,
        srt_path=None,
        vo_duration_sec=speech_dur if speech_dur > 0 else None,
    )
    if draft_path:
        print(f"  剪映草稿: {draft_path}")
        for root in (
            Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
            Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft",
        ):
            if not root.is_dir():
                continue
            dest = root / draft_name
            try:
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(draft_path, dest)
                print(f"  已安装草稿到: {dest}")
            except Exception as e:
                print(f"  [warn] 复制草稿到 {root.name} 失败: {e}")

    write_text(
        meta_dir / "导入说明.txt",
        "【剪映导入 · 仅 clips + 人声】\n"
        "\n"
        "目录应只有：\n"
        "  剪映导入/clips/001.mp4 …（末镜为固定片尾，自带声音）\n"
        "  剪映导入/voiceover_clean.wav（仅口播正文，不含片尾音）\n"
        "（字幕后期再加；备查 srt 在 工程/captions_zh.srt）\n"
        "\n"
        "正确步骤：\n"
        "1. 只打开 剪映导入/clips/\n"
        "2. 素材库排序「名称」或「拍摄时间」（001→N，末段是片尾）\n"
        "3. 全选 → 添加视频序列 / 拖进时间线\n"
        "4. 再导入同级 voiceover_clean.wav，对齐 0 秒\n"
        "5. 末镜片尾勿静音（自带音效）；口播 wav 不要拉长盖住片尾\n"
        "\n"
        "不要整夹拖 package/；剪映导入/ 内也不要混入 srt/说明文件。\n"
        "对照表：工程/clip_map.json\n",
    )

    # 工程层：说明 / json / 英文稿 / 素材表（勿导入剪映）
    if (cfg.get("export") or {}).get("include_english_stub", True) and srt_src.exists():
        zh = srt_src.read_text(encoding="utf-8")
        write_text(meta_dir / "captions_en_TODO.srt", zh)
        write_text(
            meta_dir / "captions_en_NOTE.txt",
            "captions_en_TODO.srt 目前与中文字幕同轴，请在剪映后期自行加字幕。\n",
        )

    csv_path = meta_dir / "asset_index.csv"
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

    lines = [
        f"# {title} · 剪映时间线",
        "",
        f"- 目标画幅：{width}×{height} 竖屏",
        f"- 口播总长：{voiceover.get('total_duration', 0):.1f}s",
        f"- 画面片段：{len(clip_rows)} 段（`剪映导入/clips/001.mp4`…）",
        f"- 人声：`剪映导入/voiceover_clean.wav`",
        f"- 字幕：后期自行添加（备查 `工程/captions_zh.srt`）",
        f"- 剪映草稿：`剪映草稿/{draft_name}/`",
        f"- 粗剪预览：`../export/roughcut.mp4`",
        "",
        "## 批量分镜导入（正确）",
        "",
        "1. 只打开 `剪映导入/clips/`",
        "2. 素材库排序「名称」或「拍摄时间」→ 全选拖入时间线（末镜为固定片尾）",
        "3. 再导入 `剪映导入/voiceover_clean.wav`，对齐 0 秒（仅口播，勿盖片尾）",
        "4. 片尾末镜保留原声；字幕后期再加",
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
    write_text(meta_dir / "timeline.md", "\n".join(lines))

    write_json(
        meta_dir / "timeline.json",
        {
            "title": title,
            "width": width,
            "height": height,
            "voiceover_duration": voiceover.get("total_duration"),
            "clips": clip_rows,
            "vo_segments": voiceover.get("timeline"),
        },
    )

    export_rough = work_dir / "export" / "roughcut.mp4"
    write_json(
        meta_dir / "clip_map.json",
        {
            "note": "001.mp4=开场露脸；批量导入前请素材库按「名称」或「拍摄时间」排序",
            "order_keys": [
                "filename 001.mp4…",
                "mp4 creation_time 递增",
                "disk mtime/birthtime 递增",
            ],
            "clips": [
                {
                    "seq": r.get("seq"),
                    "file": r.get("clip_file"),
                    "label": r.get("label"),
                    "type": r.get("type"),
                    "src_name": r.get("src_name"),
                    "text": r.get("text"),
                    "timeline_start": r.get("timeline_start"),
                    "timeline_end": r.get("timeline_end"),
                    "duration": r.get("duration"),
                }
                for r in clip_rows
            ],
        },
    )
    write_text(
        pkg / "README.md",
        f"""# {title} · 交付包（剪映）

## 剪映导入/（应只有这两样）

```
剪映导入/
  clips/001.mp4 … 0xx.mp4   # 末镜 = 固定片尾（有声）
  voiceover_clean.wav         # 仅口播正文
```

字幕后期再加；备查 srt 在 `工程/captions_zh.srt`。

## 正确导入

1. 只打开 `剪映导入/clips/`
2. 素材库排序「名称」或「拍摄时间」（001→N，最后一段是片尾）
3. 全选 → 添加视频序列 / 拖进时间线
4. 再导入同级 `voiceover_clean.wav`，对齐 0 秒（不要拉长盖住片尾）
5. 末镜片尾保留原声

## 其它

| 路径 | 说明 |
|------|------|
| `剪映草稿/{draft_name}/` | 可选：打开即排好序的草稿 |
| `工程/clip_map.json` | 序号 ↔ 口播文案 |
| `../export/roughcut.mp4` | 粗剪预览 |
""",
    )

    result = {
        "package_dir": str(pkg),
        "import_dir": str(import_dir),
        "clips_dir": str(clips_dir),
        "clip_count": len(clip_rows),
        "draft": str(draft_path) if draft_path else "",
        "timeline_md": str(meta_dir / "timeline.md"),
        "roughcut": str(export_rough) if export_rough.exists() else "",
    }
    write_json(work_dir / "package.json", result)
    print(f"  package ready: {pkg}")
    print(f"  → 分镜: {clips_dir}/001.mp4…  草稿: {draft_path or '（未生成）'}")
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
        # 目录分层，避免剪映「整夹导入」把 roughcut/工程 json 全塞进时间线
        #   剪映导入/  ← 只导入这一层（clips + 人声 + 字幕）
        #   预览/      ← 合成 roughcut
        #   工程/      ← timeline 等元数据
        import_dir = ensure_dir(pkg / "剪映导入")
        preview_dir = ensure_dir(pkg / "预览")
        meta_dir = ensure_dir(pkg / "工程")
        clips_dir = ensure_dir(import_dir / "clips")
        for old in clips_dir.glob("*.mp4"):
            try:
                old.unlink()
            except OSError:
                pass
        # 清理旧版扁平布局残留，防止再次整夹误导
        for stale in (
            "clips",
            "verify",
            "voiceover_clean.wav",
            "captions_zh.srt",
            "roughcut.mp4",
            "timeline.json",
            "timeline.md",
            "voiceover.json",
            "voiceover.txt",
            "picture_plan.json",
            "concat.txt",
            "picture_track.mp4",
        ):
            sp = pkg / stale
            try:
                if sp.is_dir():
                    shutil.rmtree(sp, ignore_errors=True)
                elif sp.exists():
                    sp.unlink()
            except OSError:
                pass
        for old in pkg.glob("roughcut*.mp4"):
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
            # 与人声段严格等长（plan duration）；禁止额外末镜冻尾，避免嘴形/字幕漂移
            need_dur = float(np["duration"])
            if part.exists() and part.stat().st_size > 1000:
                try:
                    from ..utils import media_info as _mi

                    got = float(_mi(part).get("duration") or 0)
                    # 容差内直接拷贝；否则裁/补到 need_dur（补只到段长，不超）
                    if abs(got - need_dur) <= 0.04:
                        shutil.copy(part, out)
                    elif got > need_dur + 0.04:
                        run(
                            [
                                "ffmpeg",
                                "-y",
                                "-i",
                                str(part),
                                "-t",
                                f"{need_dur:.6f}",
                                "-an",
                                "-c:v",
                                "libx264",
                                "-preset",
                                "veryfast",
                                "-crf",
                                "18",
                                "-pix_fmt",
                                "yuv420p",
                                str(out),
                            ]
                        )
                    else:
                        pad = need_dur - got + 0.02
                        run(
                            [
                                "ffmpeg",
                                "-y",
                                "-i",
                                str(part),
                                "-vf",
                                f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
                                "-an",
                                "-c:v",
                                "libx264",
                                "-preset",
                                "veryfast",
                                "-crf",
                                "18",
                                "-pix_fmt",
                                "yuv420p",
                                "-t",
                                f"{need_dur:.6f}",
                                str(out),
                            ]
                        )
                except Exception as e:
                    print(f"  [warn] fit clip {out.name}: {e}")
                    try:
                        shutil.copy(part, out)
                    except OSError:
                        pass
            else:
                print(f"  [warn] missing assemble part {part.name} for {talk_name}")
            np["clip_file"] = out.name
            np["clip_path"] = str(out)
            clip_rows.append(np)
            if out.exists():
                part_paths.append(out)

        _stamp_clip_order(part_paths)

        # 本集口播轨：从总 VO 裁出 [t0, t1)。只放进「剪映导入/」。
        local_vo = import_dir / "voiceover_clean.wav"
        speech_dur = max(0.05, t1 - t0)
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(vo_wav),
                "-af",
                f"atrim=start={t0:.6f}:duration={speech_dur:.6f},asetpts=PTS-STARTPTS",
                "-ac",
                "1",
                "-ar",
                "44100",
                str(local_vo),
            ]
        )
        dur = speech_dur

        # 画面与人声对齐：不做末镜额外冻尾（用户要求嘴形/字幕同步，Σ 与 VO 同长即可）

        # 字幕 / 说明 → 剪映导入；工程元数据 → 工程/
        srt_lines = []
        for i, seg in enumerate(segs, 1):
            ls = float(seg["timeline_start"]) - t0
            le = float(seg["timeline_end"]) - t0
            text = (seg.get("text") or "").strip()
            srt_lines.append(
                f"{i}\n{ts_srt(ls)} --> {ts_srt(le)}\n{text}\n"
            )
        write_text(import_dir / "captions_zh.srt", "\n".join(srt_lines))
        write_text(
            import_dir / "导入说明.txt",
            (
                "【只导入本文件夹「剪映导入」】\n"
                "不要把上一级整夹拖进剪映（会带上预览 roughcut / 工程 json，顺序会乱）。\n\n"
                "1. 新建竖屏 1080×1920\n"
                "2. clips/ 按 01→末 顺序拖到视频轨，首尾相接、无空隙\n"
                "3. voiceover_clean.wav 拖到音频轨，对齐时间线 0\n"
                "4. 可选：captions_zh.srt\n\n"
                "说明：正文 clips 无声，与 voiceover_clean.wav 等长对齐 0 点；\n"
                "最后一镜 NN_outro_视频结尾.mp4 为固定片尾且自带声音，\n"
                "导入后不要静音该片段；人声轨不要再拼片尾音。\n"
            ),
        )
        write_text(
            meta_dir / "voiceover.txt",
            "\n".join((s.get("text") or "").strip() for s in segs),
        )

        local_vo_meta = {
            "total_duration": round(dur, 3),
            "segment_count": len(segs),
            "voiceover_wav": str(local_vo),
            "captions_zh_srt": str(import_dir / "captions_zh.srt"),
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
            "import_dir": str(import_dir),
        }
        write_json(meta_dir / "voiceover.json", local_vo_meta)

        local_plan = {
            "total_duration": round(dur, 3),
            "slot_count": len(clip_rows),
            "picture_count": len(clip_rows),
            "picture_timeline": clip_rows,
            "split_from": talk_name,
            "parent_title": title_base,
        }
        write_json(meta_dir / "picture_plan.json", local_plan)

        # 本集 roughcut → 预览/
        rough = preview_dir / "roughcut.mp4"
        if part_paths:
            concat_list = preview_dir / "concat.txt"
            write_text(
                concat_list,
                "\n".join(f"file '{p.resolve()}'" for p in part_paths) + "\n",
            )
            video_only = preview_dir / "picture_track.mp4"
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
            # 预览 roughcut：与人声等长对齐（画面略短才冻 1 帧内，不做长冻尾/尾静音）
            from ..utils import media_info as _media_info

            v_dur = float(_media_info(video_only).get("duration") or 0)
            a_dur = float(_media_info(local_vo).get("duration") or 0)
            out_dur = a_dur if a_dur > 0 else v_dur
            if v_dur + 0.04 < out_dur:
                # 仅补齐到人声长度（通常 < 1 帧级误差）
                vpad = out_dur - v_dur + 0.02
                filt = [
                    "-filter_complex",
                    f"[0:v]tpad=stop_mode=clone:stop_duration={vpad:.3f}[v]",
                    "-map",
                    "[v]",
                    "-map",
                    "1:a:0",
                ]
            elif v_dur > out_dur + 0.04:
                filt = [
                    "-filter_complex",
                    f"[0:v]trim=duration={out_dur:.6f},setpts=PTS-STARTPTS[v]",
                    "-map",
                    "[v]",
                    "-map",
                    "1:a:0",
                ]
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
                    f"{out_dur:.3f}",
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

        # 固定片尾：每个分包成片 + 剪映分轨都必须带
        outro_src = resolve_outro_path(project_dir, cfg)
        if outro_src is not None:
            try:
                assets = prepare_outro_assets(
                    outro_src,
                    work_dir,
                    width=width,
                    height=height,
                    fps=fps,
                    v_br=v_br,
                    a_br=a_br,
                )
                # 1) 预览 roughcut 接片尾
                if rough.exists():
                    tmp = preview_dir / "_body.mp4"
                    run(["ffmpeg", "-y", "-i", str(rough), "-c", "copy", str(tmp)])
                    append_outro_av(
                        tmp,
                        Path(assets["full"]),
                        rough,
                        v_br=v_br,
                        a_br=a_br,
                    )
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                # 2) 剪映导入：末 clip 使用「有声音」片尾（自带音轨）
                #    口播人声轨不再追加片尾音，避免双轨叠音；片尾靠 clip 自身音频
                n = len(clip_rows) + 1
                outro_clip = clips_dir / f"{n:02d}_outro_视频结尾.mp4"
                run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(assets["full"]),
                        "-c:v",
                        "copy",
                        "-c:a",
                        "copy",
                        str(outro_clip),
                    ]
                )
                od = float(assets.get("duration") or 0)
                t_end = float(clip_rows[-1]["timeline_end"]) if clip_rows else 0.0
                clip_rows.append(
                    {
                        "type": "outro",
                        "timeline_start": round(t_end, 3),
                        "timeline_end": round(t_end + od, 3),
                        "duration": round(od, 3),
                        "src": assets["src"],
                        "src_name": Path(assets["src"]).name,
                        "src_start": 0.0,
                        "src_end": round(od, 3),
                        "text": "[片尾·有声]",
                        "has_audio": True,
                        "clip_file": outro_clip.name,
                        "clip_path": str(outro_clip),
                    }
                )
                # 人声仍为口播正文时长；总片长 = 口播 + 片尾
                speech_only = float(media_info(local_vo).get("duration") or speech_dur)
                total_with_outro = speech_only + od
                local_vo_meta["total_duration"] = round(speech_only, 3)
                local_vo_meta["outro_sec"] = round(od, 3)
                local_vo_meta["outro_has_audio"] = True
                local_vo_meta["note"] = (
                    "voiceover_clean.wav 仅口播；片尾音在末 clip "
                    f"{outro_clip.name} 自带，勿再静音该片段"
                )
                write_json(meta_dir / "voiceover.json", local_vo_meta)
                local_plan["total_duration"] = round(total_with_outro, 3)
                local_plan["slot_count"] = len(clip_rows)
                local_plan["picture_count"] = len(clip_rows)
                local_plan["picture_timeline"] = clip_rows
                write_json(meta_dir / "picture_plan.json", local_plan)
                dur = total_with_outro
                print(
                    f"  [outro] {talk_name}: +{od:.2f}s 有声片尾 → {outro_clip.name} "
                    f"(VO 不叠片尾音)"
                )
            except Exception as e:
                print(f"  [warn] outro for {talk_name}: {e}")

        part_title = f"{title_base} · {stem}"
        # timeline.md → 工程/
        lines = [
            f"# {part_title} · 剪映时间线（独立成片）",
            "",
            f"- 目标画幅：{width}×{height} 竖屏",
            f"- 本集口播源：`{talk_name}`",
            f"- 本集时长：{dur:.1f}s（原整片 {t0:.1f}–{t1:.1f}s）",
            f"- **剪映请只导入** `剪映导入/`（勿整夹导入）",
            f"- 画面：`剪映导入/clips/`（{len(clip_rows)} 段）",
            f"- 人声：`剪映导入/voiceover_clean.wav`",
            f"- 字幕：`剪映导入/captions_zh.srt`",
            f"- 预览：`预览/roughcut.mp4`",
            "",
            "## 剪映导入步骤",
            "",
            "1. 新建竖屏项目（1080×1920）",
            "2. **只打开** `剪映导入/` 文件夹",
            "3. `clips/` 按 01→末 顺序放视频轨；`voiceover_clean.wav` 对齐 0 秒",
            "4. 可选导入 `captions_zh.srt`",
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
        write_text(meta_dir / "timeline.md", "\n".join(lines))
        write_json(
            meta_dir / "timeline.json",
            {
                "title": part_title,
                "talk_source": talk_name,
                "width": width,
                "height": height,
                "voiceover_duration": dur,
                "clips": clip_rows,
                "vo_segments": local_vo_meta["timeline"],
                "layout": {
                    "import": "剪映导入/",
                    "preview": "预览/roughcut.mp4",
                    "meta": "工程/",
                },
            },
        )
        write_text(
            pkg / "README.md",
            f"""# {part_title}

按口播源拆分：`{talk_name}`（{dur:.1f}s）。

| 目录 | 用途 |
|------|------|
| **`剪映导入/`** | **只把这一层拖进剪映**（clips + 人声 + 字幕） |
| `预览/` | 已合成 roughcut，快速试看 |
| `工程/` | timeline / json，勿导入剪映 |

**不要**把整个 `{pkg_name}/` 文件夹导入剪映（会混入 roughcut 与工程文件，顺序会乱）。
""",
        )

        info = {
            "talk_source": talk_name,
            "package_dir": str(pkg),
            "import_dir": str(import_dir),
            "clip_count": len(clip_rows),
            "duration": round(dur, 3),
            "roughcut": str(rough) if rough.exists() else "",
            "global_range": [round(t0, 3), round(t1, 3)],
        }
        packages.append(info)
        print(
            f"  split package: {pkg_name}/  import=剪映导入/  "
            f"clips={len(clip_rows)}  dur={dur:.1f}s  "
            f"roughcut={'yes' if rough.exists() else 'no'}"
        )

    result = {
        "package_by_talk_dir": str(root),
        "packages": packages,
        "count": len(packages),
    }
    write_json(work_dir / "package_by_talk.json", result)
    print(f"  package_by_talk ready: {root} ({len(packages)} packages)")
    return result
