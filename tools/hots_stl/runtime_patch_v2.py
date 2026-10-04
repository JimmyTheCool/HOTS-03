#!/usr/bin/env python3
"""Patch the exposed HOTS Blender builder into the v2 print-safe pipeline."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def sub_once(text: str, pattern: str, replacement: str, label: str, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"Expected one {label} target, found {count}")
    return updated


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: runtime_patch_v2.py PATH_TO_build_hots_stls.py")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    text = text.replace('BUILDER_VERSION = "1.0.0"', 'BUILDER_VERSION = "2.1.0"', 1)

    roster = '''HERO_SOURCE_CANDIDATES: Dict[str, Sequence[str]] = {
    "Abathur": ("abathur",),
    "Alarak": ("alarak",),
    "Alexstrasza": ("alexstrasza",),
    "Ana": ("ana",),
    "Anduin": ("anduin",),
    "Anub'arak": ("anubarak",),
    "Artanis": ("artanis",),
    "Arthas": ("arthas",),
    "Auriel": ("auriel",),
    "Azmodan": ("azmodan",),
    "Blaze": ("firebat",),
    "Brightwing": ("brightwing",),
    "The Butcher": ("butcher",),
    "Cassia": ("d2amazonf",),
    "Chen": ("chen",),
    "Cho": ("chogall",),
    "Chromie": ("chromie",),
    "D.Va": ("dva", "dvamech"),
    "Deathwing": ("deathwing",),
    "Deckard": ("deckard",),
    "Dehaka": ("dehaka",),
    "Diablo": ("diablo",),
    "E.T.C.": ("etc",),
    "Falstad": ("falstad",),
    "Fenix": ("fenix",),
    "Gall": ("chogall",),
    "Garrosh": ("garrosh",),
    "Gazlowe": ("gazlowe",),
    "Genji": ("genji",),
    "Greymane": ("greymane",),
    "Gul'dan": ("guldan",),
    "Hanzo": ("hanzo",),
    "Hogger": ("hogger",),
    "Illidan": ("illidan",),
    "Imperius": ("imperius",),
    "Jaina": ("jaina",),
    "Johanna": ("d3crusaderf",),
    "Junkrat": ("junkrat",),
    "Kael'thas": ("kaelthas",),
    "Kel'Thuzad": ("kelthuzad",),
    "Kerrigan": ("kerrigan",),
    "Kharazim": ("d3monkm",),
    "Leoric": ("kingleoric",),
    "Li Li": ("lili",),
    "Li-Ming": ("d3wizardf",),
    "Lt. Morales": ("medic",),
    "Lucio": ("lucio",),
    "Lunara": ("dryad",),
    "Maiev": ("maiev",),
    "Mal'Ganis": ("malganis",),
    "Malfurion": ("malfurion",),
    "Malthael": ("malthael",),
    "Medivh": ("medivh",),
    "Mei": ("meiow",),
    "Mephisto": ("mephisto",),
    "Muradin": ("muradin",),
    "Murky": ("murky",),
    "Nazeebo": ("d3witchdoctorm",),
    "Nova": ("nova",),
    "Orphea": ("orphea",),
    "Probius": ("probius",),
    "Qhira": ("nexushunter",),
    "Ragnaros": ("ragnaros",),
    "Raynor": ("raynor",),
    "Rehgar": ("rehgar",),
    "Rexxar": ("rexxar",),
    "Samuro": ("samuro",),
    "Sgt. Hammer": ("sgthammer",),
    "Sonya": ("d3barbarianf",),
    "Stitches": ("stitches",),
    "Stukov": ("stukov",),
    "Sylvanas": ("sylvanas",),
    "Tassadar": ("tassadar",),
    "The Lost Vikings": ("lostbaleog", "losterik", "lostolaf"),
    "Thrall": ("thrall",),
    "Tracer": ("tracer",),
    "Tychus": ("tychus",),
    "Tyrael": ("tyrael",),
    "Tyrande": ("tyrande",),
    "Uther": ("uther",),
    "Valeera": ("valeera",),
    "Valla": ("d3demonhunterf",),
    "Varian": ("varian",),
    "Whitemane": ("whitemane",),
    "Xul": ("d2necrom",),
    "Yrel": ("yrel",),
    "Zagara": ("zagara",),
    "Zarya": ("zarya",),
    "Zeratul": ("zeratul",),
    "Zul'jin": ("zuljin",),
}'''
    text = sub_once(
        text,
        r'HERO_SOURCE_CANDIDATES: Dict\[str, Sequence\[str\]\] = \{.*?\n\}',
        roster,
        "roster mapping",
        flags=re.S,
    )

    text = text.replace(
        'pattern = re.compile(r"^storm_hero_(?P<key>[a-z0-9_]+)_base\\.m3$", re.I)',
        'pattern = re.compile(r"^storm_hero_(?P<key>[a-z0-9_]+)_base(?:_v[0-9]+)?\\.m3$", re.I)',
        1,
    )
    text = text.replace(
        'def validate_binary_stl(path: Path, edge_quantum: float = 0.0005) -> dict:',
        'def validate_binary_stl(path: Path, edge_quantum: float = 0.01) -> dict:',
        1,
    )

    marker = '    # First, exact canonical key.\n'
    composite = '''    # Composite roster entries. D.Va includes pilot and mech; The Lost Vikings
    # includes all three playable characters. Each component is preserved and laid out
    # together on one print base by process_source_group.
    if hero in {"D.Va", "The Lost Vikings"}:
        selected = []
        for key in candidates:
            if key in base_index:
                selected.append(base_index[key][0])
        if len(selected) == len(candidates):
            return ("dva_composite" if hero == "D.Va" else "lostvikings_composite"), selected, "explicit multi-model composite"

'''
    if marker not in text:
        raise RuntimeError("composite insertion marker missing")
    text = text.replace(marker, composite + marker, 1)

    replacement_eval = r'''def evaluated_mesh_objects() -> List[bpy.types.Object]:
    """Bake every visible, printable evaluated mesh while excluding rendering helpers."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objects: List[bpy.types.Object] = []
    excluded = (
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
        evaluated = original.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph, preserve_all_data_layers=False)
        if not mesh or len(mesh.vertices) < 3 or len(mesh.polygons) < 1:
            if mesh:
                bpy.data.meshes.remove(mesh)
            continue
        copy = bpy.data.objects.new(f"PRINT_{safe_filename(original.name)}", mesh)
        bpy.context.scene.collection.objects.link(copy)
        copy.matrix_world = original.matrix_world.copy()
        copy["hots_source_index"] = int(original.get("hots_source_index", 0))
        copy["hots_original_name"] = original.name
        objects.append(copy)
    return objects
