#!/usr/bin/env python3
"""Apply small, auditable fixes to the unpacked HOTS STL builder."""
from __future__ import annotations

import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one {label} target, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: runtime_patch.py PATH_TO_build_hots_stls.py")

    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    # Blizzard asset names sometimes use class/codename identifiers rather than the
    # player-facing hero name. Keep all conservative Johanna aliases in the resolver.
    text = replace_once(
        text,
        '    "Johanna": ("johanna",),',
        '    "Johanna": ("johanna", "crusader", "d3crusader", "d3_crusader", "crusaderfemale", "crusader_female"),',
        "Johanna alias",
    )

    # The STL is written as float32. A 0.005 mm edge quantisation is still far below
    # the 0.38 mm voxel size, but avoids treating float round-off as a physical seam.
    text = replace_once(
        text,
        'def validate_binary_stl(path: Path, edge_quantum: float = 0.0005) -> dict:',
        'def validate_binary_stl(path: Path, edge_quantum: float = 0.005) -> dict:',
        "STL edge quantisation",
    )

    cleanup_function = r'''

def final_manifold_cleanup(obj: bpy.types.Object, weld_distance: float) -> dict:
    """Weld tiny cracks, remove duplicate/degenerate faces and cap open loops."""
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_distance)
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()

    if bm.edges:
        bmesh.ops.dissolve_degenerate(
            bm,
            dist=max(1e-6, weld_distance * 0.25),
            edges=list(bm.edges),
        )

    # Remove exact duplicate faces, a common cause of isolated >2-face edges after
    # joining thin real-time game-mesh shells.
    bm.verts.index_update()
    seen = set()
    duplicate_faces = []
    for face in list(bm.faces):
        key = tuple(sorted(vertex.index for vertex in face.verts))
        if key in seen:
            duplicate_faces.append(face)
        else:
            seen.add(key)
    if duplicate_faces:
        bmesh.ops.delete(bm, geom=duplicate_faces, context="FACES")

    boundary_edges = [edge for edge in bm.edges if len(edge.link_faces) == 1]
    if boundary_edges:
        try:
            bmesh.ops.holes_fill(bm, edges=boundary_edges, sides=0)
        except Exception as exc:
            log(f"WARNING: final hole fill reported: {exc}")

    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    mesh.validate(clean_customdata=True)
    mesh.update()

    clean_mesh_object(obj, merge_distance=weld_distance)
    triangulate_object(obj)
    return {
        "weld_distance_mm": weld_distance,
        "vertices": len(obj.data.vertices),
        "triangles": len(obj.data.polygons),
    }
'''

    text = replace_once(
        text,
        '\ndef decimate_if_needed(obj: bpy.types.Object, max_triangles: int) -> dict:\n',
        cleanup_function + '\ndef decimate_if_needed(obj: bpy.types.Object, max_triangles: int) -> dict:\n',
        "final manifold cleanup function insertion",
    )

    text = replace_once(
        text,
        '    decimation = decimate_if_needed(print_obj, args.max_triangles)\n\n    # Put the final base exactly on Z=0 after voxelization.',
        '    decimation = decimate_if_needed(print_obj, args.max_triangles)\n'
        '    decimation["final_cleanup"] = final_manifold_cleanup(\n'
        '        print_obj, max(0.005, args.voxel_mm * 0.20)\n'
        '    )\n\n'
        '    # Put the final base exactly on Z=0 after voxelization.',
        "final cleanup call",
    )

    text = replace_once(
        text,
        '    print_export = write_binary_stl(print_obj, print_path, source_key)\n'
        '    print_export["validation"] = validate_binary_stl(print_path)\n',
        '    print_export = write_binary_stl(print_obj, print_path, source_key)\n'
        '    print_export["validation"] = validate_binary_stl(print_path)\n'
        '    if not print_export["validation"]["watertight_edge_test"]:\n'
        '        log("Strict edge test failed after cleanup; applying one final voxel union")\n'
        '        voxel_remesh_object(print_obj, max(0.20, args.voxel_mm * 0.85))\n'
        '        decimation["repair_retry"] = final_manifold_cleanup(\n'
        '            print_obj, max(0.005, args.voxel_mm * 0.20)\n'
        '        )\n'
        '        retry_minimum, _retry_maximum = object_bounds(print_obj)\n'
        '        print_obj.location.z -= retry_minimum.z\n'
        '        bpy.context.view_layer.update()\n'
        '        print_export = write_binary_stl(print_obj, print_path, source_key)\n'
        '        print_export["validation"] = validate_binary_stl(print_path)\n',
        "strict repair retry",
    )

    path.write_text(text, encoding="utf-8")
    print(f"Patched {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
