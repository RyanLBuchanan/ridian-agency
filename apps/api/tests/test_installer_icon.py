"""Installer icon pins.

Incidents that shaped these:
  - the setup exe showed a blank document icon with no installerIcon in
    build.nsis (fixed 30c08cd);
  - the 0.7.0 REGRESSION: ico frames declared biHeight = 2x height
    (XOR + AND mask) while containing only the XOR data. Lenient parsers
    (PrivateExtractIcons — the extraction check) tolerate the truncation;
    Explorer's shell path does not. So these pins verify the STRUCTURE
    directly — every frame complete to the declared byte — never via
    icon extraction.

The layout itself (PNG at 256, DIB+mask below, biSizeImage=0, nine sizes)
is the Android Studio vendor convention, pinned field-by-field in
test_ui_grounding_and_packaging.py; here we pin coverage + completeness.
"""
import json
import struct
from pathlib import Path

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_REQUIRED_SIZES = {16, 20, 24, 32, 40, 48, 64, 128, 256}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _ico_frames(path: Path) -> list:
    """Parse ICONDIR + entries (stdlib only): [(size, entry-dict), ...];
    width byte 0 means 256."""
    data = path.read_bytes()
    reserved, ico_type, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0 and ico_type == 1, "not an ICO file"
    frames = []
    for i in range(count):
        w, h, _colors, _rsv, planes, bpp, size, off = struct.unpack_from(
            "<BBBBHHII", data, 6 + i * 16)
        frames.append({"w": w or 256, "h": h or 256, "planes": planes,
                       "bpp": bpp, "size": size, "data": data[off:off + size]})
    return frames


def test_ico_has_all_nine_layers():
    sizes = {f["w"] for f in
             _ico_frames(_DESKTOP / "assets" / "sunrise-waves.ico")}
    assert sizes == _REQUIRED_SIZES, \
        f"missing: {_REQUIRED_SIZES - sizes}; extra: {sizes - _REQUIRED_SIZES}"


def test_every_frame_is_complete_to_the_declared_byte():
    """THE 0.7.0 regression pin: a frame whose header promises an AND mask
    must actually contain it — data ends at the declared boundary, not at
    the XOR boundary. The 256px frame is PNG (vendor convention) and must
    be a real 256x256 PNG, declared size exact."""
    for f in _ico_frames(_DESKTOP / "assets" / "sunrise-waves.ico"):
        w, h, frame = f["w"], f["h"], f["data"]
        assert len(frame) == f["size"], f"{w}px frame truncated in file"
        assert f["bpp"] == 32 and f["planes"] == 1
        if w == 256:
            assert frame[:8] == _PNG_MAGIC, "256px frame must be PNG"
            pw, ph = struct.unpack(">II", frame[16:24])
            assert (pw, ph) == (256, 256)
            continue
        assert frame[:8] != _PNG_MAGIC, f"{w}px frame must be DIB, not PNG"
        (bi_size, bi_w, bi_h, bi_planes, bi_bpp, bi_comp,
         bi_imgsize) = struct.unpack_from("<IiiHHII", frame, 0)[:7]
        assert bi_size == 40 and bi_comp == 0 and bi_bpp == 32
        assert (bi_w, bi_h) == (w, h * 2), \
            f"{w}px: biHeight must be doubled (XOR+AND), got {bi_h}"
        assert bi_imgsize == 0                   # BI_RGB, like Android Studio
        xor = w * h * 4                          # 32bpp rows need no padding
        and_mask = ((w + 31) // 32) * 4 * h      # 1bpp rows padded to 32 bits
        assert f["size"] == 40 + xor + and_mask, \
            f"{w}px frame truncated: {f['size']} != {40 + xor + and_mask}"


def test_nsis_installer_icon_is_set():
    pkg = json.loads((_DESKTOP / "package.json").read_text(encoding="utf-8"))
    nsis = pkg["build"]["nsis"]
    assert nsis["installerIcon"] == "assets/sunrise-waves.ico"
    assert nsis["uninstallerIcon"] == "assets/sunrise-waves.ico"