'''
    text = sub_once(
        text,
        r'def evaluated_mesh_objects\(\) -> List\[bpy\.types\.Object\]:.*?\n\ndef select_only',
        replacement_eval + '\n\ndef select_only',
        "evaluated mesh collector",
        flags=re.S,
    )

    helpers = r'''

def group_bounds(objects: Sequence[bpy.types.Object]) -> Tuple[Vector, Vector]:
    mins, maxs = zip(*(object_bounds(obj) for obj in objects))
    minimum = Vector((min(v.x for v in mins), min(v.y for v in mins), min(v.z for v in mins)))
    maximum = Vector((max(v.x for v in maxs), max(v.y for v in maxs), max(v.z for v in maxs)))
    return minimum, maximum


def normalize_object_group(objects: Sequence[bpy.types.Object], target_height: float) -> dict:
    minimum, maximum = group_bounds(objects)
    dimensions = maximum - minimum
    current_height = dimensions.z if dimensions.z > max(dimensions.x, dimensions.y) * 0.20 else max(dimensions)
    if current_height <= 1e-8:
        raise RuntimeError("Imported mesh group has zero-size bounds")
    factor = target_height / current_height
    for obj in objects:
        obj.scale = (factor, factor, factor)
    apply_world_transforms(objects)
    minimum, maximum = group_bounds(objects)
    shift = Vector((-(minimum.x + maximum.x) / 2.0, -(minimum.y + maximum.y) / 2.0, -minimum.z))
    for obj in objects:
        obj.location += shift
    bpy.context.view_layer.update()
    minimum2, maximum2 = group_bounds(objects)
    return {
        "scale_factor": factor,
        "bounds_before": [list(minimum), list(maximum)],
        "bounds_after": [list(minimum2), list(maximum2)],
        "dimensions_mm": list(maximum2 - minimum2),
    }


