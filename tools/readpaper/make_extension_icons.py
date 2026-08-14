#!/usr/bin/env python3
"""Generate "DP" handwriting Chrome-extension icon candidates.

Black squircle background, white hand-written "DP", soft cool-white glow.
Outputs each candidate at 128px to icon_candidates/<name>_128.png and a
contact_sheet.png comparing all of them at 128px + a 32px legibility preview.
Pick one, then run with `--apply <name>` to write icon16/48/128.png.
"""
from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
EXT = ROOT / "extension"
OUT = EXT / "icon_candidates"
OUT.mkdir(parents=True, exist_ok=True)

SUP = "/System/Library/Fonts/Supplemental"
SYS = "/System/Library/Fonts"

# (key, label, font path, ttc index, scale tweak, pad fraction)
# pad = fraction of the canvas reserved as margin on EACH side before fitting
# "DP". Smaller pad => bigger glyphs (fills the square more).
DESIGNS = [
    ("snell",     "Snell Roundhand (Black)", f"{SUP}/SnellRoundhand.ttc", 2, 1.00, 0.20),
    ("bradley",   "Bradley Hand (Bold)",     f"{SUP}/Bradley Hand Bold.ttf", 0, 1.06, 0.07),
    ("signpaint", "SignPainter",             f"{SUP}/SignPainter.ttc", 0, 1.05, 0.20),
    ("noteworthy","Noteworthy (Bold)",       f"{SYS}/Noteworthy.ttc", 1, 0.90, 0.20),
    ("chancery",  "Apple Chancery",          f"{SUP}/Apple Chancery.ttf", 0, 1.00, 0.20),
    ("brush",     "Brush Script",            f"{SUP}/Brush Script.ttf", 0, 1.05, 0.20),
]

TEXT = "DP"
S = 512                       # hi-res working size
BG = (8, 8, 10)               # near-black background
FG = (248, 248, 244)          # white-ish text core
GLOW = (188, 214, 255)        # cool-white glow tint
RADIUS_FRAC = 0.22            # squircle-ish corner radius


def load_font(path, index, size):
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.truetype(path, size)


def fit_size(path, index, target_w, target_h, tweak):
    """Binary-ish search a point size so 'DP' fits target box."""
    lo, hi, best = 40, 900, 60
    while lo <= hi:
        mid = (lo + hi) // 2
        f = load_font(path, index, mid)
        l, t, r, b = f.getbbox(TEXT)
        w, h = r - l, b - t
        if w <= target_w and h <= target_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return int(best * tweak)


def render(key, path, index, tweak, pad_frac=0.20) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # rounded black background
    bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(bg)
    rad = int(S * RADIUS_FRAC)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=rad, fill=BG + (255,))
    img.alpha_composite(bg)

    # text layer (transparent), centered via bbox
    pad = int(S * pad_frac)
    size = fit_size(path, index, S - 2 * pad, S - 2 * pad, tweak)
    font = load_font(path, index, size)
    l, t, r, b = font.getbbox(TEXT)
    tw, th = r - l, b - t
    x = (S - tw) / 2 - l
    y = (S - th) / 2 - t

    txt = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(txt).text((x, y), TEXT, font=font, fill=FG + (255,))

    # glow = blurred tinted copies of the text alpha, layered for bloom
    alpha = txt.split()[3]
    for radius, op in [(26, 150), (14, 170), (6, 190)]:
        g = Image.new("RGBA", (S, S), GLOW + (0,))
        ga = alpha.filter(ImageFilter.GaussianBlur(radius))
        ga = ga.point(lambda v, o=op: int(v * o / 255))
        g.putalpha(ga)
        img.alpha_composite(g)

    # crisp text on top
    img.alpha_composite(txt)

    # clip everything to the rounded mask so glow doesn't bleed past corners
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=rad, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def make_all():
    rendered = {}
    for key, label, path, index, tweak, pad in DESIGNS:
        if not Path(path).exists():
            print(f"skip {key}: missing {path}")
            continue
        im = render(key, path, index, tweak, pad)
        im.resize((128, 128), Image.LANCZOS).save(OUT / f"{key}_128.png")
        rendered[key] = (label, im)
        print(f"made {key}: {label}")
    contact_sheet(rendered)


def preview(key):
    """Render one design at 128/64/32/16 side by side for a fill/legibility check."""
    for k, label, path, index, tweak, pad in DESIGNS:
        if k != key:
            continue
        im = render(key, path, index, tweak, pad)
        sizes = [128, 64, 32, 16]
        gap = 20
        W = sum(sizes) + gap * (len(sizes) + 1)
        H = max(sizes) + gap * 2
        sheet = Image.new("RGBA", (W, H), (150, 150, 154, 255))
        x = gap
        for s in sizes:
            sheet.alpha_composite(im.resize((s, s), Image.LANCZOS), (x, (H - s) // 2))
            x += s + gap
        sheet.convert("RGB").save(EXT / f"preview_{key}.png")
        print(f"preview -> {EXT / f'preview_{key}.png'}")
        return
    print(f"unknown design: {key}")


def contact_sheet(rendered):
    cols = len(rendered)
    cell_w, big, small, lab_h, gap = 200, 128, 32, 46, 24
    cell_h = big + small + lab_h + gap * 2
    W = cols * cell_w + gap
    H = cell_h + gap
    sheet = Image.new("RGBA", (W, H), (150, 150, 154, 255))  # neutral gray backdrop
    draw = ImageDraw.Draw(sheet)
    try:
        lab_font = ImageFont.truetype(f"{SYS}/SFNSMono.ttf", 18)
    except OSError:
        lab_font = ImageFont.load_default()

    for i, (key, (label, im)) in enumerate(rendered.items()):
        cx = gap + i * cell_w + (cell_w - gap - big) // 2
        y = gap
        sheet.alpha_composite(im.resize((big, big), Image.LANCZOS), (cx, y))
        # 32px legibility preview centered under it
        sx = gap + i * cell_w + (cell_w - gap - small) // 2
        sheet.alpha_composite(im.resize((small, small), Image.LANCZOS), (sx, y + big + gap))
        # label
        ly = y + big + gap + small + 10
        draw.text((gap + i * cell_w + 8, ly), f"{key}", font=lab_font, fill=(20, 20, 22, 255))
        draw.text((gap + i * cell_w + 8, ly + 20), label, font=lab_font, fill=(60, 60, 64, 255))

    sheet.convert("RGB").save(EXT / "contact_sheet.png")
    print(f"contact sheet -> {EXT / 'contact_sheet.png'}")


def apply(key):
    for key2, label, path, index, tweak, pad in DESIGNS:
        if key2 == key:
            im = render(key, path, index, tweak, pad)
            for s in (16, 48, 128):
                im.resize((s, s), Image.LANCZOS).save(EXT / f"icon{s}.png")
            print(f"applied {key} -> icon16/48/128.png")
            return
    print(f"unknown design: {key}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--apply":
        apply(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == "--preview":
        preview(sys.argv[2])
    else:
        make_all()
