# 即梦 CLI（Seedance）使用说明 · travel-talk-pipeline

> 官方 CLI 摘录：`docs/SEEDANCE_OFFICIAL_CLI.md`  
> 流水线集成：`docs/AI_VIDEO_SEEDANCE.md`  
> 本文：安装登录、Seedance 2.5 参数、与 pipeline 的衔接。

---

## 1. 是什么

**即梦 CLI**（命令名 `dreamina`）是面向 Agent / 自动化的本地命令行工具，可提交：

- 文生图 / 图生图 / 超清
- **文生视频 / 图生视频 / 首尾帧 / 多帧 / 全能参考**
- 异步任务查询、下载、历史、积分

生成消耗账户积分（与网页 Agent 模式同标准），**需高级会员及以上**。你当前账号：`vip_level=ultra`。

异步模型：**提交** 与 **查询** 是两步；可用 `--poll=N` 先等 N 秒。

合规：

> 部分模型（含 Seedance 2.5）**首次使用前需在即梦网页端用该模型完成一次生成**。  
> CLI 若返回 `AigcComplianceConfirmationRequired`，先去 Web 点一次再重试。

---

## 2. 安装 / 更新 / 登录

```bash
# 安装或更新（当前官方最新含 v1.4.15 Seedance 2.5）
curl -fsSL https://jimeng.jianying.com/cli | bash

# 确认
export PATH="$HOME/.local/bin:$PATH"
dreamina -h
dreamina version          # 期望 version.json 为 1.4.15+
dreamina login            # 浏览器 OAuth（建议手动完成，勿纯靠 Agent 登录）
dreamina user_credit      # 自检积分 / 会员
```

| 命令 | 用途 |
|------|------|
| `dreamina login` | 登录或复用登录态 |
| `dreamina login --headless` | 只出授权材料，不阻塞 |
| `dreamina login checklogin --device_code=... --poll=30` | 查 headless 是否完成 |
| `dreamina relogin` | 清态重登 |
| `dreamina logout` | 退出本地登录 |

本地目录：

| 路径 | 说明 |
|------|------|
| `~/.dreamina_cli/tasks.db` | 任务记录 |
| `~/.dreamina_cli/logs/` | 日志（排错优先看这里） |
| `~/.dreamina_cli/dreamina/SKILL.md` | Agent Skill |
| `~/.dreamina_cli/version.json` | 版本信息 |

---

## 3. Seedance 2.5 视频参数（v1.4.15+）

命令族：

| 模式 | 命令 | 本项目常用 |
|------|------|------------|
| 文生视频 | `text2video` | 无 still 时 |
| **图生视频** | **`image2video`** | **✅ 越南 AI B-roll** |
| 首尾帧 | `frames2video` | 需要收束帧时 |
| 多帧 | `multiframe2video` | 多图叙事 |
| 全能参考 | `multimodal2video` | 图+视频+音频 |

### 图生视频（推荐）

```bash
dreamina image2video \
  --image=./first.jpg \
  --prompt="镜头缓慢推进，展厅人流动" \
  --duration=5 \
  --video_resolution=720p \
  --model_version=seedance2.5 \
  --poll=120
```

| 参数 | Seedance 2.5 | 说明 |
|------|--------------|------|
| `--model_version` | `seedance2.5` | VIP-only |
| `--video_resolution` | **`480p` / `720p`** | **必填**；2.5 不支持 1080p |
| `--duration` | **4–30** 秒 | 默认 5 |
| `--image` | 本地路径 | **比例由图推断**（要竖屏 9:16 请用竖图） |
| `--prompt` | 文本 | 动作+运镜+负面约束 |
| `--poll` | 秒 | 0=只提交；建议 60–180 |

### 文生视频

```bash
dreamina text2video \
  --prompt="竖屏，科技展厅缓推镜头" \
  --duration=5 \
  --ratio=9:16 \
  --video_resolution=720p \
  --model_version=seedance2.5 \
  --poll=120
```

`ratio`：`1:1` `3:4` `16:9` `4:3` **`9:16`** `21:9`

### 查询 / 下载

```bash
dreamina query_result --submit_id=你的_submit_id
dreamina query_result --submit_id=你的_submit_id --download_dir=./downloads
dreamina list_task --gen_status=success
```

---

## 4. 与本仓库 pipeline 衔接

推荐路径（**文生视频 · 结构化 prompt · 手机实拍**）：

```bash
python run_pipeline.py run <project> --from-stage ai_video_prepare
# 编辑 <project>/work/ai_video/prompts/*.txt 与 manifest.yaml
python scripts/seedance_t2v.py --work <project>/work
python scripts/seedance_poll_download.py \
  --map <project>/work/ai_video/id_map.txt \
  --out <project>/work/ai_video/videos
python run_pipeline.py run <project> --from-stage ai_video_apply
```

| 项 | 路径 |
|----|------|
| 流水线文档 | `docs/AI_VIDEO_SEEDANCE.md` |
| 示例 manifest / prompt | `examples/ai_video/` |
| 提交脚本 | `scripts/seedance_t2v.py` |
| 下载脚本 | `scripts/seedance_poll_download.py` |
| 阶段代码 | `pipeline/stages/ai_video_*.py`、`pipeline/ai_video/` |

**硬性规则**：`max_reuse: 1` —— 一条生成视频只绑定一个 `piece_index`。

---

## 5. Session（可选）

```bash
dreamina session create "my-ai-broll"
dreamina session list
dreamina text2video --session=<id> ...
```

---

## 6. 常见问题

1. **command not found** → `export PATH="$HOME/.local/bin:$PATH"` 或重开终端。  
2. **登录「非法应用」** → 先登录即梦 Web，再本机手动 `dreamina login`，浏览器点授权（不要只靠 Agent 打开的错误 URL）。  
3. **AigcComplianceConfirmationRequired** → 网页端用 Seedance 2.5 先生成一次。  
4. **querying** → 保存 `submit_id`，稍后 `query_result`。  
5. **竖屏不对** → `text2video` 显式 `--ratio=9:16`；`image2video` 比例跟首帧。  
6. **反馈** 时带：完整命令、报错、`dreamina version`、`~/.dreamina_cli/logs/`、`submit_id`。

---

## 7. 推荐最小闭环

1. 安装/更新 CLI → `dreamina version`  
2. `dreamina login` → `dreamina user_credit`  
3. Web 端 Seedance 2.5 完成首次合规生成  
4. `ai_video_prepare` → 写结构化 prompt → `scripts/seedance_t2v.py`  
5. `ai_video_apply` → `assemble`  

---

## 8. 文档关系

| 文件 | 角色 |
|------|------|
| `docs/SEEDANCE_OFFICIAL_CLI.md` | 官方 CLI 指南摘录 |
| `SEEDANCE_CLI.md` | 本仓库工作流整理（优先读这个） |
| `docs/AI_VIDEO_SEEDANCE.md` | 流水线阶段说明 |
| `~/.dreamina_cli/dreamina/SKILL.md` | 官方 Agent Skill（本机安装脚本同步，不入库） |
