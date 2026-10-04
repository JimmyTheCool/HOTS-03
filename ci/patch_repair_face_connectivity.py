#!/usr/bin/env python3
"""Use 6-neighbour voxel connectivity so marching cubes emits one true body."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: patch_repair_face_connectivity.py REPAIR_SCRIPT")

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "    connectivity_structure = np.ones((3, 3, 3), dtype=bool)"
new = "    connectivity_structure = ndimage.generate_binary_structure(3, 1)"
count = text.count(old)
if count != 1:
    raise RuntimeError(f"Expected one 26-neighbour connectivity definition, found {count}")
text = text.replace(old, new, 1)

old2 = '''    bridge_mask = ndimage.binary_dilation(
        bridge_mask,
        structure=ndimage.generate_binary_structure(3, 2),
        iterations=1,
    )
'''
new2 = '''    bridge_mask = ndimage.binary_dilation(
        bridge_mask,
        structure=ndimage.generate_binary_structure(3, 1),
        iterations=2,
    )
'''
count2 = text.count(old2)
if count2 != 1:
    raise RuntimeError(f"Expected one primary bridge dilation block, found {count2}")
text = text.replace(old2, new2, 1)

old3 = '''        final_bridge_mask = ndimage.binary_dilation(
            final_bridge_mask,
            structure=ndimage.generate_binary_structure(3, 2),
            iterations=1,
        )
'''
new3 = '''        final_bridge_mask = ndimage.binary_dilation(
            final_bridge_mask,
            structure=ndimage.generate_binary_structure(3, 1),
            iterations=2,
        )
'''
count3 = text.count(old3)
if count3 != 1:
    raise RuntimeError(f"Expected one final bridge dilation block, found {count3}")
text = text.replace(old3, new3, 1)

path.write_text(text, encoding="utf-8")
print(f"Patched {path} for face-connected voxel unions")
