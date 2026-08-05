"""Installer icon pins.

The setup exe showed a blank white document icon because build.nsis had
no installerIcon and the ico lacked the 64/128 px layers Explorer scales
from. Pins: the ico carries ALL six standard layers, and package.json
points the NSIS installer AND uninstaller at it.
"""
import json
import struct
from pathlib import Path

_DESKTOP = Path(__file__).resolve().parents[3] / "desktop"
_REQUIRED_SIZES = {16, 32, 48, 64, 128, 256}


def _ico_layer_sizes(path: Path) -> set:
    """Parse the ICONDIR header (stdlib only): 6-byte header, then one
    16-byte ICONDIRENTRY per image; width byte 0 means 256."""
    data = path.read_bytes()
    reserved, ico_type, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0 and ico_type == 1, "not an ICO file"
    sizes = set()
    for i in range(count):
        width = data[6 + i * 16]
        sizes.add(width or 256)
    return sizes


def test_ico_has_all_six_layers():
    sizes = _ico_layer_sizes(_DESKTOP / "assets" / "sunrise-waves.ico")
    assert _REQUIRED_SIZES <= sizes, f"missing layers: {_REQUIRED_SIZES - sizes}"


def test_nsis_installer_icon_is_set():
    pkg = json.loads((_DESKTOP / "package.json").read_text(encoding="utf-8"))
    nsis = pkg["build"]["nsis"]
    assert nsis["installerIcon"] == "assets/sunrise-waves.ico"
    assert nsis["uninstallerIcon"] == "assets/sunrise-waves.ico"
