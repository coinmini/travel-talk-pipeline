# 口播时间轴对齐（从第一个字开口）

成片要求：**画面与人声都从口播文案的第一个字开始**，不要片头空白 / BGM 空等。

## 流程位置

```
transcribe                    clean_vo                         assemble
─────────                    ────────                         ────────
Whisper + 词级时间戳    →    按 words 收紧每句 src_start/end  →  talk 画面同源裁切
segment.start/end              拼 voiceover_clean.wav            + 单人声轨 mux
写入 words[]                   成片时间轴 t=0 = 第一字
```

默认**始终开启**，无需额外阶段。

| 阶段 | 做什么 |
|------|--------|
| `transcribe` | `word_timestamps=True`；用首词/尾词收紧每段 `start`/`end` |
| `clean_vo` | 再按 `words` 对齐；无词级时回退 RMS 检测段首开口 |
| `match` / `assemble` | 使用已对齐的 `timeline`，talk 的 `src_start` 即开口时刻 |

## 为何需要

Whisper 句级时间常把 **前置环境声/BGM** 算进第一句：

| 文件 | 句级 start（旧） | 词级第一字 |
|------|------------------|------------|
| 口播1 | 0.0s | ~3.6s「有人…」 |
| 口播2 | 0.0s（BGM 很响） | ~5.0s「刚…」 |

仅靠音量（RMS）不够：口播2 片头 BGM 也很大，会误判「已在说话」。**词级时间戳**才能对齐「第一个字」。

## 配置（project.yaml）

```yaml
talk:
  pad_between_sec: 0.0
  # 词级对齐后仍可用 RMS 兜底（无 words 时）
  trim_leading_silence: true
  speech_onset_peak_ratio: 0.45
  speech_onset_min_hold_sec: 0.12
  # false=每个口播文件第一句都做 RMS 兜底；true=仅整片第一句
  trim_leading_only_global_first: false
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
cleaned VO: N segments, TTs
```

`work/voiceover.json` 中：

- `timeline[].src_start`：源片开口时刻  
- `speech_onset_trims`：裁切记录（若有）

## 代码入口

- `pipeline/stages/transcribe.py` — `transcribe_wav(..., word_timestamps=True)`  
- `pipeline/stages/clean_vo.py` — `detect_speech_onset` + words 收紧  
