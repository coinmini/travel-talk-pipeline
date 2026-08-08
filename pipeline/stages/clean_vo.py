"""Stage 3: clean talking-head transcripts into a single voiceover timeline."""

from __future__ import annotations

import math
import re
import struct
import wave
from pathlib import Path
from typing import Any

from ..utils import ensure_dir, run, ts_srt, write_json, write_text

# Path used in _resolve_talk_jobs fuzzy match


def _load_mono_pcm16(wav_path: Path) -> tuple[list[int], int]:
    """Return (samples, sample_rate) mono int16."""
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        n = w.getnframes()
        raw = w.readframes(n)
        sw = w.getsampwidth()
    if sw != 2:
        # fallback: re-read via ffmpeg to 16-bit mono is heavier; skip onset
        return [], sr
    samples = list(struct.unpack("<" + "h" * (len(raw) // 2), raw))
    if ch == 2:
        samples = samples[0::2]
    elif ch > 2:
        samples = samples[0::ch]
    return samples, sr


def detect_speech_onset(
    wav_path: Path,
    start_s: float,
    end_s: float,
    *,
    peak_ratio: float = 0.45,
    min_hold_sec: float = 0.12,
    abs_floor: float = 1800.0,
    frame_sec: float = 0.02,
) -> float:
    """Return absolute time (sec) of first sustained speech within [start_s, end_s].

    Uses RMS vs segment peak: finds first stretch above max(peak*ratio, abs_floor).
    If detection fails, returns start_s unchanged.
    """
    if not wav_path or not Path(wav_path).exists():
        return start_s
    if end_s <= start_s + 0.08:
        return start_s
    try:
        samples, sr = _load_mono_pcm16(Path(wav_path))
    except Exception:
        return start_s
    if not samples or sr <= 0:
        return start_s

    i0 = max(0, int(start_s * sr))
    i1 = min(len(samples), int(end_s * sr))
    if i1 - i0 < int(0.1 * sr):
        return start_s
    window = samples[i0:i1]
    step = max(1, int(sr * frame_sec))
    rms: list[float] = []
    for i in range(0, len(window) - step + 1, step):
        chunk = window[i : i + step]
        r = math.sqrt(sum(x * x for x in chunk) / len(chunk))
        rms.append(r)
    if not rms:
        return start_s
    peak = max(rms)
    if peak < abs_floor * 0.5:
        return start_s
    thr = max(peak * float(peak_ratio), float(abs_floor))
    need = max(1, int(float(min_hold_sec) / frame_sec))
    run = 0
    for i, r in enumerate(rms):
        if r >= thr:
            run += 1
            if run >= need:
                # back up to start of sustained region
                onset_rel = max(0, i - need + 1) * frame_sec
                onset_abs = start_s + onset_rel
                # keep a tiny headroom so first consonant isn't clipped
                onset_abs = max(start_s, onset_abs - 0.04)
                # don't eat more than 85% of the segment
                if onset_abs >= end_s - 0.15:
                    return start_s
                return round(onset_abs, 3)
        else:
            run = 0
    return start_s


def detect_speech_release_end(
    samples: list[int],
    sr: int,
    word_start_s: float,
    word_end_s: float,
    hard_cap_s: float,
    *,
    peak_ratio: float = 0.22,
    min_hold_sec: float = 0.08,
    max_extend_sec: float = 0.50,
    release_sec: float = 0.05,
    abs_floor: float = 1500.0,
    frame_sec: float = 0.02,
) -> float:
    """Return absolute end time covering the last word's acoustic release.

    Root cause of clipped finals (e.g. 「自己」): Whisper ``word.end`` is a token
    boundary and often sits *before* the voiced release finishes. Cutting at
    ``word.end`` (or a fixed pad that is too small on long finals / too large on
    short ones) either chops the last syllable or adds dead air.

    Method (mirrors onset, but from the last-word peak forward):
      1. Peak RMS inside the last word body
      2. After that peak, find first sustained drop below peak * peak_ratio
      3. + release_sec headroom; clamp to [word_end, hard_cap]

    On failure, returns ``word_end_s`` (caller may add a tiny safety floor).
    """
    if not samples or sr <= 0:
        return float(word_end_s)
    if hard_cap_s <= word_end_s + 0.01:
        return round(min(hard_cap_s, float(word_end_s)), 3)

    search0 = max(0.0, float(word_start_s) - 0.05)
    search1 = min(float(hard_cap_s), float(word_end_s) + float(max_extend_sec))
    if search1 <= search0 + 0.04:
        return round(min(hard_cap_s, float(word_end_s)), 3)

    i0 = max(0, int(search0 * sr))
    i1 = min(len(samples), int(search1 * sr))
    step = max(1, int(sr * frame_sec))
    window = samples[i0:i1]
    if len(window) < step:
        return round(min(hard_cap_s, float(word_end_s)), 3)

    rms: list[float] = []
    for i in range(0, len(window) - step + 1, step):
        chunk = window[i : i + step]
        rms.append(math.sqrt(sum(x * x for x in chunk) / len(chunk)))
    if not rms:
        return round(min(hard_cap_s, float(word_end_s)), 3)

    # Peak inside last-word body (not ambient after the phrase)
    peak = 0.0
    peak_i = 0
    for i, r in enumerate(rms):
        t = search0 + i * frame_sec
        if float(word_start_s) - 0.02 <= t <= float(word_end_s) + 0.08:
            if r > peak:
                peak = r
                peak_i = i
    if peak < abs_floor * 0.5:
        return round(min(hard_cap_s, float(word_end_s)), 3)

    thr = max(peak * float(peak_ratio), float(abs_floor) * 0.5)
    need = max(1, int(float(min_hold_sec) / frame_sec))
    # Start looking near word_end so early dips inside the syllable don't win
    start_i = max(peak_i, int((float(word_end_s) - search0 - 0.05) / frame_sec))
    start_i = max(0, min(start_i, len(rms) - 1))

    run = 0
    end_i: int | None = None
    for i in range(start_i, len(rms)):
        if rms[i] < thr:
            run += 1
            if run >= need:
                end_i = i - need + 1
                break
        else:
            run = 0

    if end_i is None:
        last_hi = start_i
        for i in range(start_i, len(rms)):
            if rms[i] >= thr:
                last_hi = i
        end_abs = search0 + (last_hi + 1) * frame_sec + float(release_sec)
    else:
        end_abs = search0 + end_i * frame_sec + float(release_sec)

    # Never earlier than Whisper token end; never past hard cap (next word / EOF)
    end_abs = max(float(word_end_s), end_abs)
    end_abs = min(end_abs, float(hard_cap_s))
    return round(end_abs, 3)


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

    Important: also concatenate ``words`` so later stages that tighten start/end
    from first/last word still see the full merged phrase (not only the first
    fragment's tokens — which would make the last syllable look like an earlier word).
    """
    if not segs:
        return []
    out = [dict(segs[0])]
    if "words" in out[0] and out[0]["words"] is not None:
        out[0]["words"] = list(out[0]["words"])
    for s in segs[1:]:
        prev = out[-1]
        gap = s["start"] - prev["end"]
        span = s["end"] - prev["start"]
        if gap <= max_gap and span <= max_len:
            prev["end"] = s["end"]
            prev["text"] = (prev["text"].rstrip() + s["text"]).strip()
            # Merge word timestamps so last-word end is the real final token
            sw = s.get("words") or []
            if sw:
                pw = prev.get("words")
                if pw is None:
                    prev["words"] = list(sw)
                else:
                    prev["words"] = list(pw) + list(sw)
        else:
            ns = dict(s)
            if "words" in ns and ns["words"] is not None:
                ns["words"] = list(ns["words"])
            out.append(ns)
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
    # 片头/段首对齐到真正开口（Whisper 常把前置环境声/BGM 算进第一句）
    trim_leading = bool(talk_cfg.get("trim_leading_silence", True))
    onset_peak_ratio = float(talk_cfg.get("speech_onset_peak_ratio") or 0.45)
    onset_hold = float(talk_cfg.get("speech_onset_min_hold_sec") or 0.12)
    # true=只裁整条 VO 的第一句；false=每个口播文件的第一句都裁
    trim_only_global_first = bool(talk_cfg.get("trim_leading_only_global_first", False))

    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    full_text_parts: list[str] = []
    onset_trims: list[dict[str, Any]] = []

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

        # 每个口播文件第一句：裁掉「第一个字之前」的空白/环境声
        # 每句段尾：按末词声学 release 实测收尾（Whisper word.end 常偏紧）
        is_first_job_seg = True
        file_dur = float(talk.get("duration") or 0) or None
        if file_dur is None or file_dur <= 0:
            try:
                from ..utils import media_info

                file_dur = float(media_info(Path(talk["path"])).get("duration") or 0) or None
            except Exception:
                file_dur = None
        # 实测 release 之后的极小安全底（编码器/播放器尾帧），不是「硬补尾巴」主手段
        tail_safety = float(talk_cfg.get("segment_tail_pad_sec") or 0.06)
        # 兼容旧配置：若仍写很大的 pad，降到合理安全底，真正延展靠 RMS release
        if tail_safety > 0.12:
            tail_safety = 0.06

        wav_path = Path(talk.get("wav") or "")
        if not wav_path.exists():
            wav_path = work_dir / "talk" / f"{talk.get('stem') or Path(name).stem}.wav"
        pcm_samples: list[int] = []
        pcm_sr = 0
        try:
            if wav_path.exists():
                pcm_samples, pcm_sr = _load_mono_pcm16(wav_path)
        except Exception:
            pcm_samples, pcm_sr = [], 0

        for seg_i, seg in enumerate(kept):
            src_start = float(seg["start"])
            src_end = float(seg["end"])
            if src_end <= src_start + 0.04:
                continue
            is_last_job_seg = seg_i == len(kept) - 1

            # hard cap: 下一句词首 / 文件尾，防止吞进下一句
            hard_cap = float(file_dur) - 0.02 if file_dur and file_dur > 0 else src_end + 1.0
            if not is_last_job_seg and seg_i + 1 < len(kept):
                next_start = float(kept[seg_i + 1]["start"])
                nw = kept[seg_i + 1].get("words") or []
                if nw:
                    try:
                        next_start = min(next_start, float(nw[0]["start"]))
                    except (TypeError, ValueError, KeyError, IndexError):
                        pass
                hard_cap = min(hard_cap, next_start - 0.01)

            # 优先用 Whisper 词级时间戳（transcribe 已写入 words）
            words = seg.get("words") or []
            w0 = w1 = None
            if words:
                try:
                    w0 = float(words[0].get("start"))
                    w1 = float(words[-1].get("end"))
                    # 已在 transcribe 收紧过 start；此处再保险一次
                    if w0 > src_start + 0.05:
                        onset_trims.append(
                            {
                                "src_name": name,
                                "text": (seg.get("text") or "")[:40],
                                "old_start": round(src_start, 3),
                                "new_start": round(max(0.0, w0 - 0.04), 3),
                                "trimmed_sec": round(w0 - src_start, 3),
                                "method": "word_ts",
                            }
                        )
                        print(
                            f"  [onset/words] {name}: {src_start:.2f}s → {w0:.2f}s "
                            f"| {(seg.get('text') or '')[:24]}"
                        )
                        src_start = max(0.0, w0 - 0.04)
                except (TypeError, ValueError, KeyError, IndexError):
                    w0 = w1 = None

            # 每个口播文件第一句：若无词级时间戳，回退 RMS 检测
            if trim_leading and is_first_job_seg and not words:
                if (not trim_only_global_first) or (not timeline):
                    onset = detect_speech_onset(
                        wav_path,
                        src_start,
                        src_end,
                        peak_ratio=onset_peak_ratio,
                        min_hold_sec=onset_hold,
                    )
                    if onset > src_start + 0.08:
                        onset_trims.append(
                            {
                                "src_name": name,
                                "text": (seg.get("text") or "")[:40],
                                "old_start": round(src_start, 3),
                                "new_start": round(onset, 3),
                                "trimmed_sec": round(onset - src_start, 3),
                                "method": "rms",
                            }
                        )
                        print(
                            f"  [onset/rms] {name}: first speech {src_start:.2f}s → {onset:.2f}s "
                            f"(trim {onset - src_start:.2f}s) | {(seg.get('text') or '')[:24]}"
                        )
                        src_start = onset
            is_first_job_seg = False

            # 段尾：用末词声学 release 实测，而不是固定 pad
            if w1 is not None and w0 is not None and pcm_samples and pcm_sr > 0:
                try:
                    last_w0 = float(words[-1].get("start"))
                except (TypeError, ValueError, KeyError, IndexError):
                    last_w0 = w1 - 0.25
                measured = detect_speech_release_end(
                    pcm_samples,
                    pcm_sr,
                    last_w0,
                    w1,
                    hard_cap,
                )
                # 再加极小安全底（AAC/播放器），夹在 hard_cap 内
                new_end = min(hard_cap, max(float(src_end), measured) + tail_safety)
                if new_end > float(src_end) + 0.02 or measured > w1 + 0.02:
                    print(
                        f"  [tail/release] {name}: word_end={w1:.2f}s → "
                        f"release={measured:.2f}s src_end={new_end:.2f}s "
                        f"| {(seg.get('text') or '')[:24]}"
                    )
                src_end = new_end
            else:
                # 无词级/无 wav：末句略放宽，非末句贴 hard_cap
                if is_last_job_seg:
                    src_end = min(hard_cap, float(src_end) + max(tail_safety, 0.12))
                else:
                    src_end = min(hard_cap, float(src_end))
                src_end = max(src_start + 0.05, src_end)

            # 最终夹紧
            src_end = min(src_end, hard_cap) if hard_cap > src_start else src_end
            if file_dur and file_dur > 0:
                src_end = min(src_end, float(file_dur) - 0.02)
            src_end = max(src_start + 0.05, src_end)

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
        "trim_leading_silence": trim_leading,
        "speech_onset_trims": onset_trims,
    }
    write_json(work_dir / "voiceover.json", result)
    print(f"  cleaned VO: {len(timeline)} segments, {total:.1f}s")
    if onset_trims:
        tsum = sum(float(x.get("trimmed_sec") or 0) for x in onset_trims)
        print(f"  speech onset trims: {len(onset_trims)} file(s), -{tsum:.2f}s leading non-speech")
    return result
