"""Stage 4: tag B-roll with config overrides + lightweight visual heuristics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..utils import ensure_dir, extract_thumbnail, write_json


def _skin_ratio(bgr: np.ndarray) -> float:
    if bgr is None or bgr.size == 0:
        return 0.0
    ycrcb = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    mask = cv2.inRange(ycrcb, (0, 133, 77), (255, 173, 127))
    return float(mask.mean()) / 255.0


def _water_ratio(bgr: np.ndarray) -> float:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # blue-cyan water + grayish lake
    m1 = cv2.inRange(hsv, (80, 30, 40), (130, 255, 255))
    m2 = cv2.inRange(hsv, (0, 0, 60), (180, 50, 200))  # low-sat water/sky mix
    return float(cv2.bitwise_or(m1, m2).mean()) / 255.0


def _green_ratio(bgr: np.ndarray) -> float:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (35, 40, 40), (90, 255, 255))
    return float(m.mean()) / 255.0


def _sky_ratio(bgr: np.ndarray) -> float:
    h, w = bgr.shape[:2]
    top = bgr[: h // 3]
    hsv = cv2.cvtColor(top, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (90, 20, 80), (140, 255, 255))
    return float(m.mean()) / 255.0


def heuristic_tags(thumb_path: Path, info: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    img = cv2.imread(str(thumb_path))
    if img is None:
        return tags

    skin = _skin_ratio(img)
    water = _water_ratio(img)
    green = _green_ratio(img)
    sky = _sky_ratio(img)

    if skin > 0.08:
        tags.extend(["自拍", "出镜", "人脸"])
    if water > 0.18:
        tags.extend(["湖", "湖面", "水面"])
    if green > 0.2 and water < 0.15:
        tags.extend(["草原", "草地"])
    if sky > 0.25 and skin < 0.05:
        tags.append("天空")
    if green > 0.12 and water > 0.12:
        tags.extend(["岸边", "水草"])

    # duration hints
    dur = float(info.get("duration") or 0)
    if info.get("kind") == "image":
        tags.append("照片")
    if dur >= 12:
        tags.append("长镜头")
    if dur and dur < 4:
        tags.append("短镜头")

    # vertical travel selfie often has life vest orange — rough orange detect
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(hsv, (5, 120, 80), (25, 255, 255))
    if float(orange.mean()) / 255.0 > 0.04 and skin > 0.05:
        tags.extend(["船", "救生衣", "乘船"])

    # dedupe preserve order
    seen = set()
    out = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def run_tag_broll(
    asset_index: dict[str, Any],
    work_dir: Path,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    overrides = (cfg.get("broll") or {}).get("tags_override") or {}
    thumb_dir = ensure_dir(work_dir / "thumbs")
    tagged = []

    for item in asset_index.get("broll") or []:
        name = item["name"]
        path = Path(item["path"])
        thumb = Path(item.get("thumb") or "")
        if not thumb.exists():
            thumb = thumb_dir / f"broll_{path.stem[:30]}.jpg"
            try:
                extract_thumbnail(path, thumb)
            except Exception:
                pass

        auto = heuristic_tags(thumb, item) if thumb.exists() else []
        manual = list(overrides.get(name) or overrides.get(item.get("stem", "")) or item.get("tags") or [])
        # manual wins / prepends
        tags = []
        for t in manual + auto:
            if t not in tags:
                tags.append(t)

        rec = {
            **item,
            "thumb": str(thumb) if thumb else "",
            "tags": tags,
            "tags_auto": auto,
            "tags_manual": manual,
        }
        tagged.append(rec)

    result = {
        "count": len(tagged),
        "broll": tagged,
    }
    write_json(work_dir / "broll_tags.json", result)
    print(f"  tagged {len(tagged)} broll assets")
    for b in tagged[:8]:
        print(f"    - {b['name'][:32]}: {', '.join(b['tags']) or '(no tags)'}")
    if len(tagged) > 8:
        print(f"    ... +{len(tagged) - 8} more")
    return result
