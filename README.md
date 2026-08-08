# Travel Talk Pipeline · 旅拍口播自动剪辑流水线

把日常拍摄的 **口播 A-roll + 沿途 B-roll/照片**，自动剪成：

1. **粗剪竖屏 MP4**（1080×1920，可预览 / 可先发）
2. **剪映友好工程包**（分段 clips + 人声 + SRT + `timeline.md`）

面向：**竖屏多平台、旅拍记录 + 口播解说**；最后 10%～20% 网感包装仍建议在剪映完成。

---

## 设计目标

| 目标 | 做法 |
|------|------|
| 口播不「说完又重复最后一句」 | **全程只叠一条**精剪人声轨；画面与人声分段对齐 |
| 口播嘴形基本对齐 | 露脸画面与 VO 段使用**同一 `src_start/src_end`** |
| 大量盖 B-roll、少露脸 | `target_face_ratio` + 两轮匹配（先锁主体词） |
| 说到什么看到什么 | 口播关键词 ↔ B-roll 标签严格命中（河马/水鸟/倒影/船…） |
| B-roll 不复用、不冻帧硬撑 | `max_reuse: 1`；源不够长则换镜或露脸 |
| 可选「更聪明」 | **Grok Build 会话**填 JSON（标签/叙事，非外部 API） |
| 本地 B-roll 语义不够 | **Seedance 文生视频**补真实感空镜（本机 dreamina CLI） |

---

## 架构一览

```
素材目录/
  口播*.mp4          → A-roll
  *.mp4 / 照片       → B-roll
  project.yaml       → 叙事顺序 / 比例 / 规则
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  pipeline（纯代码编排）                               │
│  ingest → transcribe → clean_vo → tag_broll         │
│       → [ai_prepare → apply_ai] → match             │
│       → [ai_video_prepare → seedance → apply]       │
│       → assemble（静态图 Ken Burns）→ package        │
└─────────────────────────────────────────────────────┘
        │
        ▼
  work/export/roughcut.mp4
  work/package/   （剪映导入）
```

| 阶段 | 作用 |
|------|------|
| `ingest` | 扫描素材、抽缩略图、分类 talk / broll / final |
| `transcribe` | 本地 `mlx_whisper` 转写口播 |
| `clean_vo` | 去口误、按 `sequence` 拼人声；**防句尾重叠** |
| `tag_broll` | 启发式标签（可被 AI 标签覆盖） |
| `ai_prepare` | 可选：导出缩略图 + 口播文案分析包 |
| `apply_ai` | 可选：消费 `work/ai/*.json` |
| `match` | 两轮匹配：主体词优先 → 其余盖空镜 / 低露脸 |
| `ai_video_prepare` | 可选：导出 `work/ai_video` 结构化 prompt 包 |
| `ai_video_apply` | 可选：把 Seedance 成片写回 `picture_plan`（禁止复用） |
| `assemble` | **无声画面轨** + **单人声轨** mux 成片；静态图自动 Ken Burns 推拉/平移 |
| `package` | 导出 clips / timeline / SRT |

### 音频策略（重要）

- 人声：仅 `voiceover_clean.wav`（由各口播段 `atrim` 精确拼接）
- 画面：talk / broll 都只出 **无声** 片段再 concat
- 禁止对 talk 再叠一份源片音轨（否则易出现「最后一句重复」）

### 匹配策略（重要）

1. **Pass 1**：含河马/水鸟/倒影/船等 **主体词** 的口播句，优先占用带对应标签的 B-roll  
2. **Pass 2**：其余句默认盖空镜；仅开场钩子 / CTA 等短句强制露脸  
3. 「河马」**不能**被泛「动物/自拍」顶替；无对应镜头则宁可不盖  

---

## 环境

- macOS（当前按 Apple Silicon + `mlx_whisper` 验证）
- `ffmpeg` / `ffprobe`
- Python 3.10+（推荐 conda 环境）

```bash
# 依赖
brew install ffmpeg
pip install -r requirements.txt
# ASR
pip install mlx mlx-whisper
```

---

## 快速开始

```bash
git clone <this-repo>
cd video_cut   # 或你的 clone 目录

# 准备素材目录
mkdir -p my_trip
# 放入：口播1.mp4 口播2.mp4 … 以及沿途视频/照片
cp examples/kenya.project.yaml my_trip/project.yaml
# 按实际文件名改 sequence

python run_pipeline.py init my_trip --title 我的旅程
python run_pipeline.py run my_trip
```

成片：`my_trip/work/export/roughcut.mp4`  
剪映包：`my_trip/work/package/`

### 素材命名约定

| 命名 | 角色 |
|------|------|
| 含 `口播` / `talk` / `vo` | 口播主轴 |
| 含 `终稿` / `final` | 参考成片（不参与拼接） |
| 其他视频/照片 | B-roll |

---

## 可选 AI 层（Grok Build，不是外部 API）

分析在 **当前 Grok Build / 同类 Agent 会话** 中完成：看缩略图、读口播，写 JSON；代码只编排与应用。

