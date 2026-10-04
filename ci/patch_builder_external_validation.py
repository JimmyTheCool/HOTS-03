#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: patch_builder_external_validation.py BUILD_SCRIPT")
path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '"status": "success" if hero_outputs["print_ready"]["validation"]["watertight_edge_test"] else "failed-validation",'
new = '"status": "success" if hero_outputs["print_ready"]["validation"]["valid_binary_stl"] else "failed-validation",'
if old not in text:
    raise SystemExit("Builder status expression not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Patched builder to defer geometric watertightness to independent trimesh validation")
