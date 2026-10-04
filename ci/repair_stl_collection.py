#!/usr/bin/env python3
"""
Repair a folder of printable STL files and enforce one closed connected solid.

Repair order:
1. Weld STL vertices at 0.01 mm and remove duplicate/degenerate faces.
2. Run MeshFix with component joining while preserving all components.
3. If still invalid, reconstruct a connected occupancy grid and marching-cubes mesh.

The script always writes a JSON and CSV report and exits non-zero when any file
cannot satisfy the quality gate.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback
from typing import Any

import numpy as np
import pymeshfix
from scipy import ndimage
from scipy.spatial import cKDTree
import trimesh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--report-csv", required=True, type=Path)
    parser.add_argument("--expected", type=int, default=None)
    parser.add_argument("--pitch", type=float, default=0.62)
    parser.add_argument("--max-triangles", type=int, default=500_000)
    parser.add_argument("--target-triangles", type=int, default=440_000)
    parser.add_argument("--minimum-dimension", type=float, default=2.0)
    parser.add_argument("--maximum-dimension", type=float, default=350.0)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_mesh(mesh: trimesh.Trimesh, digits: int = 2) -> trimesh.Trimesh:
    cleaned = mesh.copy()
    cleaned.merge_vertices(
        merge_tex=True,
        merge_norm=True,
        digits_vertex=digits,
        digits_norm=digits,
    )
    try:
        cleaned.update_faces(cleaned.nondegenerate_faces())
    except Exception:
        pass
    try:
        cleaned.update_faces(cleaned.unique_faces())
    except Exception:
        pass
    cleaned.remove_unreferenced_vertices()
    try:
        trimesh.repair.fix_normals(cleaned, multibody=True)
    except TypeError:
        trimesh.repair.fix_normals(cleaned)
    return cleaned


def mesh_body_count(mesh: trimesh.Trimesh) -> int:
    try:
        return int(mesh.body_count)
    except Exception:
        return int(len(mesh.split(only_watertight=False)))


def mesh_stats(mesh: trimesh.Trimesh) -> dict[str, Any]:
    inspected = clean_mesh(mesh)
    extents = [float(value) for value in inspected.extents]
    return {
        "vertices": int(len(inspected.vertices)),
        "triangles": int(len(inspected.faces)),
        "watertight": bool(inspected.is_watertight),
        "winding_consistent": bool(inspected.is_winding_consistent),
        "finite_coordinates": bool(np.isfinite(inspected.vertices).all()),
        "volume_mm3": float(abs(inspected.volume)),
        "x_mm": extents[0],
        "y_mm": extents[1],
        "z_mm": extents[2],
        "connected_bodies": mesh_body_count(inspected),
    }


def stats_pass(stats: dict[str, Any], args: argparse.Namespace) -> bool:
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


def repair_with_meshfix(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    prepared = clean_mesh(mesh)
    vertices = np.ascontiguousarray(prepared.vertices, dtype=np.float64)
    faces = np.ascontiguousarray(prepared.faces, dtype=np.int32)
    fixed_vertices, fixed_faces = pymeshfix.clean_from_arrays(
        vertices,
        faces,
        verbose=False,
        joincomp=True,
        remove_smallest_components=False,
    )
    if len(fixed_vertices) < 4 or len(fixed_faces) < 4:
        raise RuntimeError("MeshFix returned an empty or trivial mesh")
    fixed = trimesh.Trimesh(
        vertices=fixed_vertices,
        faces=fixed_faces,
        process=True,
        validate=True,
    )
    return clean_mesh(fixed)


def line_points(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    distance = float(np.linalg.norm(b.astype(float) - a.astype(float)))
    count = max(2, int(math.ceil(distance * 2.0)) + 1)
    return np.rint(np.linspace(a, b, count)).astype(np.int64)


def connect_occupancy_components(matrix: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    matrix = np.asarray(matrix, dtype=bool).copy()
    connectivity_structure = np.ones((3, 3, 3), dtype=bool)
    labels, initial_count = ndimage.label(matrix, structure=connectivity_structure)
    if initial_count <= 1:
        return matrix, {
            "initial_components": int(initial_count),
            "removed_tiny_components": 0,
            "bridges_added": 0,
            "final_components": int(initial_count),
        }

    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    tiny_limit = max(8, int(matrix.sum() * 0.000002))
    removed = 0
    for label_index in range(1, initial_count + 1):
        if sizes[label_index] < tiny_limit:
            matrix[labels == label_index] = False
            removed += 1

    labels, component_count = ndimage.label(matrix, structure=connectivity_structure)
    if component_count <= 1:
        return matrix, {
            "initial_components": int(initial_count),
            "removed_tiny_components": removed,
            "bridges_added": 0,
            "final_components": int(component_count),
        }

    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    main_label = int(np.argmax(sizes))
    main_points = np.argwhere(labels == main_label)
    main_tree = cKDTree(main_points)
    bridge_mask = np.zeros_like(matrix, dtype=bool)
    bridges = 0

    for label_index in range(1, component_count + 1):
        if label_index == main_label:
            continue
        points = np.argwhere(labels == label_index)
        if len(points) == 0:
            continue
        if len(points) > 20_000:
            stride = max(1, len(points) // 20_000)
            query_points = points[::stride]
        else:
            query_points = points
        distances, nearest_indices = main_tree.query(query_points, k=1)
        selected = int(np.argmin(distances))
        source = query_points[selected]
        target = main_points[int(nearest_indices[selected])]
        points_on_line = line_points(source, target)
        valid = np.all(
            (points_on_line >= 0)
            & (points_on_line < np.asarray(matrix.shape, dtype=np.int64)),
            axis=1,
        )
        points_on_line = points_on_line[valid]
        bridge_mask[tuple(points_on_line.T)] = True
        bridges += 1

    bridge_mask = ndimage.binary_dilation(
        bridge_mask,
        structure=ndimage.generate_binary_structure(3, 2),
        iterations=1,
    )
    matrix |= bridge_mask
    matrix = ndimage.binary_closing(
        matrix,
        structure=ndimage.generate_binary_structure(3, 2),
        iterations=1,
    )
    matrix = ndimage.binary_fill_holes(matrix)

    final_labels, final_count = ndimage.label(matrix, structure=connectivity_structure)
    if final_count > 1:
        # A final deterministic pass joins any components left after morphology.
        final_sizes = np.bincount(final_labels.ravel())
        final_sizes[0] = 0
        final_main_label = int(np.argmax(final_sizes))
        final_main_points = np.argwhere(final_labels == final_main_label)
        final_tree = cKDTree(final_main_points)
        final_bridge_mask = np.zeros_like(matrix, dtype=bool)
        for label_index in range(1, final_count + 1):
            if label_index == final_main_label:
                continue
            points = np.argwhere(final_labels == label_index)
            if len(points) == 0:
                continue
            query_points = points[:: max(1, len(points) // 20_000)]
            distances, nearest_indices = final_tree.query(query_points, k=1)
            selected = int(np.argmin(distances))
            source = query_points[selected]
            target = final_main_points[int(nearest_indices[selected])]
            points_on_line = line_points(source, target)
            valid = np.all(
                (points_on_line >= 0)
                & (points_on_line < np.asarray(matrix.shape, dtype=np.int64)),
                axis=1,
            )
            points_on_line = points_on_line[valid]
            final_bridge_mask[tuple(points_on_line.T)] = True
            bridges += 1
        final_bridge_mask = ndimage.binary_dilation(
            final_bridge_mask,
            structure=ndimage.generate_binary_structure(3, 2),
            iterations=1,
        )
        matrix |= final_bridge_mask
        matrix = ndimage.binary_fill_holes(matrix)
        _, final_count = ndimage.label(matrix, structure=connectivity_structure)

    return matrix, {
        "initial_components": int(initial_count),
        "removed_tiny_components": int(removed),
        "bridges_added": int(bridges),
        "final_components": int(final_count),
    }


def reconstruct_connected_voxels(
    mesh: trimesh.Trimesh,
    pitch: float,
) -> tuple[trimesh.Trimesh, dict[str, int]]:
    prepared = clean_mesh(mesh)
    voxel_grid = prepared.voxelized(pitch=pitch, method="subdivide")
    matrix = np.asarray(voxel_grid.matrix, dtype=bool).copy()
    matrix = ndimage.binary_closing(
        matrix,
        structure=ndimage.generate_binary_structure(3, 2),
        iterations=1,
    )
    matrix = ndimage.binary_fill_holes(matrix)
    matrix, connectivity = connect_occupancy_components(matrix)
    reconstructed = trimesh.voxel.ops.matrix_to_marching_cubes(
        matrix,
        pitch=float(pitch),
    )
    reconstructed.apply_translation(np.asarray(voxel_grid.origin, dtype=float))
    reconstructed = clean_mesh(reconstructed)
    return reconstructed, connectivity


def reduce_triangles_if_needed(
    mesh: trimesh.Trimesh,
    target: int,
) -> trimesh.Trimesh:
    if len(mesh.faces) <= target:
        return mesh
    try:
        simplified = mesh.simplify_quadric_decimation(face_count=target)
    except TypeError:
        simplified = mesh.simplify_quadric_decimation(target)
    return clean_mesh(simplified)


def process_one(path: Path, output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "file": path.name,
        "input_bytes": path.stat().st_size,
        "input_sha256": sha256_file(path),
        "attempts": {},
        "passed": False,
    }

    original = trimesh.load_mesh(path, force="mesh", process=False)
    if not isinstance(original, trimesh.Trimesh):
        raise TypeError(f"Expected Trimesh, got {type(original)!r}")
    original = clean_mesh(original)
    entry["attempts"]["rounded_vertex_merge"] = mesh_stats(original)

    selected: trimesh.Trimesh | None = None
    selected_method: str | None = None

    if stats_pass(entry["attempts"]["rounded_vertex_merge"], args):
        selected = original
        selected_method = "rounded_vertex_merge"

    if selected is None:
        try:
            fixed = repair_with_meshfix(original)
            fixed = reduce_triangles_if_needed(fixed, args.target_triangles)
            fixed_stats = mesh_stats(fixed)
            entry["attempts"]["pymeshfix"] = fixed_stats
            if stats_pass(fixed_stats, args):
                selected = fixed
                selected_method = "pymeshfix"
        except Exception as exc:
            entry["attempts"]["pymeshfix_error"] = {
                "error": repr(exc),
                "traceback": traceback.format_exc(),
            }

    if selected is None:
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

    if selected is None:
        entry["error"] = "All repair methods failed the quality gate"
        return entry

    output_path = output_dir / path.name
    output_dir.mkdir(parents=True, exist_ok=True)
    selected.export(output_path, file_type="stl")

    reloaded = trimesh.load_mesh(output_path, force="mesh", process=False)
    reloaded = clean_mesh(reloaded)
    final_stats = mesh_stats(reloaded)
    entry["selected_method"] = selected_method
    entry["output_bytes"] = output_path.stat().st_size
    entry["output_sha256"] = sha256_file(output_path)
    entry["final"] = final_stats
    entry["passed"] = stats_pass(final_stats, args)
    if not entry["passed"]:
        entry["error"] = "Exported STL failed the final reload quality gate"
    return entry


def main() -> int:
    args = parse_args()
    args.input = args.input.resolve()
    args.output = args.output.resolve()
    args.report_json = args.report_json.resolve()
    args.report_csv = args.report_csv.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_csv.parent.mkdir(parents=True, exist_ok=True)

    paths = sorted(args.input.glob("*.stl"))
    results: list[dict[str, Any]] = []
    for index, path in enumerate(paths, start=1):
        print(f"[{index}/{len(paths)}] Repairing {path.name}", flush=True)
        try:
            result = process_one(path, args.output, args)
        except Exception as exc:
            result = {
                "file": path.name,
                "passed": False,
                "fatal_error": repr(exc),
                "traceback": traceback.format_exc(),
            }
        results.append(result)
        print(
            f"    {'PASS' if result.get('passed') else 'FAIL'}"
            f" via {result.get('selected_method', 'none')}",
            flush=True,
        )

    expected_ok = args.expected is None or len(paths) == args.expected
    failures = [item for item in results if not item.get("passed")]
    report = {
        "input_folder": str(args.input),
        "output_folder": str(args.output),
        "expected_files": args.expected,
        "found_files": len(paths),
        "expected_count_passed": expected_ok,
        "all_passed": expected_ok and not failures,
        "settings": {
            "voxel_pitch_mm": args.pitch,
            "max_triangles": args.max_triangles,
            "target_triangles": args.target_triangles,
            "minimum_dimension_mm": args.minimum_dimension,
            "maximum_dimension_mm": args.maximum_dimension,
            "vertex_weld_digits": 2,
            "required_connected_bodies": 1,
        },
        "failed_files": failures,
        "files": results,
    }
    args.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    flat_rows = []
    for item in results:
        final = item.get("final", {})
        flat_rows.append(
            {
                "file": item.get("file"),
                "passed": item.get("passed"),
                "selected_method": item.get("selected_method"),
                "vertices": final.get("vertices"),
                "triangles": final.get("triangles"),
                "watertight": final.get("watertight"),
                "winding_consistent": final.get("winding_consistent"),
                "finite_coordinates": final.get("finite_coordinates"),
                "volume_mm3": final.get("volume_mm3"),
                "x_mm": final.get("x_mm"),
                "y_mm": final.get("y_mm"),
                "z_mm": final.get("z_mm"),
                "connected_bodies": final.get("connected_bodies"),
                "input_sha256": item.get("input_sha256"),
                "output_sha256": item.get("output_sha256"),
                "error": item.get("error") or item.get("fatal_error"),
            }
        )
    with args.report_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0].keys()) if flat_rows else ["file"])
        writer.writeheader()
        writer.writerows(flat_rows)

    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
