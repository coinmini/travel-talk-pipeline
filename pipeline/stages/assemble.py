"""Stage 6: assemble rough-cut vertical MP4.

音频策略（修复「最后一句重复」）：
  全程只使用一条 voiceover_clean.wav，画面轨按时长对齐后 mux。
  不再对 talk 段从口播源另切一份音轨（会与 VO 轨错位/重听句尾）。

嘴形：
  talk 画面从口播源按与 VO 相同的 src_start + duration 切视频（无音），
  与主音轨同一时间轴对齐。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..utils import (
    append_outro_av,
    ensure_dir,
    media_info,
    prepare_outro_assets,
    resolve_outro_path,
    run,
    write_json,
    write_text,
)


def _has_subtitles_filter() -> bool:
    try:
        r = run(["ffmpeg", "-hide_banner", "-filters"], check=False)
        return "subtitles" in (r.stdout or "")
    except Exception:
        return False


def _snap_dur(dur: float, fps: int, *, mode: str = "round") -> float:
    """Snap duration to whole frames.

    Default **round** + residual on last piece (see ``run_assemble``) so
    Σ video ≈ VO length without systematic ceil-drift (which desyncs CapCut
    dual-track lip-sync / captions). Use ceil only when explicitly needed.
    """
    import math

    if fps <= 0:
        return max(0.05, float(dur))
    raw = max(0.05, float(dur))
    if mode == "floor":
        frames = max(1, int(math.floor(raw * fps + 1e-9)))
    elif mode == "ceil":
        frames = max(1, int(math.ceil(raw * fps - 1e-9)))
    else:
        frames = max(1, int(round(raw * fps)))
    return frames / float(fps)


# 静态图 Ken Burns：按片序号轮换，避免全片同一动效
_IMAGE_MOTIONS = (
    "zoom_in",
    "zoom_out",
    "pan_right",
    "pan_left",
    "pan_up",
    "pan_down",
    "zoom_in_up",
    "zoom_out_right",
)


def _pick_image_motion(piece: dict[str, Any], index: int, styles: list[str] | None) -> str:
    explicit = (piece.get("motion") or piece.get("image_motion") or "").strip()
    if explicit:
        return explicit
    pool = [s for s in (styles or list(_IMAGE_MOTIONS)) if s]
    if not pool:
        pool = list(_IMAGE_MOTIONS)
    # 稳定轮换：同一 piece 重跑结果一致
    key = f"{piece.get('src_name') or piece.get('src') or index}:{index}"
    h = sum(ord(c) for c in key)
    return pool[h % len(pool)]


def _image_motion_vf(
    width: int,
    height: int,
    fps: int,
    dur: float,
    motion: str,
    *,
    zoom_max: float = 1.22,
) -> str:
    """Build ffmpeg vf for still-image Ken Burns (zoom/pan).

    Pre-scale to 2× canvas so zoompan has room, then animate to target size.
    """
    frames = max(1, int(round(float(dur) * fps)))
    z1 = max(1.05, float(zoom_max))
    # on: 0 .. frames-1
    # progress p = on/(frames-1) ≈ on/max(frames-1,1)
    denom = max(frames - 1, 1)

    m = (motion or "zoom_in").lower().strip()
    if m == "zoom_out":
        z = f"max({z1:.4f}-{(z1-1.0):.6f}*on/{denom},1.0)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif m == "pan_right":
        z = f"{(1.0 + (z1 - 1.0) * 0.55):.4f}"
        x = f"(iw-iw/zoom)*on/{denom}"
        y = "ih/2-(ih/zoom/2)"
    elif m == "pan_left":
        z = f"{(1.0 + (z1 - 1.0) * 0.55):.4f}"
        x = f"(iw-iw/zoom)*(1-on/{denom})"
        y = "ih/2-(ih/zoom/2)"
    elif m == "pan_down":
        z = f"{(1.0 + (z1 - 1.0) * 0.55):.4f}"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*on/{denom}"
    elif m == "pan_up":
        z = f"{(1.0 + (z1 - 1.0) * 0.55):.4f}"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*(1-on/{denom})"
    elif m == "zoom_in_up":
        z = f"min(1.0+{(z1-1.0):.6f}*on/{denom},{z1:.4f})"
        x = "iw/2-(iw/zoom/2)"
        y = f"(ih-ih/zoom)*(1-0.65*on/{denom})"
    elif m == "zoom_out_right":
        z = f"max({z1:.4f}-{(z1-1.0):.6f}*on/{denom},1.0)"
        x = f"(iw-iw/zoom)*on/{denom}"
        y = "ih/2-(ih/zoom/2)"
    else:  # zoom_in default
        z = f"min(1.0+{(z1-1.0):.6f}*on/{denom},{z1:.4f})"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"

    # 2× 画布保证 zoom/pan 不露黑边
    sw, sh = width * 2, height * 2
    return (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,"
        f"crop={sw}:{sh},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
        f"setsar=1,format=yuv420p"
    )


def _v_filters(
    width: int,
    height: int,
    fps: int,
    start: float,
    dur: float,
    *,
    allow_freeze: bool = False,
    src_span: float | None = None,
) -> str:
    take = dur
    pad = 0.0
    if allow_freeze and src_span is not None and src_span + 0.05 < dur:
        take = max(0.05, src_span)
        pad = dur - take
    parts = [
        f"trim=start={start:.6f}:duration={take:.6f}",
        "setpts=PTS-STARTPTS",
        f"fps={fps}",
    ]
    if pad > 0.04:
        parts.append(f"tpad=stop_mode=clone:stop_duration={pad:.6f}")
    parts.extend(
        [
            f"scale={width}:{height}:force_original_aspect_ratio=increase",
            f"crop={width}:{height}",
            "setsar=1",
            "format=yuv420p",
            f"trim=duration={dur:.6f}",
            "setpts=PTS-STARTPTS",
        ]
    )
    return ",".join(parts)


def _run_ffmpeg(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        msg = tail[-5:] if tail else ["ffmpeg failed"]
        raise RuntimeError("ffmpeg failed:\n" + "\n".join(msg))


def _render_video_only(
    piece: dict[str, Any],
    out: Path,
    *,
    width: int,
    height: int,
    fps: int,
    dur: float,
    index: int = 0,
    image_motion: bool = True,
    image_motion_styles: list[str] | None = None,
    image_zoom_max: float = 1.22,
) -> None:
    """Render a silent video clip of exact duration `dur`."""
    ensure_dir(out.parent)
    src = piece["src"]
    ss = float(piece.get("src_start") or 0)
    is_image = Path(src).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".heic"}
    src_span = None
    if piece.get("src_end") is not None:
        src_span = max(0.05, float(piece["src_end"]) - ss)

    if is_image:
        if image_motion and piece.get("image_motion") is not False:
            motion = _pick_image_motion(piece, index, image_motion_styles)
            piece["image_motion_applied"] = motion
            vf = _image_motion_vf(
                width, height, fps, dur, motion, zoom_max=image_zoom_max
            )
        else:
            motion = "none"
            piece["image_motion_applied"] = motion
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},fps={fps},setsar=1,format=yuv420p,"
                f"trim=duration={dur:.6f},setpts=PTS-STARTPTS"
            )
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(src),
                "-t",
                f"{dur:.6f}",
                "-vf",
                vf,
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
        return

    # talk：优先不超过 src_end；若 VO 段略长于源跨度/帧对齐后变长，冻尾帧补齐
    # （剪映分轨导入时画面绝不能短于人声段）
    # broll：按 src_start + dur 取，源不够则冻尾
    allow_freeze = True
    if piece.get("type") == "talk" and src_span is not None:
        # 需要输出的时长已在上层 ceil 对齐；源不够时用冻帧，禁止裁短 VO
        pass

    vf = _v_filters(
        width,
        height,
        fps,
        ss,
        dur,
        allow_freeze=allow_freeze,
        src_span=src_span,
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            vf,
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


def run_assemble(
    picture_plan: dict[str, Any],
    voiceover: dict[str, Any],
    work_dir: Path,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    out_cfg = cfg.get("output") or {}
    width = int(out_cfg.get("width") or 1080)
    height = int(out_cfg.get("height") or 1920)
    fps = int(out_cfg.get("fps") or 30)
    v_br = out_cfg.get("video_bitrate") or "6M"
    a_br = out_cfg.get("audio_bitrate") or "192k"
    exp = cfg.get("export") or {}
    burn = bool(exp.get("burn_subtitles", True)) and _has_subtitles_filter()
    if bool(exp.get("burn_subtitles", True)) and not _has_subtitles_filter():
        print(
            "  [warn] ffmpeg 无 subtitles 滤镜（未编译 libass），跳过烧字幕；"
            "请用 package/captions_zh.srt 在剪映导入"
        )
    font = exp.get("subtitle_font") or "PingFang SC"
    fontsize = int(exp.get("subtitle_fontsize") or 42)
    margin_v = int(exp.get("subtitle_margin_v") or 160)
    asm_cfg = cfg.get("assemble") or {}
    image_motion = bool(asm_cfg.get("image_motion", True))
    image_motion_styles = asm_cfg.get("image_motion_styles")
    if isinstance(image_motion_styles, str):
        image_motion_styles = [s.strip() for s in image_motion_styles.split(",") if s.strip()]
    image_zoom_max = float(asm_cfg.get("image_zoom_max") or 1.22)

    vo_wav = Path(voiceover.get("voiceover_wav") or "")
    if not vo_wav.exists():
        raise RuntimeError("voiceover wav missing")

    export_dir = ensure_dir(work_dir / "export")
    parts_dir = ensure_dir(work_dir / "assemble_parts")
    for old in parts_dir.glob("av_*.mp4"):
        old.unlink(missing_ok=True)
    for old in parts_dir.glob("v_*.mp4"):
        old.unlink(missing_ok=True)

    pieces = picture_plan.get("picture_timeline") or []
    if not pieces:
        raise RuntimeError("picture_timeline is empty; run match first")

    # 画面时长与 VO 段对齐：nearest 帧；总帧数吸到 VO 总长，避免 ceil 累积漂移
    # （剪映分轨 = 各 clip 相加，漂移会表现为嘴形/字幕错位）
    part_paths: list[Path] = []
    total = 0.0
    image_motion_count = 0
    plan_durs = [float(p.get("duration") or 0.1) for p in pieces]
    snap_durs = [_snap_dur(d, fps, mode="round") for d in plan_durs]
    vo_total = float(media_info(vo_wav).get("duration") or sum(plan_durs) or 0)
    if vo_total > 0.05 and snap_durs:
        target_frames = max(1, int(round(vo_total * fps)))
        got_frames = sum(max(1, int(round(d * fps))) for d in snap_durs)
        # 把差额记到最后一段，使 Σ 画面帧 ≈ 人声总长（不额外冻长尾）
        delta_f = target_frames - got_frames
        if delta_f != 0:
            last_f = max(1, int(round(snap_durs[-1] * fps)) + delta_f)
            snap_durs[-1] = last_f / float(fps)

    for i, piece in enumerate(pieces):
        dur = snap_durs[i] if i < len(snap_durs) else _snap_dur(
            float(piece.get("duration") or 0.1), fps, mode="round"
        )
        out = parts_dir / f"v_{i:04d}.mp4"
        src_name = str(piece.get("src_name", ""))[:22]
        is_img = Path(str(piece.get("src") or "")).suffix.lower() in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".heic",
        }
        motion_note = ""
        if is_img and image_motion:
            motion_note = f" motion={_pick_image_motion(piece, i, image_motion_styles)}"
            image_motion_count += 1
        print(
            f"  render video {i+1}/{len(pieces)}: {piece['type']} "
            f"{src_name}{motion_note} "
            f"src={float(piece.get('src_start') or 0):.2f} dur={dur:.2f}"
        )
        _render_video_only(
            piece,
            out,
            width=width,
            height=height,
            fps=fps,
            dur=dur,
            index=i,
            image_motion=image_motion,
            image_motion_styles=image_motion_styles
            if isinstance(image_motion_styles, list)
            else None,
            image_zoom_max=image_zoom_max,
        )
        info = media_info(out)
        if abs(float(info["duration"]) - dur) > 0.2:
            print(
                f"    [warn] video dur {info['duration']:.3f}s != plan {dur:.3f}s"
            )
        total += float(info["duration"])
        part_paths.append(out)

    list_file = parts_dir / "concat.txt"
    write_text(
        list_file,
        "\n".join(f"file '{p.resolve()}'" for p in part_paths) + "\n",
    )

    video_only = export_dir / "picture_track.mp4"
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
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

    # 单一人声轨 + 画面（禁止 talk 再叠一份源音）
    vo_dur = float(media_info(vo_wav)["duration"] or 0)
    vid_dur = float(media_info(video_only)["duration"] or 0)
    # 对齐到较短者，避免尾部空镜或空声；若画面略短则冻尾帧
    pad = max(0.0, min(vo_dur, total) - vid_dur + 0.05)
    rough = export_dir / "roughcut_nosub.mp4"
    if pad > 0.08:
        fc = f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.3f}[v]"
    else:
        fc = "[0:v]null[v]"
    out_dur = min(vo_dur, vid_dur + max(pad, 0)) if vo_dur > 0 else vid_dur
    # 以人声为准
    out_dur = vo_dur if vo_dur > 0 else vid_dur
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_only),
            "-i",
            str(vo_wav),
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-map",
            "1:a",
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
            "-ar",
            "44100",
            "-ac",
            "1",
            "-t",
            f"{out_dur:.3f}",
            "-movflags",
            "+faststart",
            str(rough),
        ]
    )

    final_path = export_dir / "roughcut.mp4"
    burned = False
    srt = voiceover.get("captions_zh_srt")
    if burn and srt and Path(srt).exists():
        srt_local = export_dir / "burn.srt"
        srt_local.write_text(Path(srt).read_text(encoding="utf-8"), encoding="utf-8")
        style = (
            f"FontName={font},FontSize={fontsize},PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
            f"Alignment=2,MarginV={margin_v}"
        )
        vf = f"subtitles=burn.srt:force_style='{style}'"
        r = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                rough.name,
                "-vf",
                vf,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                v_br,
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                final_path.name,
            ],
            cwd=str(export_dir),
            capture_output=True,
            text=True,
        )
        burned = r.returncode == 0 and final_path.exists()
        if not burned:
            print("  [warn] burn subtitles failed; using no-sub version")
    if not burned:
        run(["ffmpeg", "-y", "-i", str(rough), "-c", "copy", str(final_path)])

    # 固定片尾：每个视频末尾必须接上（视频结尾.MP4）
    outro_meta: dict[str, Any] | None = None
    project_dir = work_dir.parent if work_dir.name == "work" else work_dir
    outro_src = resolve_outro_path(project_dir, cfg)
    if outro_src is None:
        # 兼容 work 在更深一层
        outro_src = resolve_outro_path(work_dir.parent, cfg)
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
            body = final_path
            tmp_out = export_dir / "roughcut_with_outro.mp4"
            append_outro_av(
                body,
                Path(assets["full"]),
                tmp_out,
                v_br=v_br,
                a_br=a_br,
            )
            # replace final + nosub body with outro version
            run(["ffmpeg", "-y", "-i", str(tmp_out), "-c", "copy", str(final_path)])
            # nosub also get outro for consistency
            append_outro_av(
                rough,
                Path(assets["full"]),
                export_dir / "roughcut_nosub_with_outro.mp4",
                v_br=v_br,
                a_br=a_br,
            )
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(export_dir / "roughcut_nosub_with_outro.mp4"),
                    "-c",
                    "copy",
                    str(rough),
                ]
            )
            try:
                tmp_out.unlink()
                (export_dir / "roughcut_nosub_with_outro.mp4").unlink()
            except OSError:
                pass
            outro_meta = assets
            print(
                f"  [outro] appended {outro_src.name} "
                f"({assets.get('duration', 0):.2f}s) → {final_path.name}"
            )
        except Exception as e:
            print(f"  [warn] outro append failed: {e}")
    else:
        print("  [outro] skip: 未找到 视频结尾.MP4（可放在项目目录或 export.outro_video）")

    info = media_info(final_path)
    result = {
        "roughcut": str(final_path),
        "roughcut_nosub": str(rough),
        "picture_track": str(video_only),
        "width": width,
        "height": height,
        "fps": fps,
        "piece_count": len(part_paths),
        "video_duration": round(total, 3),
        "audio_duration": round(vo_dur, 3),
        "actual_duration": info.get("duration"),
        "lip_sync_mode": "single_vo_track",
        "audio_mode": "voiceover_clean_only",
        "image_motion": image_motion,
        "image_motion_count": image_motion_count,
        "image_zoom_max": image_zoom_max,
        "outro": outro_meta,
    }
    write_json(work_dir / "assemble.json", result)
    print(
        f"  exported: {final_path} ({info.get('duration', 0):.1f}s, "
        f"audio=single_vo, pieces={len(part_paths)}"
        f"{f', image_kenburns={image_motion_count}' if image_motion_count else ''}"
        f"{', outro=yes' if outro_meta else ''})"
    )
    return result
