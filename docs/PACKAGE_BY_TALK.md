# 按口播拆包（package_by_talk）与剪映导入

多口播源时（如 `sequence: [口播1, 口播2]`），`package` 阶段会额外输出：

```
work/package_by_talk/
  <口播名>/
    剪映导入/          ← 只把这一层拖进剪映
      clips/           # 01_… 无声画面，与人声段等长
      voiceover_clean.wav
      captions_zh.srt
      导入说明.txt
    预览/
      roughcut.mp4     # 已合成预览（音画一次 mux）
    工程/
      timeline.json
      …
    README.md
```

## 为什么要分层

| 错误做法 | 后果 |
|----------|------|
| 整夹导入 `<口播名>/` | 多个 roughcut、工程 json、verify 一并进时间线，顺序/嘴形全乱 |
| 只导 clips 不导人声 | 无口播轨 |
| 人声轨长于 Σ clips | 波形伸出视频轨后面，像「末尾画面缺失」 |
| 末镜硬冻 0.5s+ | 嘴形/字幕与口播段落对不齐 |

**正确**：只导入 `剪映导入/`；clips 按 `01→末` 顶齐；`voiceover_clean.wav` 从 0 对齐。

## 音画对齐规则（流程内建）

1. **`clean_vo`**：词级开口 + 末词 **RMS release** 收尾（防「自己」被切）  
2. **`assemble`**：分段时长 nearest 帧；总帧吸到 VO 总长（**无额外末镜冻尾**）  
3. **`package_by_talk`**：  
   - 各 clip **裁/补到 plan 段长**（与对应人声段一致）  
   - `voiceover_clean.wav` **不带尾静音**  
   - 预览 roughcut 与人声等长 mux（禁止 `-shortest` 裁尾）  

## 配置

```yaml
export:
  split_by_talk: true   # 默认开；false 则只出 work/package/
talk:
  segment_tail_pad_sec: 0.06  # release 后的安全底，不是硬补尾巴
```

## 重跑

改对齐 / 分包逻辑后：

```bash
python run_pipeline.py run <项目> --from-stage clean_vo
# 或仅重导出分包：
python run_pipeline.py run <项目> --from-stage assemble
```

## 与总包的区别

| 路径 | 用途 |
|------|------|
| `work/package/` | 整片一份（多口播接在一起） |
| `work/package_by_talk/<口播>/` | 每条口播独立交付 / 剪映分轨 |

日常精修优先用 **`package_by_talk/<口播>/剪映导入/`**。
