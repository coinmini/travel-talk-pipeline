"""Stage 3: clean talking-head transcripts into a single voiceover timeline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..utils import ensure_dir, run, ts_srt, write_json, write_text

# Path used in _resolve_talk_jobs fuzzy match


def _in_ranges(t0: float, t1: float, ranges: list[list[float]] | None) -> bool:
    if not ranges:
        return True
    mid = (t0 + t1) / 2
    for a, b in ranges:
        if a <= mid <= b:
            return True
    return False


def _should_drop(text: str, patterns: list[str]) -> bool:
    t = text.strip()
    for p in patterns:
        try:
            if re.search(p, t):
                return True
        except re.error:
            if p in t:
                return True
    return False


def _merge_short_segments(segs: list[dict], max_gap: float = 0.35, max_len: float = 5.0) -> list[dict]:
    """Merge consecutive tiny whisper fragments into readable caption lines.
    max_len 控制单句上限：过长会导致「一句口播」绑死大段露脸/盖画，语义难对齐。
    """
    if not segs:
        return []
    out = [dict(segs[0])]
    for s in segs[1:]:
        prev = out[-1]
        gap = s["start"] - prev["end"]
        span = s["end"] - prev["start"]
        if gap <= max_gap and span <= max_len:
            prev["end"] = s["end"]
            prev["text"] = (prev["text"].rstrip() + s["text"]).strip()
            # keep source fields from first
        else:
            out.append(dict(s))
    return out


def _resolve_talk_jobs(transcripts: dict[str, Any], talk_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build ordered jobs:
      {talk, ranges: [[s,e], ...] | None}
    Prefer `sequence` (supports splitting one file into multiple placements).
    Fallback: order / natural talks + ranges map.
    """
    talks = transcripts.get("talks") or []
    by_name = {t["name"]: t for t in talks}
    by_stem = {t["stem"]: t for t in talks}

    def resolve_file(key: str) -> dict | None:
        if key in by_name:
            return by_name[key]
        if key in by_stem:
            return by_stem[key]
        # fuzzy: endswith
        for t in talks:
            if t["name"].endswith(key) or t["stem"] == Path(key).stem:
                return t
        return None

    sequence = talk_cfg.get("sequence") or []
    if sequence:
        jobs = []
        for step in sequence:
            if isinstance(step, str):
                step = {"file": step}
            key = step.get("file") or step.get("name") or step.get("src")
            talk = resolve_file(str(key))
            if not talk:
                print(f"  [warn] sequence file not found: {key}")
                continue
            if "start" in step or "end" in step:
                s = float(step.get("start") or 0)
                e = float(step["end"]) if step.get("end") is not None else 1e9
                ranges = [[s, e]]
            elif step.get("ranges"):
                ranges = step["ranges"]
            else:
                ranges = None
            jobs.append({"talk": talk, "ranges": ranges})
        return jobs

    # order list
    order = talk_cfg.get("order") or []
    ranges_map = talk_cfg.get("ranges") or {}
    ordered_talks = []
    if order:
        for key in order:
            t = resolve_file(str(key))
            if t:
                ordered_talks.append(t)
        used = {t["name"] for t in ordered_talks}
        for t in talks:
            if t["name"] not in used:
                ordered_talks.append(t)
    else:
        ordered_talks = list(talks)

    jobs = []
    for talk in ordered_talks:
        ranges = ranges_map.get(talk["name"]) or ranges_map.get(talk["stem"])
        jobs.append({"talk": talk, "ranges": ranges})
    return jobs


