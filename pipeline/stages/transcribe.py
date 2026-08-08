"""Stage 2: transcribe talking-head videos with mlx_whisper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils import ensure_dir, extract_audio_wav, ts_srt, write_json, write_text


def _segments_to_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(
            f"{i}\n{ts_srt(seg['start'])} --> {ts_srt(seg['end'])}\n{seg['text'].strip()}\n"
        )
    return "\n".join(lines)


def transcribe_wav(wav_path: Path, model: str, language: str) -> dict[str, Any]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(wav_path),
        path_or_hf_repo=model,
        language=language,
        # 词级时间戳：用于把句首/句尾对齐到真正开口/收尾（去掉段前 BGM/空白）
        word_timestamps=True,
        verbose=False,
    )
    segments = []
    for seg in result.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg["start"])
        end = float(seg["end"])
        words_out: list[dict[str, Any]] = []
        for w in seg.get("words") or []:
            wt = (w.get("word") or w.get("text") or "").strip()
            if not wt:
                continue
            try:
                ws = float(w.get("start"))
                we = float(w.get("end"))
            except (TypeError, ValueError):
                continue
            if we <= ws:
                continue
            words_out.append(
                {
                    "word": wt,
                    "start": ws,
                    "end": we,
                    "probability": w.get("probability"),
                }
            )
        # 用首尾词收紧段边界（Whisper 段 start 常含前置环境声）
        if words_out:
            start = float(words_out[0]["start"])
            end = float(words_out[-1]["end"])
            # 段首略留 40ms，避免切掉声母
            start = max(0.0, start - 0.04)
            if end <= start + 0.05:
                end = start + 0.05
        segments.append(
            {
                "start": start,
                "end": end,
                "text": text,
                "words": words_out,
            }
        )
    return {
        "text": (result.get("text") or "").strip(),
        "segments": segments,
    }


def run_transcribe(
    asset_index: dict[str, Any],
    work_dir: Path,
    cfg: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    talk_dir = ensure_dir(work_dir / "talk")
    wcfg = cfg.get("whisper") or {}
    model = wcfg.get("model") or "mlx-community/whisper-large-v3-mlx"
    language = wcfg.get("language") or "zh"

    talks_out = []
    for item in asset_index.get("talk") or []:
        src = Path(item["path"])
        stem = src.stem
        out_base = talk_dir / stem
        meta_path = Path(str(out_base) + ".json")
        if meta_path.exists() and not force:
            import json

            cached = json.loads(meta_path.read_text(encoding="utf-8"))
            talks_out.append(cached)
            print(f"  [skip] already transcribed: {src.name}")
            continue

        wav = Path(str(out_base) + ".wav")
        print(f"  extract audio: {src.name}")
        extract_audio_wav(src, wav)
        print(f"  whisper: {src.name}")
        tr = transcribe_wav(wav, model=model, language=language)

        rec = {
            "name": src.name,
            "stem": stem,
            "path": item["path"],
            "duration": item.get("duration") or 0,
            "wav": str(wav),
            "text": tr["text"],
            "segments": tr["segments"],
            "srt": str(out_base) + ".srt",
            "txt": str(out_base) + ".txt",
        }
        write_text(Path(rec["txt"]), tr["text"])
        write_text(Path(rec["srt"]), _segments_to_srt(tr["segments"]))
        write_json(meta_path, rec)
        talks_out.append(rec)
        print(f"  -> {len(tr['segments'])} segments, {len(tr['text'])} chars")

    result = {
        "model": model,
        "language": language,
        "talks": talks_out,
    }
    write_json(work_dir / "transcripts.json", result)
    return result
