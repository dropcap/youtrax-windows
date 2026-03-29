#!/usr/bin/env python3
"""Generate youtrax.ico from icon_src.svg for the Windows build.

Requires: pip install cairosvg pillow
On Windows, cairo can be installed via: pip install cairosvg
(cairo DLL ships with the cairocffi wheel on Windows)
"""
import sys
from pathlib import Path

try:
    import cairosvg
except ImportError:
    sys.exit("cairosvg is required: pip install cairosvg")

try:
    from PIL import Image
except ImportError:
    sys.exit("Pillow is required: pip install pillow")

import io

SVG_PATH = Path(__file__).parent / 'icon_src.svg'
ICO_PATH = Path(__file__).parent / 'youtrax.ico'

# .ico typically contains these sizes
SIZES = [16, 32, 48, 64, 128, 256]

images = []
for size in SIZES:
    png_data = cairosvg.svg2png(url=str(SVG_PATH), output_width=size, output_height=size)
    img = Image.open(io.BytesIO(png_data)).convert('RGBA')
    images.append(img)

# Save as multi-resolution .ico
images[0].save(
    ICO_PATH,
    format='ICO',
    sizes=[(s, s) for s in SIZES],
    append_images=images[1:],
)
print(f"Generated {ICO_PATH} ({ICO_PATH.stat().st_size // 1024} KB)")
