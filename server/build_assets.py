# -*- coding: utf-8 -*-
"""Copy the illustration frames into docs/assets as small JPEGs for the web app.

The source frames are AI-generated illustrations (not patient data); see
NOTICE.md for provenance and licence.  They are downscaled only to keep the
static site small.
"""
from pathlib import Path

from PIL import Image

SRC = Path(r'D:\KU-Assignment\IVF\Project2\samples\examples\ai_keyframes_CLINIC_0001')
DST = Path(r'D:\KU-Assignment\IVF\Project2\IVF-Labeling\docs\assets\frames\CLINIC_0001')
SIZE = 420
QUALITY = 82

DST.mkdir(parents=True, exist_ok=True)
total = 0
count = 0
for p in sorted(SRC.glob('*.png')):
    im = Image.open(p).convert('L').resize((SIZE, SIZE), Image.LANCZOS)
    name = p.stem.replace('ai_keyframe_', '') + '.jpg'
    out = DST / name
    im.save(out, 'JPEG', quality=QUALITY, optimize=True)
    total += out.stat().st_size
    count += 1

print(f'{count} frames -> {total / 1024:.0f} KB')
for p in sorted(DST.glob('*.jpg'))[:5]:
    print('  ', p.name, f'{p.stat().st_size / 1024:.0f} KB')
