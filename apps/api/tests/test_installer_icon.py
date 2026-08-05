"""Installer icon pins.

Two incidents shaped these:
  - the setup exe showed a blank document icon with no installerIcon in
    build.nsis (fixed 30c08cd);
  - the 0.7.0 REGRESSION: every ico frame declared biHeight = 2x height
    (XOR + AND mask) while containing only the XOR data. Lenient parsers
    (PrivateExtractIcons — the extraction check) tolerate the truncation;
    Explorer's shell path does not, and the setup exe rendered as a blank
    document again. So these pins verify the STRUCTURE directly — every
    frame complete to the declared byte — never via icon extraction.
"""
import json
import struct
from pathlib import Path

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_REQUIRED_SIZES = {16, 32, 48, 64, 128, 256}


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


def test_ico_has_all_six_layers():
    sizes = {f["w"] for f in
             _ico_frames(_DESKTOP / "assets" / "sunrise-waves.ico")}
    assert _REQUIRED_SIZES <= sizes, f"missing layers: {_REQUIRED_SIZES - sizes}"


def test_every_frame_is_complete_to_the_declared_byte():
    """THE 0.7.0 regression pin: a frame whose header promises an AND mask
    must actually contain it — data ends at the declared boundary, not at
    the XOR boundary."""
    for f in _ico_frames(_DESKTOP / "assets" / "sunrise-waves.ico"):
        w, h, frame = f["w"], f["h"], f["data"]
        assert frame[:8] != b"\x89PNG\r\n\x1a\n", f"{w}px frame is PNG"
        (bi_size, bi_w, bi_h, bi_planes, bi_bpp, bi_comp,
         bi_imgsize) = struct.unpack_from("<IiiHHII", frame, 0)[:7]
        assert bi_size == 40 and bi_comp == 0 and bi_bpp == 32
        assert (bi_w, bi_h) == (w, h * 2), \
            f"{w}px: biHeight must be doubled (XOR+AND), got {bi_h}"
        xor = w * h * 4                          # 32bpp rows need no padding
        and_mask = ((w + 31) // 32) * 4 * h      # 1bpp rows padded to 32 bits
        assert f["size"] == 40 + xor + and_mask, \
            f"{w}px frame truncated: {f['size']} != {40 + xor + and_mask}"
        assert len(frame) == f["size"]
        assert bi_imgsize == xor + and_mask
        assert f["bpp"] == 32 and f["planes"] == 1


def test_nsis_installer_icon_is_set():
    pkg = json.loads((_DESKTOP / "package.json").read_text(encoding="utf-8"))
    nsis = pkg["build"]["nsis"]
    assert nsis["installerIcon"] == "assets/sunrise-waves.ico"
    assert nsis["uninstallerIcon"] == "assets/sunrise-waves.ico"
