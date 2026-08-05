"""Rebuild assets/sunrise-waves.ico strictly to the ICO spec.

Why this exists: the 0.7.0 icon regression. Every frame historically
declared biHeight = 2x height (XOR bitmap + AND mask) while containing
ONLY the XOR data - a truncation lenient parsers (PrivateExtractIcons)
tolerate but Explorer's shell path does not. The 4-frame file shipped
that way since 0.3.0 and happened to render; 0.7.0 extended the defect
to new 64/128px frames and Explorer showed the blank document icon.

This script writes every frame COMPLETE: XOR pixels (bottom-up BGRA)
plus an alpha-derived AND mask, biSizeImage covering both, dir entry
sizes exact. Pixel sources: a master image for all sizes, or with
--from-ico the existing file's own frames (byte-identical XOR reuse
for sizes it already has; Lanczos downscale from the largest for new
sizes).

Usage (from desktop/):  python scripts/rebuild_icon.py
Requires Pillow. Deterministic: same inputs -> same bytes.
"""
import struct
import sys
from pathlib import Path

from PIL import Image

SIZES = (16, 32, 48, 64, 128, 256)
ICO = Path(__file__).resolve().parents[1] / "assets" / "sunrise-waves.ico"


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
        if frame[:8] == b"\x89PNG\r\n\x1a\n":
            out[w] = Image.open(__import__("io").BytesIO(frame)).convert("RGBA")
            continue
        bpp = struct.unpack_from("<H", frame, 14)[0]
        assert bpp == 32, f"{w}px frame is {bpp}bpp; only 32bpp handled"
        xor = frame[40:40 + w * h * 4]
        img = Image.frombytes("RGBA", (w, h), bytes(xor), "raw", "BGRA", 0, -1)
        out[w] = img
    return out


def dib_frame(img):
    """One COMPLETE 32bpp DIB frame: header + XOR (bottom-up BGRA) +
    AND mask derived from alpha (bit set = transparent), rows padded
    to 32 bits. This is the part the old frames were missing."""
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
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
    return header + xor + and_mask


def build(frames):
    ordered = [(s, dib_frame(frames[s])) for s in SIZES]
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
    largest = src[max(src)]
    frames = {s: (src[s] if s in src
                  else largest.resize((s, s), Image.LANCZOS))
              for s in SIZES}
    ICO.write_bytes(build(frames))
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes, "
          f"{len(SIZES)} complete frames: {', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    sys.exit(main())
