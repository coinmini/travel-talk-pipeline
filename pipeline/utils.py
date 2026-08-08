"""Shared helpers: ffprobe, paths, time formatting, subprocess."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
TALK_NAME_RE = re.compile(r"口播|talk|vo[_-]?", re.I)
FINAL_NAME_RE = re.compile(r"终稿|final|export", re.I)


def run(cmd: list[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def ffprobe(path: Path) -> dict[str, Any]:
    r = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,bit_rate:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(r.stdout or "{}")


def media_info(path: Path) -> dict[str, Any]:
    data = ffprobe(path)
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    vs = next((s for s in streams if s.get("codec_type") == "video"), None)
    aus = [s for s in streams if s.get("codec_type") == "audio"]
    duration = float(fmt.get("duration") or 0)
    width = int(vs.get("width") or 0) if vs else 0
    height = int(vs.get("height") or 0) if vs else 0
    fps_raw = (vs or {}).get("r_frame_rate") or "0/1"
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps = 0.0
    return {
        "path": str(path.resolve()),
        "name": path.name,
        "stem": path.stem,
        "ext": path.suffix.lower(),
        "duration": duration,
        "size_mb": int(fmt.get("size") or 0) / 1024 / 1024,
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "has_audio": bool(aus),
        "is_vertical": height > width if width and height else True,
        "kind": "image" if path.suffix.lower() in IMAGE_EXTS else "video",
    }


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ts_srt(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def ts_clock(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m:02d}:{s:05.2f}"


def classify_asset_name(name: str) -> str:
    """Return talk | final | broll based on filename conventions."""
    stem = Path(name).stem
    if FINAL_NAME_RE.search(stem):
        return "final"
    if TALK_NAME_RE.search(stem):
        return "talk"
    return "broll"


def talk_sort_key(name: str) -> tuple:
    m = re.search(r"(\d+)", Path(name).stem)
    return (int(m.group(1)) if m else 999, name)


def extract_audio_wav(src: Path, dst: Path, sr: int = 16000) -> Path:
    ensure_dir(dst.parent)
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sr),
            str(dst),
        ]
    )
    return dst


def extract_thumbnail(src: Path, dst: Path, t: float | None = None, width: int = 360) -> Path:
    ensure_dir(dst.parent)
    info = media_info(src)
    if info["kind"] == "image":
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                f"scale={width}:-1",
                str(dst),
            ]
        )
        return dst
    dur = info["duration"] or 1.0
    at = t if t is not None else max(0.2, min(dur * 0.4, max(0.0, dur - 0.2)))
    run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{at:.3f}",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-1",
            str(dst),
        ]
    )
    return dst


def safe_stem(name: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", Path(name).stem, flags=re.U)
    return s[:max_len] or "clip"


def resolve_outro_path(
    project_dir: Path,
    cfg: dict[str, Any] | None = None,
) -> Path | None:
    """Locate fixed end-card video (default: 视频结尾.MP4).

    Search order:
      1) export.outro_video absolute / relative to project_dir
      2) project_dir/视频结尾.MP4
      3) project_dir.parent/视频结尾.MP4  (repo root next to 越南/)
    """
    exp = (cfg or {}).get("export") or {}
    raw = exp.get("outro_video")
    if raw is False or raw == "":
        return None
    candidates: list[Path] = []
    if raw:
        p = Path(str(raw))
        candidates.append(p if p.is_absolute() else project_dir / p)
        candidates.append(project_dir.parent / p.name)
    for name in ("视频结尾.MP4", "视频结尾.mp4", "outro.mp4", "ending.mp4"):
        candidates.append(project_dir / name)
        candidates.append(project_dir.parent / name)
    seen: set[str] = set()
    for c in candidates:
        key = str(c.resolve()) if c.exists() else str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.exists() and c.is_file():
            return c.resolve()
    return None


def prepare_outro_assets(
    outro_src: Path,
    work_dir: Path,
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    v_br: str = "6M",
    a_br: str = "192k",
) -> dict[str, Any]:
    """Normalize outro to project canvas; return silent video + wav + full A/V clip."""
    ensure_dir(work_dir)
    out_dir = ensure_dir(work_dir / "outro")
    full = out_dir / "outro_full.mp4"
    silent = out_dir / "outro_silent.mp4"
    wav = out_dir / "outro_audio.wav"

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps},setsar=1,format=yuv420p"
    )
    # full A/V for concat onto roughcut
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(outro_src),
            "-vf",
            vf,
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
            "-movflags",
            "+faststart",
            str(full),
        ]
    )
    # silent for CapCut picture track
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(full),
            "-an",
            "-c:v",
            "copy",
            str(silent),
        ]
    )
    # mono wav 44.1k for VO concat
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(full),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "44100",
            str(wav),
        ]
    )
    info = media_info(full)
    return {
        "src": str(outro_src),
        "full": str(full),
        "silent": str(silent),
        "wav": str(wav),
        "duration": float(info.get("duration") or 0),
        "width": width,
        "height": height,
        "fps": fps,
    }


def append_outro_av(
    body_mp4: Path,
    outro_full: Path,
    out_mp4: Path,
    *,
    v_br: str = "6M",
    a_br: str = "192k",
) -> Path:
    """Concatenate body roughcut + normalized outro (both A/V)."""
    ensure_dir(out_mp4.parent)
    # Re-encode concat for codec/resolution safety
    run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(body_mp4),
            "-i",
            str(outro_full),
            "-filter_complex",
            "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
            "-map",
            "[v]",
            "-map",
            "[a]",
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
            "-movflags",
            "+faststart",
            str(out_mp4),
        ]
    )
    return out_mp4


def concat_wavs(parts: list[Path], out_wav: Path) -> Path:
    """Concat mono wavs via concat demuxer."""
    ensure_dir(out_wav.parent)
    lst = out_wav.parent / f"_{out_wav.stem}_concat.txt"
    write_text(lst, "\n".join(f"file '{p.resolve()}'" for p in parts) + "\n")
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(lst),
            "-c",
            "copy",
            str(out_wav),
        ]
    )
    try:
        lst.unlink()
    except OSError:
        pass
    return out_wav
