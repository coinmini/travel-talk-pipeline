# 架构说明

## 问题与对策

| 线上问题 | 根因 | 对策 |
|----------|------|------|
| 口播句尾重复 | talk 源音 + VO 轨双轨；`pad=0 or 0.15`；句边界重叠 | 单人声轨；pad 真正为 0；同源段强制不重叠 |
| 中间口播嘴形漂 | 无声画面轨与整段人声累加漂移 | 分段时长对齐；talk 画面与 VO 同 src |
| B-roll 画面卡住 | 短镜冻帧硬撑长句 | 禁止冻帧；源不够长则换镜/露脸 |
| 说到河马却出自拍 | 启发式误标 + 泛「动物」命中 | VLM/手写真标签；主体词严格命中 |
| 露脸过多 | 空镜总时长不足 / 匹配乱抢 | 两轮匹配；目标 face_ratio；补空镜或 reuse |
| 静态图呆板 | 照片 B-roll 定格铺时长 | `assemble` 内建 Ken Burns 推拉/平移 |
| 片头空白几秒 | Whisper 句级 start 含 BGM/环境声 | **词级时间戳**收紧句首；`clean_vo` 对齐第一字 |

## 数据流

```
口播 mp4 ──whisper(+词级时间戳)──► segments + words[]
                │
                │  首词/尾词收紧 start/end
                ▼
         clean_vo：drop/merge/deoverlap
                │  再按 words 对齐开口（无 words 则 RMS）
                │  成片 t=0 = 全文第一个字
                ├──────────────────► voiceover timeline
                │                         │
                │                    atrim 拼接
                │                         ▼
                │               voiceover_clean.wav  ──┐
                │                                      │
B-roll ──tag(+AI)──► tags + scores                     │
                │                                      │
                └──────── match (2-pass) ──► picture_plan
                                              │
                         optional: ai_video_prepare
                              Seedance text2video
                         optional: ai_video_apply ─────┤
                                              │
                              assemble：
                                · talk：与 VO 同 src_start（已是开口）
                                · 视频 B-roll：trim
                                · 静态图：Ken Burns
                                              │
                                         concat + mux ──► roughcut.mp4
```

## 模块

- `pipeline/cli.py` — CLI 与阶段编排
- `pipeline/config.py` — 默认配置与 yaml 合并
- `pipeline/utils.py` — ffprobe / 抽帧 / 工具
- `pipeline/stages/transcribe.py` — Whisper + **词级时间戳**
- `pipeline/stages/clean_vo.py` — 拼 VO、**段首对齐第一字**
- `pipeline/stages/assemble.py` — 成片；静态图 Ken Burns
- `pipeline/stages/*` — 其余阶段
- `pipeline/ai/*` — Grok Build AI 产物约定
- `pipeline/ai_video/*` — Seedance/Dreamina 文生视频 B-roll
- `scripts/seedance_t2v.py` / `seedance_poll_download.py` — CLI 提交与下载
- `pipeline/templates/*` — 默认与示例 yaml
- `docs/IMAGE_MOTION.md` — 静态图 Ken Burns
- `docs/VOICEOVER_ALIGNMENT.md` — 口播开口对齐

## 扩展点

1. **换 ASR**：替换 `stages/transcribe.py`，尽量输出 `words[]`（含 start/end）  
2. **换匹配打分**：改 `stages/match.py` 的 `_score_broll` / PRIMARY 词表  
3. **真·多模态 API**：可在 `apply_ai` 前增加自动写 `broll_vlm.json` 的脚本  
4. **AI 视频提供商**：当前仅 `seedance_cli`；新 provider 接在 `pipeline/ai_video/`  
5. **静态图动效**：`assemble.image_motion_*`  
6. **开口检测**：词级优先；RMS 参数见 `talk.speech_onset_*`  



