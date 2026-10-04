#!/usr/bin/env python3
"""Add Xal'atath to the repaired HOTS STL roster when the base patch omits it."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: patch_builder_add_xalatath.py BUILD_SCRIPT")

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
entry = '    "Xal\'atath": ("xalatath",),\n'
if entry in text:
    print("Xal'atath is already present")
    raise SystemExit(0)
anchor = '    "Whitemane": ("whitemane",),\n'
if anchor not in text:
    raise SystemExit("Whitemane roster anchor not found")
path.write_text(text.replace(anchor, anchor + entry, 1), encoding="utf-8")
print("Added Xal'atath to the build roster")
