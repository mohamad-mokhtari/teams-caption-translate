"""
The app icon, drawn in code: a speech bubble with caption lines.

Drawn rather than shipped as an image file so the tray icon and the .exe icon
cannot drift apart, and the state colour (running / needs setup / problem) is
one argument. `python -m caption_translator.icon out.ico` writes the .exe icon.
"""
from __future__ import annotations

import sys

COLORS = {
    "ok":    ((52, 211, 153), (99, 102, 241)),    # green -> indigo: translating
    "setup": ((251, 191, 36), (245, 158, 11)),    # amber: needs a key
    "error": ((248, 113, 113), (190, 18, 60)),    # red: the translator is not running
}


def draw(size: int = 64, state: str = "ok"):
    from PIL import Image, ImageDraw

    s = size * 4                                    # draw large, scale down: smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    top, bottom = COLORS.get(state, COLORS["ok"])

    # Vertical gradient inside a rounded speech bubble.
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    pad = s * 0.06
    md.rounded_rectangle([pad, pad, s - pad, s * 0.78], radius=s * 0.18, fill=255)
    md.polygon([(s * 0.22, s * 0.70), (s * 0.44, s * 0.70), (s * 0.20, s * 0.95)], fill=255)
    img.paste(grad, (0, 0), mask)

    # Two caption lines: one long, one shorter.
    w = (255, 255, 255, 240)
    h = s * 0.085
    d.rounded_rectangle([s * 0.22, s * 0.25, s * 0.78, s * 0.25 + h], radius=h / 2, fill=w)
    d.rounded_rectangle([s * 0.22, s * 0.45, s * 0.60, s * 0.45 + h], radius=h / 2, fill=w)

    return img.resize((size, size), Image.LANCZOS)


def ensure_png(directory) -> str:
    """A 256px PNG for Linux menus and autostart entries; written once."""
    import pathlib
    path = pathlib.Path(directory) / "icon.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        draw(256).save(path)
    return str(path)


def write_ico(path: str) -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    draw(256).save(path, format="ICO", sizes=[(n, n) for n in sizes])


if __name__ == "__main__":
    write_ico(sys.argv[1] if len(sys.argv) > 1 else "icon.ico")