```bash
# 1) 生成分析包（JSON 未填时会暂停后续）
python run_pipeline.py run my_trip --ai

# 2) 按 work/ai/AGENT_PROMPT.md 填写：
#    work/ai/broll_vlm.json       # 真标签 + score
#    work/ai/narrative_plan.json  # 强制露脸句 / 应盖画句

# 3) 应用并出片
python run_pipeline.py run my_trip --from-stage apply_ai
```

示例格式见 `examples/ai/`。

| 文件 | 作用 |
|------|------|
| `broll_vlm.json` | 河马/水鸟/自拍等真标签 + 0–10 高光分 |
| `narrative_plan.json` | `force_face_texts` / `prefer_broll_texts` / 目标露脸比 |
| `highlight_scores.json` | 可选；分数也可写在 vlm 的 `score` 字段 |

---

## 可选 AI 视频 B-roll（Seedance / 即梦 CLI）

当库内 B-roll **语义盖不上口播**（如 AI/展会观点段却是海景）时，用 **文生视频** 补真实感空镜。

- 登录与积分在本机 `dreamina` 完成；**仓库不保存任何 key/token**
- Prompt 必须用 skill 结构（【基础设定】/【画面内容】…），不要压成长白话
- **一条生成视频只盖一个 piece**（`max_reuse: 1`）

```bash
# match 之后
python run_pipeline.py run my_trip --from-stage ai_video_prepare
# 编辑 work/ai_video/prompts/*.txt 与 manifest.yaml
python scripts/seedance_t2v.py --work my_trip/work
python scripts/seedance_poll_download.py \
  --map my_trip/work/ai_video/id_map.txt \
  --out my_trip/work/ai_video/videos
python run_pipeline.py run my_trip --from-stage ai_video_apply   # 含 assemble/package
```

详见：`docs/AI_VIDEO_SEEDANCE.md`、`SEEDANCE_CLI.md`、`examples/ai_video/`。

---

## 常用命令

```bash
python run_pipeline.py stages

# 只跑部分阶段
python run_pipeline.py run my_trip --stage match --stage assemble --stage package

# 从某阶段跑到结束
python run_pipeline.py run my_trip --from-stage clean_vo
python run_pipeline.py run my_trip --from-stage apply_ai

# 强制重转写
python run_pipeline.py run my_trip --stage transcribe --force-transcribe
python run_pipeline.py run my_trip --from-stage clean_vo
```

---

## `project.yaml` 关键字段

```yaml
title: 肯尼亚纳瓦沙湖

talk:
  sequence:                    # 口播拼接顺序（可拆同一文件）
    - { file: 口播素材3.mp4, start: 0.9 }
    - { file: 口播素材1.mp4, start: 0, end: 17.4 }
  drop_segment_patterns:       # 丢掉口误/废句
    - "^这是什么"
  pad_between_sec: 0.0         # 必须为 0，避免句间静音错位

match:
  reuse_broll: false
  max_reuse: 1                 # 改 2 可显著降低露脸（空镜可复用）
  target_face_ratio: 0.12      # 目标露脸；受空镜总时长硬约束
  force_face_open_segments: 2  # 开场前 N 句强制主角露脸
  broll_pack_max_segments: 1   # 单句单镜，语义更好对齐

assemble:
  image_motion: true           # 静态图自动推拉/平移（Ken Burns）
  image_zoom_max: 1.22         # 推拉幅度
```

完整示例：`examples/kenya.project.yaml`。  
静态图动效说明：`docs/IMAGE_MOTION.md`。

---

## 产物结构

```
my_trip/work/
  export/roughcut.mp4          # 粗剪成片
  package/
    timeline.md                # 剪映操作说明 + 分镜表
    clips/                     # 按序号竖屏片段
    voiceover_clean.wav
    captions_zh.srt
    roughcut.mp4
  ai/                          # --ai 时生成
    AGENT_PROMPT.md
    pack/thumbs/
    broll_vlm.json
    narrative_plan.json
  ai_video/                    # ai_video_prepare 时生成（本地视频不入库）
    manifest.yaml
    prompts/*.txt
    videos/*.mp4
```

---

## 效果边界（实测经验）

- 结构正确、语义对齐、无重复句尾时，主观约 **90%** 可用，剩余靠剪映 BGM/贴纸/微调。
- **露脸比例**下限 ≈ `1 - (无脸空镜总时长 / 口播总时长)`。  
  空镜不够时，再低的 `target_face_ratio` 也压不下去 → 需补空镜或 `max_reuse: 2`。
- 没有对应标签的镜头（如口播说长颈鹿、库里只有河马）→ 宁可不盖，不强行糊弄。

---

## 仓库说明

- **代码**：`pipeline/`、`run_pipeline.py`
- **示例配置**：`examples/`
- **不包含**：原始成片素材、`work/` 缓存（见 `.gitignore`）

本地素材请自行放入项目目录，勿提交大体积视频。

---

## License

MIT — 见 [LICENSE](./LICENSE)
