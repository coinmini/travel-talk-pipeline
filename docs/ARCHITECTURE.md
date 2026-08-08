# 架构说明

## 问题与对策

| 线上问题 | 根因 | 对策 |
|----------|------|------|
| 口播句尾重复 | talk 源音 + VO 轨双轨；`pad=0 or 0.15`；句边界重叠 | 单人声轨；pad 真正为 0；同源段强制不重叠 |
| 中间口播嘴形漂 | 无声画面轨与整段人声累加漂移 | 分段时长对齐；talk 画面与 VO 同 src |
| B-roll 画面卡住 | 短镜冻帧硬撑长句 | 禁止冻帧；源不够长则换镜/露脸 |
| 说到河马却出自拍 | 启发式误标 + 泛「动物」命中 | VLM/手写真标签；主体词严格命中 |
| 露脸过多 | 空镜总时长不足 / 匹配乱抢 | 两轮匹配；目标 face_ratio；补空镜或 reuse |

## 数据流

```
口播 mp4 ──whisper──► segments.json
                │
                ├─drop/merge/deoverlap──► voiceover timeline
                │                              │
                │                         atrim 拼接
                │                              ▼
                │                    voiceover_clean.wav  ──┐
                │                                           │
B-roll ──tag(+AI)──► tags + scores                          │
                │                                           │
                └──────── match (2-pass) ──► picture_plan   │
                                              │             │
                         optional: ai_video_prepare         │
                              structured prompts            │
                              Seedance text2video           │
                         optional: ai_video_apply ──────────┤
                                              │             │
                                         无声视频段 concat    │
                                              ▼             │
                                         picture_track ────mux──► roughcut.mp4
```

## 模块

- `pipeline/cli.py` — CLI 与阶段编排
- `pipeline/config.py` — 默认配置与 yaml 合并
- `pipeline/utils.py` — ffprobe / 抽帧 / 工具
- `pipeline/stages/*` — 各阶段
- `pipeline/ai/*` — Grok Build AI 产物约定
- `pipeline/ai_video/*` — Seedance/Dreamina 文生视频 B-roll
- `scripts/seedance_t2v.py` / `seedance_poll_download.py` — CLI 提交与下载
- `pipeline/templates/*` — 默认与示例 yaml

## 扩展点

1. **换 ASR**：替换 `stages/transcribe.py`，保持 segments 结构  
2. **换匹配打分**：改 `stages/match.py` 的 `_score_broll` / PRIMARY 词表  
3. **真·多模态 API**：可在 `apply_ai` 前增加自动写 `broll_vlm.json` 的脚本；接口保持 JSON 契约即可  
4. **AI 视频提供商**：当前仅 `seedance_cli`（本机 dreamina）；新 provider 接在 `pipeline/ai_video/` 即可  

