#!/usr/bin/env python3
"""Remove both MeshFix passes from a patched STL repair script.

Used for unusually large meshes where MeshFix is terminated before connected-voxel
reconstruction can begin. This patch deliberately locates the repair blocks by stable
boundary markers, so it works whether the quality gate is named stats_pass or
candidate_pass and regardless of the order of the dimension-retention patch.
"""
from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch_repair_skip_meshfix.py REPAIR_SCRIPT")

    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    pre_start_marker = (
        "    if selected is None:\n"
        "        try:\n"
        "            fixed = repair_with_meshfix(original)"
    )
    voxel_start_marker = (
        "\n\n    if selected is None:\n"
        "        voxel, connectivity = reconstruct_connected_voxels(original, args.pitch)"
    )
    pre_start = text.find(pre_start_marker)
    if pre_start < 0:
        raise RuntimeError("Could not find the pre-voxel MeshFix block start")
    voxel_start = text.find(voxel_start_marker, pre_start)
    if voxel_start < 0:
        raise RuntimeError("Could not find the connected-voxel block following MeshFix")

    pre_replacement = (
        "    if selected is None:\n"
        "        entry[\"attempts\"][\"pymeshfix_skipped\"] = {\n"
        "            \"reason\": \"disabled for bounded-memory connected-voxel repair\"\n"
        "        }"
    )
    text = text[:pre_start] + pre_replacement + text[voxel_start:]

    post_start_marker = (
        "        # A final MeshFix pass repairs rare marching-cubes/decimation anomalies.\n"
        "        try:\n"
        "            voxel = repair_with_meshfix(voxel)\n"
        "        except Exception:\n"
        "            voxel = clean_mesh(voxel)\n"
    )
    post_replacement = (
        "        # Avoid a second high-memory MeshFix pass. The exported STL is reopened\n"
        "        # and independently validated below and again by the workflow validator.\n"
        "        voxel = clean_mesh(voxel)\n"
    )
    post_count = text.count(post_start_marker)
    if post_count != 1:
        raise RuntimeError(f"Expected one post-voxel MeshFix block, found {post_count}")
    text = text.replace(post_start_marker, post_replacement, 1)

    path.write_text(text, encoding="utf-8")
    print(f"Patched {path}: MeshFix disabled before and after voxel reconstruction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
