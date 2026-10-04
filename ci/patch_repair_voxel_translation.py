#!/usr/bin/env python3
"""Patch Trimesh VoxelGrid placement for versions exposing translation, not origin."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: patch_repair_voxel_translation.py REPAIR_SCRIPT")

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "reconstructed.apply_translation(np.asarray(voxel_grid.origin, dtype=float))"
new = "reconstructed.apply_translation(np.asarray(voxel_grid.translation, dtype=float))"
count = text.count(old)
if count != 1:
    raise RuntimeError(f"Expected one VoxelGrid origin reference, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print(f"Patched {path} to use VoxelGrid.translation")
