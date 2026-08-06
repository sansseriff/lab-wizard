"""Render the app icon from `logo-appicon.svg`.

macOS does not round app icons — the app supplies the shape — so the icon has to
be drawn as a rounded square with real transparency outside it. A bare
line-art mark on a transparent square shows up in the Dock as a floating glyph;
a rounded square on an *opaque* white background shows up as a white tile.

The subtlety, and the reason this script exists rather than a one-line
conversion: `qlmanage` is the only SVG rasteriser available by default on macOS,
and it composites onto opaque white. Its RGB output is correct, but its alpha is
255 everywhere. Since the squircle geometry is known exactly, the fix is to
discard that alpha and redraw the mask.

Geometry is Apple's app-icon template on a 1024pt canvas: an 824x824 body inset
100 on every side, corner radius 185.4. Those numbers are what make the icon
line up with every other app in the Dock.

Usage:
    python scripts/build_icon.py

Writes `icon.png` at the repo root and copies it to the frontend's static
directory, from which the build carries it into `backend/static`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw


REPO = Path(__file__).resolve().parent.parent
SOURCE_SVG = REPO / "logo-appicon.svg"
TARGETS = [
    REPO / "icon.png",
    REPO / "lab_wizard" / "wizard" / "frontend" / "static" / "icon.png",
]

SIZE = 1024
# Supersample the mask before downscaling; drawing the rounded rect at final
# size leaves visibly stepped corners.
SUPERSAMPLE = 4
INSET = 100
RADIUS = 185.4


def _rasterise(svg: Path, out_dir: Path) -> Path:
    """Render the SVG with qlmanage, whose output is opaque but correct in RGB."""
    subprocess.run(
        ["qlmanage", "-t", "-s", str(SIZE), "-o", str(out_dir), str(svg)],
        check=True,
        capture_output=True,
    )
    rendered = out_dir / f"{svg.name}.png"
    if not rendered.is_file():
        raise RuntimeError(f"qlmanage produced no output for {svg}")
    return rendered


def _squircle_mask() -> Image.Image:
    mask = Image.new("L", (SIZE * SUPERSAMPLE, SIZE * SUPERSAMPLE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [
            INSET * SUPERSAMPLE,
            INSET * SUPERSAMPLE,
            (SIZE - INSET) * SUPERSAMPLE - 1,
            (SIZE - INSET) * SUPERSAMPLE - 1,
        ],
        radius=int(RADIUS * SUPERSAMPLE),
        fill=255,
    )
    return mask.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> int:
    if sys.platform != "darwin":
        print("This script needs qlmanage, which is macOS-only.", file=sys.stderr)
        return 1
    if not SOURCE_SVG.is_file():
        print(f"Missing {SOURCE_SVG}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        # qlmanage names its output after the input file, so give it a copy to
        # avoid writing a stray PNG next to the source.
        staged = tmp_dir / SOURCE_SVG.name
        shutil.copy(SOURCE_SVG, staged)
        art = Image.open(_rasterise(staged, tmp_dir)).convert("RGB")
        art = art.resize((SIZE, SIZE), Image.LANCZOS)

        icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        icon.paste(art, (0, 0), _squircle_mask())

        low, high = icon.getchannel("A").getextrema()
        if low != 0:
            raise RuntimeError(
                f"Icon has no transparent pixels (alpha range {low}-{high}); "
                "the mask did not apply."
            )

        for target in TARGETS:
            target.parent.mkdir(parents=True, exist_ok=True)
            icon.save(target)
            print(f"wrote {target.relative_to(REPO)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
