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
| 末字被裁（如「自己」） | `word.end` 偏紧；`-shortest` 裁尾 | **RMS release** 收尾；mux 以人声为准 |
| 剪映分轨末尾缺画面 | 整夹导入 / Σ clips ≠ 人声 / 硬冻尾 | 只导 `剪映导入/`；段长=人声；**无额外冻尾** |
| 剪映嘴形字幕漂 | ceil 帧累积漂移 | assemble 总帧吸到 VO；分包 clip 等长 plan |

## 数据流

```
口播 mp4 ──whisper(+词级时间戳)──► segments + words[]
                │
                │  首词收紧 start；尾词 + release 收 end
                ▼
         clean_vo：drop/merge(words)/deoverlap
                │  开口 word/RMS；收尾 detect_speech_release_end
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
                         optional: ai_video_prepare / apply
                                              │
                              assemble：帧对齐 VO 总长
                                · talk：同 src_start（开口）
                                · 无额外末镜冻尾
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    ▼                                                   ▼
              export/roughcut.mp4                          package_by_talk/<口播>/
              package/（整片）                               剪映导入/ + 预览/ + 工程/
```

## 模块

- `pipeline/cli.py` — CLI 与阶段编排  
- `pipeline/config.py` — 默认配置与 yaml 合并  
- `pipeline/utils.py` — ffprobe / 抽帧 / 工具  
- `pipeline/stages/transcribe.py` — Whisper + **词级时间戳**  
- `pipeline/stages/clean_vo.py` — 拼 VO、**开口 + release 收尾**  
- `pipeline/stages/assemble.py` — 成片；Ken Burns；**帧对齐 VO**  
- `pipeline/stages/export_package.py` — 总包 + **`package_by_talk` 分层**  
- `pipeline/ai/*` / `pipeline/ai_video/*` — AI 标签与 Seedance B-roll  
- `docs/VOICEOVER_ALIGNMENT.md` — 开口与末字  
- `docs/PACKAGE_BY_TALK.md` — 剪映分包导入  
- `docs/IMAGE_MOTION.md` — 静态图 Ken Burns  

## 扩展点

1. **换 ASR**：替换 `stages/transcribe.py`，尽量输出 `words[]`（含 start/end）  
2. **换匹配打分**：改 `stages/match.py` 的 `_score_broll` / PRIMARY 词表  
3. **真·多模态 API**：可在 `apply_ai` 前增加自动写 `broll_vlm.json` 的脚本  
4. **AI 视频提供商**：当前仅 `seedance_cli`；新 provider 接在 `pipeline/ai_video/`  
5. **静态图动效**：`assemble.image_motion_*`  
6. **开口/收尾检测**：词级 + RMS；参数见 `talk.speech_onset_*` / `segment_tail_pad_sec`  



