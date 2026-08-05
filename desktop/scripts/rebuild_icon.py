"""Rebuild assets/sunrise-waves.ico to the Android Studio convention.

History (the five installer-icon incidents, resolved 2026-08-05): the
original file shipped mask-less DIB frames (header declared biHeight =
2x height but the AND mask was absent) and rendered only by Explorer's
leniency; a Pillow regeneration made it worse (all-PNG at every size,
then mask-less 64/128). The structural investigation against REAL vendor
icons on this machine settled the target convention:

  - Git for Windows: 23 frames, ALL PNG (disproving the repo's earlier
    "all-PNG renders blank" theory);
  - Android Studio: PNG for the 256px frame ONLY, classic 32bpp DIB with
    a real AND mask and biSizeImage=0 for every smaller frame, plus the
    20/24/40 DPI-scaling sizes;
  - Microsoft Office: all-DIB with masks (legacy multi-depth).

This script emits Android Studio's exact layout, our sizes:
  256 (PNG), then 128/64/48/40/32/24/20/16 (DIB+mask, biSizeImage=0).
Pixel sources: sizes already in the existing file keep their XOR bytes
verbatim (the 16/32/48/256 pixels have shipped and rendered since
0.3.0); new sizes are Lanczos-downscaled from the 256px master.

Usage (from desktop/):  python scripts/rebuild_icon.py
Requires Pillow. Deterministic for a given input file + Pillow version.
Pinned by apps/api/tests/test_installer_icon.py and the vendor-convention
pin in test_ui_grounding_and_packaging.py.
"""
import io
import struct
import sys
from pathlib import Path

from PIL import Image

PNG_SIZE = 256
DIB_SIZES = (128, 64, 48, 40, 32, 24, 20, 16)   # Studio order: descending
ICO = Path(__file__).resolve().parents[1] / "assets" / "sunrise-waves.ico"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def read_frames(path):
    """Existing ico -> {size: RGBA Image}, decoding raw DIB XOR data
    directly (no re-encode) so reused pixels stay byte-identical."""
    data = path.read_bytes()
    _, typ, count = struct.unpack_from("<HHH", data, 0)
    assert typ == 1, "not an ico"
    out = {}
    for i in range(count):
        w, h, _c, _r, _p, _bpp, size, off = struct.unpack_from(
            "<BBBBHHII", data, 6 + i * 16)
        w, h = w or 256, h or 256
        frame = data[off:off + size]
        if frame[:8] == _PNG_MAGIC:
            out[w] = Image.open(io.BytesIO(frame)).convert("RGBA")
            continue
        bpp = struct.unpack_from("<H", frame, 14)[0]
        assert bpp == 32, f"{w}px frame is {bpp}bpp; only 32bpp handled"
        xor = frame[40:40 + w * h * 4]
        out[w] = Image.frombytes("RGBA", (w, h), bytes(xor), "raw", "BGRA", 0, -1)
    return out


def dib_frame(img):
    """One complete 32bpp DIB frame, Studio-style: header with
    biSizeImage=0 (legal for BI_RGB, what Studio writes) + XOR pixels
    (bottom-up BGRA) + alpha-derived AND mask (bit set = transparent),
    mask rows padded to 32 bits."""
    w, h = img.size
    xor = img.tobytes("raw", "BGRA", 0, -1)
    mask_stride = ((w + 31) // 32) * 4
    alpha = img.getchannel("A").tobytes()
    rows = []
    for y in range(h - 1, -1, -1):          # bottom-up, like the XOR data
        row = bytearray(mask_stride)
        for x in range(w):
            if alpha[y * w + x] < 128:      # transparent -> mask bit 1
                row[x // 8] |= 0x80 >> (x % 8)
        rows.append(bytes(row))
    and_mask = b"".join(rows)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    return header + xor + and_mask


def png_frame(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")             # RGBA -> colortype 6, bitdepth 8
    return buf.getvalue()


def build(frames):
    ordered = [(PNG_SIZE, png_frame(frames[PNG_SIZE]))]
    ordered += [(s, dib_frame(frames[s])) for s in DIB_SIZES]
    dir_hdr = struct.pack("<HHH", 0, 1, len(ordered))
    entries, blobs = b"", b""
    offset = 6 + 16 * len(ordered)
    for s, blob in ordered:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32,
                               len(blob), offset)
        offset += len(blob)
        blobs += blob
    return dir_hdr + entries + blobs


def main():
    src = read_frames(ICO)
    master = src[max(src)]
    wanted = (PNG_SIZE,) + DIB_SIZES
    frames = {s: (src[s] if s in src
                  else master.resize((s, s), Image.LANCZOS))
              for s in wanted}
    ICO.write_bytes(build(frames))
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes): "
          f"{PNG_SIZE} PNG + DIB {', '.join(map(str, DIB_SIZES))}")


if __name__ == "__main__":
    sys.exit(main())