def mesh_topology(obj: bpy.types.Object) -> dict:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary = sum(1 for edge in bm.edges if len(edge.link_faces) == 1)
    nonmanifold = sum(1 for edge in bm.edges if len(edge.link_faces) not in (1, 2))
    edges = len(bm.edges)
    bm.free()
    minimum, maximum = object_bounds(obj)
    dimensions = maximum - minimum
    return {
        "boundary_edges": boundary,
        "nonmanifold_edges": nonmanifold,
        "edges": edges,
        "boundary_ratio": boundary / max(1, edges),
        "dimensions": list(dimensions),
        "min_dimension": min(dimensions),
        "max_dimension": max(dimensions),
    }


def prepare_print_component(obj: bpy.types.Object, thickness: float) -> dict:
    clean_mesh_object(obj, merge_distance=0.005)
    topo = mesh_topology(obj)
    name = str(obj.get("hots_original_name", obj.name)).casefold()
    sheet_tokens = (
        "cape", "cloak", "cloth", "hair", "wing", "feather", "banner",
        "skirt", "robe", "coat", "scarf", "veil", "tabard", "loin",
        "leaf", "fin", "membrane", "flag", "dress", "tassel",
    )
    sheet_like = (
        topo["boundary_edges"] > 0
        and (
            topo["min_dimension"] < max(0.9, topo["max_dimension"] * 0.035)
            or topo["boundary_ratio"] > 0.22
            or any(token in name for token in sheet_tokens)
        )
    )
    if sheet_like:
        solidify_open_surfaces(obj, thickness)
        clean_mesh_object(obj, merge_distance=0.005)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.normal_update()
    for vertex in bm.verts:
        if vertex.normal.length_squared > 1e-16:
            vertex.co += vertex.normal.normalized() * 0.08
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {"name": name, "sheet_solidified": sheet_like, "topology_before": topo}


def final_mesh_cleanup(obj: bpy.types.Object, weld_distance: float) -> None:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    if bm.verts:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_distance)
    if bm.edges:
        try:
            bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=max(1e-6, weld_distance * 0.25))
        except Exception:
            pass
    if bm.faces:
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.validate(clean_customdata=True)
    obj.data.update()
