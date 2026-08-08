# AI 视频 B-roll（Seedance / 即梦 CLI）

当本地 B-roll **语义对不上口播**（例如观点段被错误盖上海景）时，用 **文生视频** 补真实感空镜，再写回 `picture_plan`。

> 登录与积分在本机 `dreamina` CLI 完成。**不要**把 token / cookie / key 写进仓库。

## 流程位置

```
match → ai_video_prepare → [写 prompt + seedance_t2v] → ai_video_apply → assemble → package
```

| 阶段 | 作用 |
|------|------|
| `ai_video_prepare` | 从 `picture_plan` 生成 `work/ai_video/`（manifest + prompt 骨架） |
| （脚本）`scripts/seedance_t2v.py` | 读 **结构化** prompt，调 `dreamina text2video` |
| （脚本）`scripts/seedance_poll_download.py` | 轮询下载 mp4 |
| `ai_video_apply` | 按 manifest 写回 `picture_plan`，**max_reuse=1** 禁止一条视频盖多句 |

## 快速使用

```bash
# 1) 匹配完成后导出 AI 视频包
python run_pipeline.py run my_trip --from-stage ai_video_prepare

# 可选：project.yaml 里只选中要替换的句子
# ai_video:
#   text_contains: ["人工智能", "供应商", "信任"]
#   # 或 piece_indices: [19, 21, 23, 25, 26, 28]

# 2) 安装/登录即梦 CLI（一次性）
curl -fsSL https://jimeng.jianying.com/cli | bash
export PATH="$HOME/.local/bin:$PATH"
dreamina login
dreamina user_credit

# 3) 编辑结构化 prompt（不要压成一段长白话）
#    my_trip/work/ai_video/prompts/*.txt
#    参考 examples/ai_video/prompts/

# 4) 提交生成 + 下载
python scripts/seedance_t2v.py --work my_trip/work
python scripts/seedance_poll_download.py \
  --map my_trip/work/ai_video/id_map.txt \
  --out my_trip/work/ai_video/videos

# 5) 写回 timeline 并成片
python run_pipeline.py run my_trip --from-stage ai_video_apply
```

`ai_video_apply` 会自动继续 `assemble` + `package`（若使用 `--from-stage ai_video_apply`）。

## Prompt 规范

必须用 skill 结构，例如：

- `【基础设定】` 画幅 / 拍摄感 / 场景  
- `【氛围与画质】`  
- `【画面内容】` 景别 / 构图 / 运镜手法 / 分秒动作  
- `【负面要求】`  

推荐风格：**手机实拍 + 现实场景**，禁止科幻全息、赛博霓虹、未来展馆。  
示例：`examples/ai_video/`。

## 配置（project.yaml）

```yaml
ai_video:
  provider: seedance_cli
  model_version: seedance2.5
  ratio: "9:16"
  video_resolution: 720p   # 2.5 仅 480p/720p
  duration: 5
  max_reuse: 1
  text_contains: []        # 或 piece_indices: []
  style_negative: "不要海景误盖、不要科幻全息"
```

## 本地产物（不入库）

`**/work/` 已在 `.gitignore` 中。`work/ai_video/videos/*.mp4`、`jobs.jsonl`、`id_map.txt` 均为本地文件。

## 官方 CLI 摘录

完整即梦 CLI 说明见仓库根目录 `SEEDANCE_CLI.md`（基于官方 Wiki 整理）。  
安装：`curl -fsSL https://jimeng.jianying.com/cli | bash`。
