#!/usr/bin/env python3
"""Generate the raster favicon assets from the same design as favicon.svg.

The SVG is the primary icon; browsers that prefer bitmaps/ICO get these files.
Requires Pillow (dev only, see requirements-dev.txt):

    python3 scripts/make_favicon.py

Writes favicon-32.png, apple-touch-icon.png and favicon.ico into
tvguide/web/static/. Run it whenever the design changes.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "tvguide" / "web" / "static"

S = 16  # supersampling factor (64 * 16 = 1024 px master)
W = 64 * S

OUTLINE = (35, 37, 38, 255)
BODY_TOP = (244, 235, 211, 255)
BODY_BOTTOM = (215, 194, 153, 255)
SCREEN = (16, 20, 24, 255)
BARS = [
    (236, 236, 236, 255),  # white
    (232, 210, 26, 255),   # yellow
    (37, 208, 200, 255),   # cyan
    (47, 182, 74, 255),    # green
    (201, 59, 208, 255),   # magenta
    (212, 58, 52, 255),    # red
    (47, 91, 212, 255),    # blue
]
KNOB = (35, 37, 38, 255)
CREAM = (232, 220, 192, 255)


def sc(*vals: float) -> tuple[int, ...]:
    return tuple(round(v * S) for v in vals)


def rx(box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return sc(*box)  # type: ignore[return-value]


def lerp(a: tuple[int, int, int, int], b: tuple[int, int, int, int], t: float) -> tuple[int, int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))  # type: ignore[return-value]


def rounded_mask(box: tuple[float, float, float, float], radius: float) -> Image.Image:
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle(rx(box), radius=round(radius * S), fill=255)
    return mask


def overlay(base: Image.Image, mask: Image.Image, draw_fn) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    a = ImageChops.multiply(layer.getchannel("A"), mask)
    layer.putalpha(a)
    base.alpha_composite(layer)


def build_master() -> Image.Image:
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lw = round(2.5 * S)

    # Antennas (behind the body)
    d.line([sc(24, 18), sc(15, 5)], fill=OUTLINE, width=lw)
    d.line([sc(40, 18), sc(49, 5)], fill=OUTLINE, width=lw)
    for cx, cy in ((15, 5), (49, 5)):
        x, y = sc(cx, cy)
        r = round(1.7 * S)
        d.ellipse([x - r, y - r, x + r, y + r], fill=OUTLINE)

    # Body with vertical gradient
    body_box = (4, 18, 60, 56)
    grad = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        gd.line([(0, y), (W, y)], fill=lerp(BODY_TOP, BODY_BOTTOM, y / (W - 1)))
    img.paste(grad, (0, 0), rounded_mask(body_box, 8))
    d.rounded_rectangle(rx(body_box), radius=round(8 * S), outline=OUTLINE, width=lw)

    # Screen
    screen_box = (10, 24, 44, 48)
    screen_mask = rounded_mask(screen_box, 4)
    img.paste(Image.new("RGBA", (W, W), SCREEN), (0, 0), screen_mask)

    # Colour bars
    x0, y0, x1, y1 = screen_box
    bar_w = (x1 - x0) / len(BARS)
    for i, color in enumerate(BARS):
        bx0 = x0 + i * bar_w
        bx1 = bx0 + bar_w + 0.05
        overlay(img, screen_mask, lambda dd, a=bx0, b=bx1, c=color: dd.rectangle(rx((a, y0, b, y1)), fill=c))

    # Scanlines
    def scanlines(dd: ImageDraw.ImageDraw) -> None:
        y = y0 + 1.5
        while y < y1:
            dd.rectangle(rx((x0, y, x1, y + 1.1)), fill=(0, 0, 0, 41))
            y += 3.0

    overlay(img, screen_mask, scanlines)

    # Glass shine
    def shine(dd: ImageDraw.ImageDraw) -> None:
        dd.ellipse(rx((x0 + 1, y0 + 1, x0 + 24, y0 + 16)), fill=(255, 255, 255, 60))

    overlay(img, screen_mask, shine)

    # Screen frame
    d.rounded_rectangle(rx(screen_box), radius=round(4 * S), outline=OUTLINE, width=round(1.5 * S))

    # Knobs
    for cx, cy, r in ((52, 30, 3.1),):
        x, y = sc(cx, cy)
        rr = round(r * S)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=KNOB)
        ri = round(1.1 * S)
        d.ellipse([x - ri, y - ri, x + ri, y + ri], fill=CREAM)
    d.rounded_rectangle(rx((49, 38, 55, 42.5)), radius=round(1 * S), fill=KNOB)

    # Feet
    d.rounded_rectangle(rx((16, 56, 22, 59)), radius=round(1 * S), fill=KNOB)
    d.rounded_rectangle(rx((42, 56, 48, 59)), radius=round(1 * S), fill=KNOB)
    return img


def main() -> None:
    master = build_master()
    resample = Image.Resampling.LANCZOS
    master.resize((32, 32), resample).save(OUT / "favicon-32.png")
    master.resize((180, 180), resample).save(OUT / "apple-touch-icon.png")
    master.resize((256, 256), resample).save(
        OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print(f"wrote favicon assets to {OUT}")


if __name__ == "__main__":
    main()
