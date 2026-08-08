#!/usr/bin/env python3
"""短视频封面生成：参考「封面式样」——竖屏自拍主视觉 + 粗描边中文大标题。

用法：
  python scripts/make_covers.py \\
    --photo 口播帧.jpg \\
    --title 也重新认识自己 --title 黑岛山海之间 \\
    --colors "#FFE566,#FFFFFF" --pos top \\
    --out cover.jpg

  # 批量 YAML（见 examples/covers.example.yaml）
  python scripts/make_covers.py --batch covers.yaml
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageEnhance

W, H = 1080, 1920
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def load_font(size: int) -> ImageFont.ImageFont:
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size=size, index=0)
            except Exception:
                try:
                    return ImageFont.truetype(p, size=size)
                except Exception:
                    continue
    return ImageFont.load_default()


def cover_base(photo: Path, *, focus_y: float = 0.42) -> Image.Image:
    im = Image.open(photo).convert("RGB")
    iw, ih = im.size
    target = W / H
    if iw / ih > target:
        nw = int(ih * target)
        left = max(0, (iw - nw) // 2)
        im = im.crop((left, 0, left + nw, ih))
    else:
        nh = int(iw / target)
        top = int((ih - nh) * focus_y)
        top = max(0, min(top, ih - nh))
        im = im.crop((0, top, iw, top + nh))
    im = im.resize((W, H), Image.Resampling.LANCZOS)
    im = ImageEnhance.Brightness(im).enhance(1.05)
    im = ImageEnhance.Contrast(im).enhance(1.08)
    im = ImageEnhance.Color(im).enhance(1.05)
    return im


def gradient_scrim(h_frac: float, from_top: bool, strength: int = 160) -> Image.Image:
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    band = int(H * h_frac)
    for y in range(band):
        t = y / max(1, band - 1)
        a = int(strength * (1 - t) ** 1.2) if from_top else int(strength * (t**1.1))
        yy = y if from_top else H - band + y
        draw.line([(0, yy), (W, yy)], fill=(0, 0, 0, a))
    return overlay


def draw_stroke_text(
    base: Image.Image,
    lines: list[tuple[str, str]],
    *,
    y: int,
    font_size: int = 78,
    stroke: int = 7,
    line_gap: int = 16,
) -> None:
    draw = ImageDraw.Draw(base)
    font = load_font(font_size)
    cy = y
    for text, color in lines:
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (W - tw) // 2
        draw.text(
            (x + 3, cy + 4),
            text,
            font=font,
            fill=(0, 0, 0),
            stroke_width=stroke,
            stroke_fill=(0, 0, 0),
        )
        draw.text(
            (x, cy),
            text,
            font=font,
            fill=color,
            stroke_width=stroke,
            stroke_fill=(15, 15, 15),
        )
        cy += th + line_gap


def make_cover(
    photo: Path,
    out: Path,
    lines: list[tuple[str, str]],
    *,
    text_pos: str = "top",
    sub: str | None = None,
    focus_y: float = 0.4,
    font_size: int = 78,
) -> Path:
    base = cover_base(photo, focus_y=focus_y).convert("RGBA")
    if text_pos == "top":
        base = Image.alpha_composite(base, gradient_scrim(0.38, True, 150))
        y = 120
    else:
        base = Image.alpha_composite(base, gradient_scrim(0.42, False, 170))
        y = H - 420 if sub else H - 360
    draw_stroke_text(base, lines, y=y, font_size=font_size)
    if sub:
        draw = ImageDraw.Draw(base)
        f = load_font(36)
        bbox = draw.textbbox((0, 0), sub, font=f, stroke_width=3)
        tw = bbox[2] - bbox[0]
        sy = y + 200 if text_pos == "top" else H - 160
        draw.text(
            ((W - tw) // 2, sy),
            sub,
            font=f,
            fill="#FFFFFF",
            stroke_width=3,
            stroke_fill=(0, 0, 0),
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out, quality=92)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="竖屏短视频封面（描边大标题）")
    ap.add_argument("--photo", type=Path, help="人像/现场底图")
    ap.add_argument("--title", action="append", default=[], help="标题行，可重复")
    ap.add_argument(
        "--colors",
        default="#FFE566,#FFFFFF",
        help="逗号分隔颜色，与 title 对应，不足则循环",
    )
    ap.add_argument("--pos", choices=["top", "bottom"], default="top")
    ap.add_argument("--sub", default=None, help="副标题小字")
    ap.add_argument("--out", type=Path, default=Path("cover.jpg"))
    ap.add_argument("--font-size", type=int, default=78)
    ap.add_argument("--focus-y", type=float, default=0.4)
    args = ap.parse_args()

    if not args.photo or not args.title:
        ap.error("需要 --photo 与至少一个 --title")
    colors = [c.strip() for c in args.colors.split(",") if c.strip()]
    lines = [
        (t, colors[i % len(colors)])
        for i, t in enumerate(args.title)
    ]
    path = make_cover(
        args.photo,
        args.out,
        lines,
        text_pos=args.pos,
        sub=args.sub,
        focus_y=args.focus_y,
        font_size=args.font_size,
    )
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
