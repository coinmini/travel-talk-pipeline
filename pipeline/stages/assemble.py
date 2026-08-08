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

from ..utils import ensure_dir, media_info, run, write_json, write_text


def _has_subtitles_filter() -> bool:
    try:
        r = run(["ffmpeg", "-hide_banner", "-filters"], check=False)
        return "subtitles" in (r.stdout or "")
    except Exception:
        return False


def _snap_dur(dur: float, fps: int) -> float:
    frames = max(1, int(round(float(dur) * fps)))
    return frames / float(fps)


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

    # talk：严格不超过 src_end，避免把下一句画面/口型带进来
    # broll：按 src_start + dur 取（match 已保证源够长）
    allow_freeze = False
    if piece.get("type") == "talk" and src_span is not None:
        # 时长以 min(plan, src_span) 为准，禁止读出 src_end
        dur = min(dur, src_span)
        dur = _snap_dur(dur, fps)
        if dur > src_span:
            dur = src_span

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

    # 画面时长必须与 VO 段一致：优先用 piece.duration（来自 clean_vo）
    part_paths: list[Path] = []
    total = 0.0
    for i, piece in enumerate(pieces):
        dur = float(piece.get("duration") or 0.1)
        # talk 再保险：不超过源跨度
        if piece.get("type") == "talk":
            span = float(piece.get("src_end") or 0) - float(piece.get("src_start") or 0)
            if span > 0.05:
                dur = min(dur, span)
        dur = _snap_dur(dur, fps)
        out = parts_dir / f"v_{i:04d}.mp4"
        print(
            f"  render video {i+1}/{len(pieces)}: {piece['type']} "
            f"{str(piece.get('src_name', ''))[:22]} "
            f"src={float(piece.get('src_start') or 0):.2f} dur={dur:.2f}"
        )
        _render_video_only(
            piece, out, width=width, height=height, fps=fps, dur=dur
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
    }
    write_json(work_dir / "assemble.json", result)
    print(
        f"  exported: {final_path} ({info.get('duration', 0):.1f}s, "
        f"audio=single_vo, pieces={len(part_paths)})"
    )
    return result
