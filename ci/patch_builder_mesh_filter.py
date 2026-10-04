#!/usr/bin/env python3
"""Refine M3 mesh-helper filtering without dropping legitimate named geometry."""
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: patch_builder_mesh_filter.py BUILD_SCRIPT")

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = '''    excluded = (
        "collision", "physics", "ragdoll", "shadow", "holo", "decal",
        "volume", "ribbon", "particle", "emitter", "hitbox", "selection",
        "placement", "bounds", "proxy", "helper", "reference", "ref_",
        "camera", "light", "weapontrail", "trail_",
    )
    for original in list(bpy.context.scene.objects):
        if original.type != "MESH" or original.hide_render or original.hide_get():
            continue
        lower_name = original.name.casefold()
        if any(token in lower_name for token in excluded):
            continue
'''
new = '''    # Match helper names as complete tokens/prefixes. The earlier substring test
    # could incorrectly remove legitimate meshes such as Lightbringer weapons or
    # Shadowblade armour simply because their names contained "light" or "shadow".
    excluded_pattern = re.compile(
        r"(?:^|[_ .-])(?:collision|physics|ragdoll|shadow(?:volume)?|holo|decal|"
        r"volume|ribbon|particle|emitter|hitbox|selection|placement|bounds|proxy|"
        r"helper|reference|camera|light|weapontrail|trail)(?:[_ .-]|[0-9]+|$)",
        re.I,
    )
    for original in list(bpy.context.scene.objects):
        if original.type != "MESH" or original.hide_render or original.hide_get():
            continue
        lower_name = original.name.casefold()
        if excluded_pattern.search(lower_name):
            continue
'''
if old not in text:
    raise SystemExit("Expected evaluated-mesh exclusion block was not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Refined mesh helper filtering")
