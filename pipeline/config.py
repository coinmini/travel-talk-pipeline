"""Load and merge project config with defaults."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .utils import ensure_dir


DEFAULTS: dict[str, Any] = {
    "title": "",
    "style": "travel_talk",  # 旅拍口播
    "target_duration_sec": 0,  # 0 = use full cleaned VO
    "output": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "video_bitrate": "6M",
        "audio_bitrate": "192k",
    },
    "talk": {
        # 口播文件匹配：文件名含这些关键词
        "name_keywords": ["口播", "talk", "vo"],
        # 手动指定顺序（文件名或 stem）；空则按数字排序后全部拼接
        "order": [],
        # 每段口播的取舍：{ "口播素材1": [[start, end], ...] } 秒；空则全段
        "ranges": {},
        # 精细序列（推荐）：可把同一口播拆成多段、按终稿叙事重排
        # - { file: 口播素材3.mp4 }
        # - { file: 口播素材1.mp4, start: 0, end: 17.4 }
        # - { file: 口播素材4.mp4 }
        # - { file: 口播素材2.mp4 }
        # - { file: 口播素材1.mp4, start: 21.3 }
        "sequence": [],
        # 去掉匹配这些正则的字幕段（口误/重录）
        "drop_segment_patterns": [
            r"^这是什么",
            r"也许这就是那瓦湖$",
        ],
        # 相邻段间隔（秒），拼 VO 时加一点呼吸
        "pad_between_sec": 0.0,
    },
    "broll": {
        # 单镜目标时长范围
        "min_clip_sec": 1.5,
        "max_clip_sec": 4.0,
        "prefer_vertical": True,
        # 照片 Ken Burns 默认时长
        "photo_duration_sec": 3.0,
        # 人工标签覆盖：{ "filename.mp4": ["河马", "湖面"] }
        "tags_override": {},
    },
    "narrative": {
        # 叙事弧 face_ratio = 该段期望「露脸时长占比」（其余盖 B-roll）
        # 旅拍默认大量盖画：景物/体验很低，钩子/金句略高
        "stages": [
            {"id": "hook", "keywords": ["提到", "首先", "却不知道", "很多人"], "face_ratio": 0.2},
            {"id": "place", "keywords": ["著名", "淡水湖", "不可错过", "栖息", "生活着"], "face_ratio": 0.08},
            {"id": "poetic", "keywords": ["飞翔", "守着", "波纹", "节奏", "从不着急"], "face_ratio": 0.06},
            {"id": "experience", "keywords": ["乘船", "体验", "湖面", "水鸟", "河马", "小船"], "face_ratio": 0.08},
            {"id": "reflection", "keywords": ["人生", "赶路", "感受", "观察", "珍惜", "效率", "从容"], "face_ratio": 0.15},
            {"id": "insight", "keywords": ["启发", "比较", "方向", "脚步", "辽阔", "温柔"], "face_ratio": 0.12},
            {"id": "cta", "keywords": ["如果有机会", "你更想", "评论区", "告诉我", "日落"], "face_ratio": 0.15},
        ],
        "default_face_ratio": 0.12,
    },
    "match": {
        # VO 关键词 → B-roll 标签同义词（口播↔画面对齐核心）
        "keyword_aliases": {
            "河马": ["河马", "海马", "hippo"],
            "海马": ["河马", "hippo"],
            "水鸟": ["水鸟", "鸟", "bird"],
            "鸟": ["水鸟", "鸟", "bird"],
            "飞翔": ["水鸟", "鸟"],
            "飞过": ["水鸟", "鸟"],
            "耳朵": ["河马", "海马"],
            "眼睛": ["河马", "海马"],
            "长颈鹿": ["长颈鹿", "giraffe"],
            "斑马": ["斑马", "zebra"],
            "船": ["船", "boat", "乘船", "救生衣"],
            "乘船": ["船", "boat", "乘船", "救生衣"],
            "小船": ["船", "boat", "乘船"],
            "湖": ["湖", "湖面", "水面", "倒影", "空镜", "风景"],
            "湖面": ["湖", "湖面", "水面", "空镜"],
            "倒影": ["倒影", "树", "水面", "空镜"],
            "树": ["树", "倒影", "岸", "空镜"],
            "日落": ["日落", "夕阳", "黄昏", "晚霞", "天空"],
            "马赛马拉": ["草原", "越野", "safari", "马拉"],
            "草原": ["草原", "safari"],
            "水草": ["水草", "岸边", "湖"],
            "自拍": ["自拍", "出镜", "人脸"],
        },
        "reuse_broll": False,
        "max_reuse": 1,
        # 旅拍默认低露脸，大量盖画
        "target_face_ratio": 0.12,
        "force_face_open_segments": 1,
        "force_face_close_segments": 1,
        "broll_pack_max_sec": 4.5,
        "broll_pack_max_segments": 1,
    },
    "export": {
        "burn_subtitles": True,
        "subtitle_font": "PingFang SC",
        "subtitle_fontsize": 42,
        "subtitle_margin_v": 160,
        "include_english_stub": True,
    },
    "whisper": {
        "model": "mlx-community/whisper-large-v3-mlx",
        "language": "zh",
    },
    # 可选 AI 层：由当前 Grok Build 会话分析，不调外部 API
    "ai": {
        "enabled": False,  # CLI --ai 时启用
        "provider": "grok-build",
        "note": "填写 work/ai/*.json 后 apply_ai 生效",
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def load_project_config(project_dir: Path, config_path: Path | None = None) -> dict[str, Any]:
    """
    Merge order: DEFAULTS < pipeline/templates/default_project.yaml < project.yaml < --config
    """
    root = Path(__file__).resolve().parent
    template = root / "templates" / "default_project.yaml"
    cfg = deepcopy(DEFAULTS)
    cfg = deep_merge(cfg, load_yaml(template))

    project_yaml = project_dir / "project.yaml"
    if project_yaml.exists():
        cfg = deep_merge(cfg, load_yaml(project_yaml))

    if config_path:
        cfg = deep_merge(cfg, load_yaml(config_path))

    if not cfg.get("title"):
        cfg["title"] = project_dir.name

    return cfg


def init_project_yaml(project_dir: Path, title: str | None = None) -> Path:
    """Write a starter project.yaml if missing."""
    ensure_dir(project_dir)
    path = project_dir / "project.yaml"
    if path.exists():
        return path
    sample = {
        "title": title or project_dir.name,
        "target_duration_sec": 0,
        "talk": {
            "order": [],
            "ranges": {},
            "drop_segment_patterns": [
                r"^这是什么",
            ],
        },
        "broll": {
            "tags_override": {
                # "example.mp4": ["河马", "湖面"],
            }
        },
        "export": {
            "burn_subtitles": True,
        },
    }
    path.write_text(
        yaml.safe_dump(sample, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path
