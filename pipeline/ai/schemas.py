"""AI 产物文件约定（写在 <project>/work/ai/）。

全部由 Grok Build 会话填写，流水线代码只读写 JSON，不调外部 API。
"""

from __future__ import annotations

# 允许的标签词表（可扩展，匹配阶段 keyword_aliases 会用到）
TAG_VOCAB = [
    "河马",
    "水鸟",
    "鸟",
    "长颈鹿",
    "斑马",
    "船",
    "乘船",
    "救生衣",
    "湖",
    "湖面",
    "水面",
    "倒影",
    "树",
    "日落",
    "草原",
    "天空",
    "自拍",
    "出镜",
    "人脸",
    "岸边",
    "水草",
    "风景",
    "动物",
    "空镜",
]

"""
broll_vlm.json
{
  "version": 1,
  "source": "grok-build",
  "items": [
    {
      "name": "xxx.mp4",
      "tags": ["河马", "湖面"],
      "score": 8.5,          # 0-10 高光/可用度
      "shake": 0.2,          # 0-1 抖动
      "face": false,         # 是否主要是人脸自拍
      "note": "河马露背，水面清晰"
    }
  ]
}

narrative_plan.json
{
  "version": 1,
  "source": "grok-build",
  "title": "肯尼亚纳瓦沙湖",
  "target_face_ratio": 0.2,
  "sequence_suggestion": [
    {"file": "口播素材3.mp4", "start": 0.9},
    {"file": "口播素材1.mp4", "start": 0, "end": 17.4},
    ...
  ],
  "force_face_texts": [
    "提到肯尼亚",
    "人生不只是不断赶路",
    "评论区告诉我"
  ],
  "prefer_broll_texts": [
    "河马安静地守着水域",
    "水鸟贴着湖面飞过"
  ],
  "notes": "开场露脸钩子，中段盖画，金句与CTA露脸"
}

highlight_scores.json  （可与 broll_vlm 合并；若单独存在则覆盖 score）
{
  "version": 1,
  "source": "grok-build",
  "scores": {
    "xxx.mp4": 8.5,
    "yyy.mp4": 6.0
  }
}
"""
