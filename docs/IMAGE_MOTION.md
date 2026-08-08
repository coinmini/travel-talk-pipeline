# 静态图 Ken Burns（assemble 内置）

旅拍素材里常有 **照片 B-roll**。若直接定格铺满时长，画面会发呆。  
流水线在 **`assemble` 阶段**对静态图自动做推拉/平移（Ken Burns），无需单独阶段。

## 流程位置

```
match → [ai_video_apply] → assemble（视频 trim + 静态图 motion）→ package
                              ▲
                              └── jpg/png/webp/heic 自动 zoom/pan
```

视频 B-roll / 口播 talk **不受影响**，只处理图片源。

## 默认行为

| 项 | 默认 |
|----|------|
| 开关 | `assemble.image_motion: true` |
| 最大推拉 | `image_zoom_max: 1.22`（约 22%） |
| 动效池 | zoom_in / zoom_out / pan_left / pan_right / pan_up / pan_down / zoom_in_up / zoom_out_right |
| 选型 | 按 `src_name + 序号` 稳定轮换（重跑结果一致） |

实现：`pipeline/stages/assemble.py`（ffmpeg `zoompan`）。

## 配置（project.yaml）

```yaml
assemble:
  image_motion: true
  image_zoom_max: 1.22          # 1.12 更克制，1.30 更明显
  # image_motion_styles:        # 可选：只启用部分
  #   - zoom_in
  #   - zoom_out
  #   - pan_left
  #   - pan_right
```

关闭：

```yaml
assemble:
  image_motion: false
```

## 单段覆盖（可选）

在 `picture_plan.json` 某条上写：

```json
{
  "type": "broll",
  "src": ".../photo.jpg",
  "motion": "zoom_in"
}
```

`motion: "none"` 或 `image_motion: false` 可强制该段定格。

## 日志

assemble 日志示例：

```
render video 8/33: broll Weixin Image_... motion=zoom_in_up ...
exported: .../roughcut.mp4 (..., image_kenburns=5)
```

`work/assemble.json` 含 `image_motion`、`image_motion_count`。