def run_clean_vo(
    transcripts: dict[str, Any],
    work_dir: Path,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    talk_cfg = cfg.get("talk") or {}
    patterns = talk_cfg.get("drop_segment_patterns") or []
    # 注意：不能写 `or 0.15`，否则 pad=0.0 会被当成未设置
    _pad_raw = talk_cfg.get("pad_between_sec", 0.0)
    pad = float(0.0 if _pad_raw is None else _pad_raw)

    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    full_text_parts: list[str] = []

    jobs = _resolve_talk_jobs(transcripts, talk_cfg)
    for job in jobs:
        talk = job["talk"]
        ranges = job.get("ranges")
        name = talk["name"]
        segs = talk.get("segments") or []
        kept = []
        for seg in segs:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            if _should_drop(text, patterns):
                continue
            if not _in_ranges(seg["start"], seg["end"], ranges):
                continue
            kept.append(dict(seg))

        kept = _merge_short_segments(kept)
        if not kept:
            continue

        # 同一口播源内：强制时间轴不重叠，避免句尾被下一段再切一次（听感=重复最后一句）
        for i in range(1, len(kept)):
            prev_end = float(kept[i - 1]["end"])
            cur_start = float(kept[i]["start"])
            if cur_start < prev_end:
                kept[i]["start"] = prev_end
            if float(kept[i]["end"]) <= float(kept[i]["start"]) + 0.04:
                kept[i]["end"] = float(kept[i]["start"]) + 0.05

        for seg in kept:
            src_start = float(seg["start"])
            src_end = float(seg["end"])
            if src_end <= src_start + 0.04:
                continue
            # 段尾略收 20ms，减少边界把下一句词头带进来
            src_end = max(src_start + 0.05, src_end - 0.02)
            dur = max(0.05, src_end - src_start)
            text = (seg.get("text") or "").strip()
            item = {
                "src": talk["path"],
                "src_name": name,
                "src_start": round(src_start, 3),
                "src_end": round(src_end, 3),
                "timeline_start": round(cursor, 3),
                "timeline_end": round(cursor + dur, 3),
                "duration": round(dur, 3),
                "text": text,
            }
            timeline.append(item)
            full_text_parts.append(item["text"])
            cursor += dur + pad

    # remove trailing pad from total
    total = timeline[-1]["timeline_end"] if timeline else 0.0

    # Build continuous audio via ffmpeg concat of segment extracts
    vo_dir = ensure_dir(work_dir / "vo")
    parts_dir = ensure_dir(vo_dir / "parts")
    list_file = vo_dir / "concat.txt"
    wav_parts: list[Path] = []

    for i, item in enumerate(timeline):
        part = parts_dir / f"vo_{i:04d}.wav"
        # 解码后 atrim，时长与 timeline.duration 一致，避免 -ss/-to 边界把邻句卷进来
        ss = float(item["src_start"])
        dur = float(item["duration"])
        run(
            [
                "ffmpeg",
                "-y",
                "-i",
                item["src"],
                "-vn",
                "-af",
                f"atrim=start={ss:.6f}:duration={dur:.6f},asetpts=PTS-STARTPTS,aresample=44100",
                "-ac",
                "1",
                "-ar",
                "44100",
                str(part),
            ]
        )
        wav_parts.append(part)
        if pad > 0 and i < len(timeline) - 1:
            silence = parts_dir / f"silence_{i:04d}.wav"
            run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r=44100:cl=mono",
                    "-t",
                    str(pad),
                    str(silence),
                ]
            )
            wav_parts.append(silence)

    concat_lines = []
    for p in wav_parts:
        # concat demuxer needs escaped single quotes
        concat_lines.append(f"file '{p.resolve()}'")
    write_text(list_file, "\n".join(concat_lines) + "\n")

    vo_wav = vo_dir / "voiceover_clean.wav"
    if wav_parts:
        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(vo_wav),
            ]
        )
    else:
        # empty placeholder
        run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "1",
                str(vo_wav),
            ]
        )

    # SRT on cleaned timeline
    srt_lines = []
    for i, item in enumerate(timeline, 1):
        srt_lines.append(
            f"{i}\n{ts_srt(item['timeline_start'])} --> {ts_srt(item['timeline_end'])}\n{item['text']}\n"
        )
    srt_path = vo_dir / "captions_zh.srt"
    write_text(srt_path, "\n".join(srt_lines))
    write_text(vo_dir / "voiceover.txt", "\n".join(full_text_parts))

    result = {
        "total_duration": round(total, 3),
        "segment_count": len(timeline),
        "voiceover_wav": str(vo_wav),
        "captions_zh_srt": str(srt_path),
        "timeline": timeline,
        "full_text": "\n".join(full_text_parts),
    }
    write_json(work_dir / "voiceover.json", result)
    print(f"  cleaned VO: {len(timeline)} segments, {total:.1f}s")
    return result
