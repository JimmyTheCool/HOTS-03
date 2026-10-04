#!/usr/bin/env python3
"""Remove both MeshFix passes from a patched STL repair script.

Used for unusually large meshes where MeshFix is terminated by the hosted runner
before connected-voxel reconstruction can begin. The resulting mesh is still
independently checked after export for watertightness, winding, one connected body,
finite coordinates, dimensions, volume, triangle count, and silhouette retention.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch_repair_skip_meshfix.py REPAIR_SCRIPT")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    pre_pattern = re.compile(
        r'''    if selected is None:\n        try:\n            fixed = repair_with_meshfix\(original\).*?\n            entry\["attempts"\]\["pymeshfix_error"\] = \{\n                "error": repr\(exc\),\n                "traceback": traceback\.format_exc\(\),\n            \}\n''',
        re.DOTALL,
    )
    pre_replacement = '''    if selected is None:\n        entry["attempts"]["pymeshfix_skipped"] = {\n            "reason": "disabled for bounded-memory connected-voxel repair"\n        }\n'''
    text, pre_count = pre_pattern.subn(pre_replacement, text, count=1)
    if pre_count != 1:
        raise RuntimeError(f"Expected one pre-voxel MeshFix block, found {pre_count}")

    post_old = '''        # A final MeshFix pass repairs rare marching-cubes/decimation anomalies.\n        try:\n            voxel = repair_with_meshfix(voxel)\n        except Exception:\n            voxel = clean_mesh(voxel)\n'''
    post_new = '''        # Avoid a second high-memory MeshFix pass. The exported STL is reopened\n        # and independently validated below and again by the workflow validator.\n        voxel = clean_mesh(voxel)\n'''
    if post_old not in text:
        raise RuntimeError("Could not find post-voxel MeshFix block")
    text = text.replace(post_old, post_new, 1)

    path.write_text(text, encoding="utf-8")
    print(f"Patched {path}: MeshFix disabled before and after voxel reconstruction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
