#!/usr/bin/env python3
"""Patch repair_stl_collection.py so MeshFix is diagnostic only.

The rounded original is still accepted when it is already a single watertight body.
Otherwise the face-connected voxel reconstruction is always used, preventing MeshFix
from silently deleting detached weapons, limbs, armour, wings, or other components.
This patch accepts both the original stats_pass gate and the dimension-retention
candidate_pass gate, so it is independent of patch ordering.
"""
from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch_repair_force_voxel.py REPAIR_SCRIPT")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    candidates = [
        (
            '''            if candidate_pass(fixed_stats, original_stats, args):
                selected = fixed
                selected_method = "pymeshfix"
''',
            '''            # MeshFix remains useful as a diagnostic candidate, but do not select it
            # here. On multi-shell game meshes it can satisfy topology checks by
            # deleting detached visible components. Continue to the face-connected
            # voxel reconstruction, which retains every meaningful component.
            if False and candidate_pass(fixed_stats, original_stats, args):
                selected = fixed
                selected_method = "pymeshfix"
''',
        ),
        (
            '''            if stats_pass(fixed_stats, args):
                selected = fixed
                selected_method = "pymeshfix"
''',
            '''            # MeshFix remains useful as a diagnostic candidate, but do not select it
            # here. On multi-shell game meshes it can satisfy topology checks by
            # deleting detached visible components. Continue to the face-connected
            # voxel reconstruction, which retains every meaningful component.
            if False and stats_pass(fixed_stats, args):
                selected = fixed
                selected_method = "pymeshfix"
''',
        ),
    ]

    matches = [(old, new) for old, new in candidates if old in text]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one MeshFix selection block, found {len(matches)}")
    old, new = matches[0]
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"Patched {path} to force face-connected voxel reconstruction when needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
