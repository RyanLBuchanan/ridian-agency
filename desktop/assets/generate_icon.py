"""Generate a placeholder Ridian Agency icon.

Run once to produce ``icon.png`` (used by Electron's BrowserWindow) and
``icon.ico`` (used by the Windows desktop shortcut). Re-run any time
you want to refresh the icon. Requires Pillow (already in the project
venv: ``..\\..\\.venv\\Scripts\\python.exe assets\\generate_icon.py``).

The image is intentionally minimal: a Ridian-blue rounded square with
white "RA" wordmark. Replace this file with a real brand asset whenever
you're ready — both Electron and the shortcut script will pick up the
new icon automatically.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent
PNG_PATH = OUT_DIR / "icon.png"
ICO_PATH = OUT_DIR / "icon.ico"

SIZE = 512  # source image, downsampled for .ico variants
BG_TOP = (38, 87, 224)       # #2657e0
BG_BOTTOM = (28, 68, 184)    # #1c44b8
FG = (255, 255, 255)


def _vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (size, size), top)
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return img


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def _pick_font(target_height: int) -> ImageFont.FreeTypeFont:
    """Try a few Windows fonts; fall back to Pillow's default."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",     # Segoe UI Bold
        "C:/Windows/Fonts/segoeui.ttf",      # Segoe UI Regular
        "C:/Windows/Fonts/arialbd.ttf",      # Arial Bold
        "C:/Windows/Fonts/arial.ttf",        # Arial Regular
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, target_height)
        except OSError:
            continue
    return ImageFont.load_default()


def make_icon() -> None:
    gradient = _vertical_gradient(SIZE, BG_TOP, BG_BOTTOM)
    mask = _rounded_mask(SIZE, radius=int(SIZE * 0.18))

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    canvas.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(canvas)
    font = _pick_font(int(SIZE * 0.5))
    text = "RA"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (SIZE - tw) / 2 - bbox[0]
    # Slight optical lift so the wordmark feels centered.
    y = (SIZE - th) / 2 - bbox[1] - int(SIZE * 0.03)
    draw.text((x, y), text, font=font, fill=FG)

    canvas.save(PNG_PATH, format="PNG")
    print(f"wrote {PNG_PATH}")

    # Save multi-resolution ICO for the Windows shortcut.
    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    canvas.save(ICO_PATH, format="ICO", sizes=ico_sizes)
    print(f"wrote {ICO_PATH}")


if __name__ == "__main__":
    make_icon()
