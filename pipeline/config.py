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
        # 成片从「第一个字」开口开始：裁掉 Whisper 段首的环境声/BGM
        "trim_leading_silence": True,
        "speech_onset_peak_ratio": 0.45,
        "speech_onset_min_hold_sec": 0.12,
        # False=每个口播文件第一句都裁；True=仅整条时间线第一句
        "trim_leading_only_global_first": False,
        # 末词 release 后安全底（秒）；主延展靠 RMS
        "segment_tail_pad_sec": 0.04,
        # 末字最多再延长多久（秒）；过大口播脸会「冻尾」过久
        "speech_release_max_extend_sec": 0.16,
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
            # 海岛 / 度假
            "海": ["海", "海水", "海边", "沙滩", "海岸", "空镜", "风景"],
            "海水": ["海", "海水", "海边", "蓝", "空镜"],
            "海边": ["海", "海边", "沙滩", "海岸"],
            "沙滩": ["沙滩", "海边", "海"],
            "青山": ["山", "青山", "风景", "空镜"],
            "山": ["山", "青山", "风景"],
            "游泳": ["游泳", "海", "玩水", "水上"],
            "滑板": ["冲浪", "滑板", "水上", "玩水"],
            "滑墙板": ["冲浪", "滑板", "水上"],
            "滑梯": ["滑梯", "水上", "玩水"],
            "水上": ["水上", "滑梯", "游泳", "玩水"],
            "渔民": ["渔民", "渔船", "码头", "收获"],
            "渔船": ["渔船", "船", "渔民", "码头"],
            "收获": ["渔民", "渔船", "码头"],
            "阳光": ["阳光", "天空", "海", "空镜"],
            "蓝": ["海", "海水", "天空", "空镜"],
            # 展会 / AI / 商务
            "人工智能": ["展会", "展台", "设备", "屏幕", "AI", "科技"],
            "AI": ["展会", "展台", "设备", "屏幕", "科技"],
            "展会": ["展会", "展台", "展位", "设备", "人群", "科技"],
            "讲会": ["展会", "展台", "展位"],
            "展位": ["展会", "展台", "展位", "设备"],
            "供应商": ["展会", "设备", "展台", "商务"],
            "设备": ["设备", "展会", "展台", "机器", "科技"],
            "客户": ["展会", "商务", "人群", "交谈"],
            "团队": ["团队", "人群", "展会", "商务"],
            "售后": ["展会", "商务", "设备"],
            "信任": ["展会", "商务", "交谈"],
            "物理世界": ["设备", "机器", "展会", "科技"],
            "聊天框": ["屏幕", "科技", "AI"],
            "数字世界": ["屏幕", "科技", "AI"],
        },
        "reuse_broll": False,
        "max_reuse": 1,
        "max_reuse_image": 1,  # 静态图永不复用（独立于视频 max_reuse）
        # 旅拍默认低露脸，大量盖画；但开场必须主角露脸
        "target_face_ratio": 0.12,
        "force_face_open_segments": 2,  # 前 N 句强制 talk（不受时长上限）
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
        # 多口播源 → work/package_by_talk/<口播名>/{剪映导入,预览,工程}/
        "split_by_talk": True,
        # 每条成片固定片尾（项目目录或仓库根下的 视频结尾.MP4）；false 关闭
        "outro_video": "视频结尾.MP4",
    },
    # 静态图 B-roll：Ken Burns 推拉/平移，避免呆板定格
    "assemble": {
        "image_motion": True,
        "image_zoom_max": 1.22,
        "image_motion_styles": [
            "zoom_in",
            "zoom_out",
            "pan_left",
            "pan_right",
            "pan_up",
            "pan_down",
            "zoom_in_up",
            "zoom_out_right",
        ],
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
    # 可选 AI 视频 B-roll：本机 dreamina CLI + Seedance 文生视频（无密钥入库）
    "ai_video": {
        "provider": "seedance_cli",
        "model_version": "seedance2.5",
        "ratio": "9:16",
        "video_resolution": "720p",
        "duration": 5,
        "max_reuse": 1,
        "style": "phone_documentary_realism",
        # 筛选要替换的 picture_plan 槽位（二选一；都空则默认全部 broll 供人工删减）
        "piece_indices": [],
        "text_contains": [],
        "style_negative": "不要海景误盖、不要科幻全息",
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
