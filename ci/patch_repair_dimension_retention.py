#!/usr/bin/env python3
"""Patch the STL repair utility to reject geometry loss during repair."""
from pathlib import Path
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one {label} target, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: patch_repair_dimension_retention.py REPAIR_SCRIPT")
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '    parser.add_argument("--maximum-dimension", type=float, default=350.0)\n'
        '    return parser.parse_args()\n',
        '    parser.add_argument("--maximum-dimension", type=float, default=350.0)\n'
        '    parser.add_argument("--minimum-dimension-retention", type=float, default=0.90)\n'
        '    parser.add_argument("--maximum-dimension-retention", type=float, default=1.10)\n'
        '    return parser.parse_args()\n',
        "dimension-retention arguments",
    )

    old_function = '''def stats_pass(stats: dict[str, Any], args: argparse.Namespace) -> bool:
    dimensions = [stats["x_mm"], stats["y_mm"], stats["z_mm"]]
    return bool(
        stats["watertight"]
        and stats["winding_consistent"]
        and stats["finite_coordinates"]
        and stats["volume_mm3"] > 1.0
        and 100 <= stats["triangles"] <= args.max_triangles
        and min(dimensions) >= args.minimum_dimension
        and max(dimensions) <= args.maximum_dimension
        and stats["connected_bodies"] == 1
    )
'''
    new_function = '''def stats_pass(stats: dict[str, Any], args: argparse.Namespace) -> bool:
    dimensions = [stats["x_mm"], stats["y_mm"], stats["z_mm"]]
    return bool(
        stats["watertight"]
        and stats["winding_consistent"]
        and stats["finite_coordinates"]
        and stats["volume_mm3"] > 1.0
        and 100 <= stats["triangles"] <= args.max_triangles
        and min(dimensions) >= args.minimum_dimension
        and max(dimensions) <= args.maximum_dimension
        and stats["connected_bodies"] == 1
    )


def dimension_retention(
    candidate: dict[str, Any],
    original: dict[str, Any],
) -> dict[str, float]:
    ratios = {}
    for axis in ("x_mm", "y_mm", "z_mm"):
        denominator = max(float(original[axis]), 1e-9)
        ratios[axis] = float(candidate[axis]) / denominator
    return ratios


def candidate_pass(
    candidate: dict[str, Any],
    original: dict[str, Any],
    args: argparse.Namespace,
) -> bool:
    ratios = dimension_retention(candidate, original)
    candidate["dimension_retention"] = ratios
    return bool(
        stats_pass(candidate, args)
        and all(
            args.minimum_dimension_retention <= ratio <= args.maximum_dimension_retention
            for ratio in ratios.values()
        )
    )
'''
    text = replace_once(text, old_function, new_function, "quality-gate function")

    text = replace_once(
        text,
        '    original = clean_mesh(original)\n'
        '    entry["attempts"]["rounded_vertex_merge"] = mesh_stats(original)\n',
        '    original = clean_mesh(original)\n'
        '    original_stats = mesh_stats(original)\n'
        '    original_stats["dimension_retention"] = {"x_mm": 1.0, "y_mm": 1.0, "z_mm": 1.0}\n'
        '    entry["attempts"]["rounded_vertex_merge"] = original_stats\n',
        "original statistics capture",
    )

    text = replace_once(
        text,
        '    if stats_pass(entry["attempts"]["rounded_vertex_merge"], args):\n',
        '    if candidate_pass(entry["attempts"]["rounded_vertex_merge"], original_stats, args):\n',
        "original candidate gate",
    )
    text = replace_once(
        text,
        '            if stats_pass(fixed_stats, args):\n',
        '            if candidate_pass(fixed_stats, original_stats, args):\n',
        "MeshFix candidate gate",
    )

    old_voxel = '''    if selected is None:
        voxel, connectivity = reconstruct_connected_voxels(original, args.pitch)
        voxel = reduce_triangles_if_needed(voxel, args.target_triangles)
        # A final MeshFix pass repairs rare marching-cubes/decimation anomalies.
        try:
            voxel = repair_with_meshfix(voxel)
        except Exception:
            voxel = clean_mesh(voxel)
        voxel_stats = mesh_stats(voxel)
        entry["attempts"]["connected_voxel"] = voxel_stats
        entry["voxel_connectivity"] = connectivity
        if stats_pass(voxel_stats, args):
            selected = voxel
            selected_method = "connected_voxel"
'''
    new_voxel = '''    if selected is None:
        voxel, connectivity = reconstruct_connected_voxels(original, args.pitch)
        voxel = reduce_triangles_if_needed(voxel, args.target_triangles)
        voxel_stats = mesh_stats(voxel)
        entry["attempts"]["connected_voxel"] = voxel_stats
        entry["voxel_connectivity"] = connectivity
        if candidate_pass(voxel_stats, original_stats, args):
            selected = voxel
            selected_method = "connected_voxel"
        else:
            # Marching cubes is normally closed already. Use MeshFix only when the
            # directly reconstructed surface still fails, and reject it if dimensions
            # collapse or expand beyond the retention limits.
            try:
                voxel_fixed = repair_with_meshfix(voxel)
                voxel_fixed = reduce_triangles_if_needed(voxel_fixed, args.target_triangles)
                voxel_fixed_stats = mesh_stats(voxel_fixed)
                entry["attempts"]["connected_voxel_plus_meshfix"] = voxel_fixed_stats
                if candidate_pass(voxel_fixed_stats, original_stats, args):
                    selected = voxel_fixed
                    selected_method = "connected_voxel_plus_meshfix"
            except Exception as exc:
                entry["attempts"]["connected_voxel_plus_meshfix_error"] = {
                    "error": repr(exc),
                    "traceback": traceback.format_exc(),
                }
'''
    text = replace_once(text, old_voxel, new_voxel, "connected voxel fallback")

    text = replace_once(
        text,
        '    entry["final"] = final_stats\n'
        '    entry["passed"] = stats_pass(final_stats, args)\n',
        '    entry["final"] = final_stats\n'
        '    entry["passed"] = candidate_pass(final_stats, original_stats, args)\n',
        "final reload gate",
    )

    text = replace_once(
        text,
        '            "maximum_dimension_mm": args.maximum_dimension,\n'
        '            "vertex_weld_digits": 2,\n',
        '            "maximum_dimension_mm": args.maximum_dimension,\n'
        '            "minimum_dimension_retention": args.minimum_dimension_retention,\n'
        '            "maximum_dimension_retention": args.maximum_dimension_retention,\n'
        '            "vertex_weld_digits": 2,\n',
        "report settings",
    )

    text = replace_once(
        text,
        '                "connected_bodies": final.get("connected_bodies"),\n'
        '                "input_sha256": item.get("input_sha256"),\n',
        '                "connected_bodies": final.get("connected_bodies"),\n'
        '                "x_retention": final.get("dimension_retention", {}).get("x_mm"),\n'
        '                "y_retention": final.get("dimension_retention", {}).get("y_mm"),\n'
        '                "z_retention": final.get("dimension_retention", {}).get("z_mm"),\n'
        '                "input_sha256": item.get("input_sha256"),\n',
        "CSV retention fields",
    )

    path.write_text(text, encoding="utf-8")
    print(f"Patched {path} with dimensional-retention protection")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