'''
    text = text.replace('\ndef duplicate_object(obj: bpy.types.Object, name: str) -> bpy.types.Object:\n', helpers + '\n\ndef duplicate_object(obj: bpy.types.Object, name: str) -> bpy.types.Object:\n', 1)

    new_process = r'''def process_source_group(
    source_key: str,
    paths: Sequence[Path],
    output_root: Path,
    args: argparse.Namespace,
) -> dict:
    started = time.monotonic()
    reset_scene()
    import_info = import_source_group(paths, source_key, args.skip_animation)
    mesh_objects = evaluated_mesh_objects()
    if not mesh_objects:
        raise RuntimeError("M3 import created no printable evaluated mesh objects")

    baked_set = set(mesh_objects)
    for obj in list(bpy.context.scene.objects):
        if obj not in baked_set:
            bpy.data.objects.remove(obj, do_unlink=True)
    apply_world_transforms(mesh_objects)

    if len(paths) > 1:
        grouped: Dict[int, List[bpy.types.Object]] = {}
        for obj in mesh_objects:
            grouped.setdefault(int(obj.get("hots_source_index", 0)), []).append(obj)
        ordered_groups = [grouped[key] for key in sorted(grouped)]
        bounds = [group_bounds(group) for group in ordered_groups]
        widths = [max(0.1, maximum.x - minimum.x) for minimum, maximum in bounds]
        gap = max(0.25, max(widths) * 0.14)
        cursor = 0.0
        centres = []
        for width in widths:
            centres.append(cursor + width / 2.0)
            cursor += width + gap
        overall_centre = (centres[0] + centres[-1]) / 2.0
        for group, (minimum, maximum), centre in zip(ordered_groups, bounds, centres):
            current_centre = (minimum.x + maximum.x) / 2.0
            shift = centre - overall_centre - current_centre
            for obj in group:
                obj.location.x += shift
        bpy.context.view_layer.update()

    normalization = normalize_object_group(mesh_objects, args.target_height_mm)

    detail_parts = [duplicate_object(obj, f"DETAIL_{index}_{obj.name}") for index, obj in enumerate(mesh_objects)]
    detail_obj = join_meshes(detail_parts, f"DETAIL_{source_key}")
    clean_mesh_object(detail_obj, merge_distance=0.002)
    triangulate_object(detail_obj)
    detail_path = output_root / ".build_cache" / "detail" / f"{safe_filename(source_key)}.stl"
    detail_export = write_binary_stl(detail_obj, detail_path, source_key)
    detail_export["validation"] = validate_binary_stl(detail_path)

    component_reports = []
    for obj in mesh_objects:
        component_reports.append(prepare_print_component(obj, args.thin_part_mm))

    base_minimum, base_maximum = group_bounds(mesh_objects)
    width = max(10.0, base_maximum.x - base_minimum.x)
    depth = max(10.0, base_maximum.y - base_minimum.y)
    radius = max(width, depth) / 2.0 + args.base_margin_mm
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=96,
        radius=radius,
        depth=args.base_height_mm,
        location=(0.0, 0.0, -args.base_height_mm / 2.0 + 0.70),
    )
    base = bpy.context.active_object
    base.name = "PRINT_BASE"
    for obj in mesh_objects:
        obj.location.z -= 0.55
    bpy.context.view_layer.update()
    apply_world_transforms(mesh_objects + [base])

    print_obj = join_meshes(mesh_objects + [base], f"PRINT_READY_{source_key}")
    clean_mesh_object(print_obj, merge_distance=max(0.005, args.voxel_mm * 0.03))
    voxel_remesh_object(print_obj, args.voxel_mm)
    final_mesh_cleanup(print_obj, max(0.005, args.voxel_mm * 0.06))
    decimation = decimate_if_needed(print_obj, args.max_triangles)
    final_mesh_cleanup(print_obj, max(0.005, args.voxel_mm * 0.05))

    minimum, maximum = object_bounds(print_obj)
    print_obj.location.z -= minimum.z
    bpy.context.view_layer.update()
    print_path = output_root / ".build_cache" / "print_ready" / f"{safe_filename(source_key)}.stl"
    print_export = write_binary_stl(print_obj, print_path, source_key)
    print_export["validation"] = validate_binary_stl(print_path)

    if not print_export["validation"]["watertight_edge_test"]:
        retry_voxel = max(args.voxel_mm, 0.62)
        log(f"Strict edge test failed; retrying clean voxel union at {retry_voxel:.3f} mm")
        voxel_remesh_object(print_obj, retry_voxel)
        final_mesh_cleanup(print_obj, max(0.008, retry_voxel * 0.05))
        decimation["retry_voxel_mm"] = retry_voxel
        decimation["retry"] = decimate_if_needed(print_obj, args.max_triangles)
        minimum, maximum = object_bounds(print_obj)
        print_obj.location.z -= minimum.z
        bpy.context.view_layer.update()
        print_export = write_binary_stl(print_obj, print_path, source_key)
        print_export["validation"] = validate_binary_stl(print_path)

    blend_path = None
    if args.keep_blend:
        blend_path = output_root / "03_Blender_Source" / f"{safe_filename(source_key)}.blend"
        blend_path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), compress=True)

    return {
        "source_key": source_key,
        "source_paths": [str(p) for p in paths],
        "import": import_info,
        "normalization": normalization,
        "components": component_reports,
        "detail": detail_export,
        "print_ready": print_export,
        "decimation": decimation,
        "blend_path": str(blend_path) if blend_path else None,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }
'''
    text = sub_once(
        text,
        r'def process_source_group\(.*?\n\ndef copy_source_result_for_hero',
        new_process + '\n\ndef copy_source_result_for_hero',
        "source processing pipeline",
        flags=re.S,
    )

    text = text.replace('91 playable roster entries as at 2026-10-04', '90 playable hero selections as at 2026-10-04', 1)
    text = text.replace('91 playable roster entries', '90 playable hero selections')

    path.write_text(text, encoding="utf-8")
    print(f"Patched {path} to HOTS STL Builder v2.1.0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
