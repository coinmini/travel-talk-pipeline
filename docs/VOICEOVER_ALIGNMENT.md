# 口播时间轴对齐（开口 + 收尾）

成片要求：

1. **从文案第一个字开口**开始，不要片头空白 / BGM 空等  
2. **最后一个字完整**，不要在「自己」等收尾被裁断  

## 流程位置

```
transcribe                 clean_vo                          assemble / package
──────────                 ────────                          ─────────────────
Whisper word_timestamps →  首词收紧 src_start                 talk 同源裁切
segment + words[]          末词 RMS release → src_end         画面帧对齐 VO 总长
                           atrim 拼 voiceover_clean.wav       mux 以人声为准
```

默认始终开启。

| 阶段 | 做什么 |
|------|--------|
| `transcribe` | `word_timestamps=True`；首/尾词收紧句级边界（尾词 + 小量 headroom） |
| `clean_vo` | 再按 `words` 对齐开口；**末词声学 release 实测收尾**；无 words 时 RMS 开口兜底 |
| `assemble` | 段时长 nearest 帧；**Σ 帧吸到 VO 总长**（不额外冻长尾） |
| `package_by_talk` | 分轨 clips **与段长等长**；目录见 `docs/PACKAGE_BY_TALK.md` |

## 为何需要（开口）

Whisper 句级时间常把 **前置环境声/BGM** 算进第一句：

| 文件 | 句级 start（旧） | 词级第一字 |
|------|------------------|------------|
| 口播1 | 0.0s | ~3.6s「有人…」 |
| 口播2 | 0.0s（BGM 很响） | ~5.0s「刚…」 |

仅靠音量（RMS）不够：口播2 片头 BGM 也很大。**词级时间戳**才能对齐「第一个字」。

## 末字被切的根因（不要只靠加大 pad）

| 环节 | 问题 | 正确做法 |
|------|------|----------|
| Whisper `word.end` | token 边界，常早于声学 release | `detect_speech_release_end`：从末词峰后找能量跌落 |
| 固定大 `segment_tail_pad` | 太大=空尾/漂移，太小=仍切字 | 只作 release 后 ~60ms 安全底 |
| `_merge_short_segments` 丢 words | 末词时间戳停在前半句 | 合并时拼接 `words` |
| mux `-shortest` | 画面略短时裁掉人声尾巴 | 以人声为准；预览 mux 等长 |
| 剪映整夹导入 | roughcut + json 全进时间线 | 只导 `剪映导入/` |
| 末镜硬冻 0.5s+ | 嘴形字幕对不齐 | **禁止**额外冻尾；画面=人声段长 |

**尾部流程**：`word.end` → `detect_speech_release_end`（夹在下一词/文件尾）→ `+ segment_tail_pad_sec` 安全底。

## 配置（project.yaml）

```yaml
talk:
  pad_between_sec: 0.0
  trim_leading_silence: true
  speech_onset_peak_ratio: 0.45
  speech_onset_min_hold_sec: 0.12
  trim_leading_only_global_first: false
  # release 实测后的极小安全底（非硬补尾巴）
  segment_tail_pad_sec: 0.06

export:
  split_by_talk: true
```

## 重跑命令

改对齐逻辑后需 **强制重转写**（刷新 words）再往下跑：

```bash
python run_pipeline.py run <项目> --stage transcribe --force-transcribe
python run_pipeline.py run <项目> --from-stage clean_vo
```

日志示例：

```
[onset/words] 越南口播2.mp4: 0.00s → 5.00s | 刚刚结束的2026...
[tail/release] 越南口播1.mp4: word_end=61.62s → release=62.14s src_end=62.20s | 也重新认识自己
cleaned VO: N segments, TTs
```

`work/voiceover.json`：

- `timeline[].src_start` / `src_end`：源片开口与收尾  
- `speech_onset_trims`：片头裁切记录（若有）

## 代码入口

- `pipeline/stages/transcribe.py` — `word_timestamps=True`  
- `pipeline/stages/clean_vo.py` — `detect_speech_onset` / **`detect_speech_release_end`** / merge words  
- `pipeline/stages/assemble.py` — 帧对齐 VO 总长  
- `pipeline/stages/export_package.py` — `package_by_talk` 分层目录 + 段长对齐  
- `docs/PACKAGE_BY_TALK.md` — 剪映导入约定  
