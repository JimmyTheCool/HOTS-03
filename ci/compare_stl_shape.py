#!/usr/bin/env python3
"""Compare repaired STL surfaces against their corresponding raw STL surfaces.

This quality gate is deliberately separate from watertightness. It catches a repair
which becomes a valid solid by deleting a limb, weapon, wing, mech component, or other
substantial geometry while retaining similar overall dimensions.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import traceback
from typing import Any

import numpy as np
from scipy.spatial import cKDTree
import trimesh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--repaired", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--report-csv", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=int)
    parser.add_argument("--distance-mm", type=float, default=1.75)
    parser.add_argument("--minimum-raw-coverage", type=float, default=0.96)
    parser.add_argument("--minimum-repaired-coverage", type=float, default=0.85)
    parser.add_argument("--maximum-p95-mm", type=float, default=2.50)
    parser.add_argument("--maximum-p99-mm", type=float, default=5.00)
    parser.add_argument("--maximum-points", type=int, default=250_000)
    return parser.parse_args()


def clean_mesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    mesh = mesh.copy()
    mesh.merge_vertices(
        merge_tex=True,
        merge_norm=True,
        digits_vertex=2,
        digits_norm=2,
    )
    try:
        mesh.update_faces(mesh.nondegenerate_faces())
    except Exception:
        pass
    try:
        mesh.update_faces(mesh.unique_faces())
    except Exception:
        pass
    mesh.remove_unreferenced_vertices()
    return mesh


def deterministic_surface_points(mesh: trimesh.Trimesh, maximum: int) -> np.ndarray:
    """Return deterministic vertices plus area-representative face centroids."""
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    centroids = np.asarray(mesh.triangles_center, dtype=np.float64)

    if len(vertices) > maximum // 2:
        indices = np.linspace(0, len(vertices) - 1, maximum // 2, dtype=np.int64)
        vertices = vertices[indices]
    remaining = max(0, maximum - len(vertices))

    if remaining and len(centroids):
        areas = np.asarray(mesh.area_faces, dtype=np.float64)
        valid = np.isfinite(areas) & (areas > 0)
        centroids = centroids[valid]
        areas = areas[valid]
        if len(centroids) > remaining:
            cumulative = np.cumsum(areas)
            total = float(cumulative[-1]) if len(cumulative) else 0.0
            if total > 0:
                targets = np.linspace(0.0, total, remaining, endpoint=False)
                indices = np.searchsorted(cumulative, targets, side="left")
                indices = np.clip(indices, 0, len(centroids) - 1)
                centroids = centroids[indices]
            else:
                indices = np.linspace(0, len(centroids) - 1, remaining, dtype=np.int64)
                centroids = centroids[indices]
        points = np.vstack((vertices, centroids))
    else:
        points = vertices

    points = points[np.isfinite(points).all(axis=1)]
    if len(points) == 0:
        raise RuntimeError("Mesh produced no finite comparison points")
    return np.ascontiguousarray(points, dtype=np.float64)


def nearest_distance_stats(source: np.ndarray, target: np.ndarray, threshold: float) -> dict[str, float]:
    tree = cKDTree(target)
    distances, _ = tree.query(source, k=1, workers=-1)
    distances = np.asarray(distances, dtype=np.float64)
    return {
        "point_count": int(len(distances)),
        "coverage_within_threshold": float(np.mean(distances <= threshold)),
        "mean_mm": float(np.mean(distances)),
        "median_mm": float(np.median(distances)),
        "p90_mm": float(np.quantile(distances, 0.90)),
        "p95_mm": float(np.quantile(distances, 0.95)),
        "p99_mm": float(np.quantile(distances, 0.99)),
        "maximum_mm": float(np.max(distances)),
    }


def compare_one(raw_path: Path, repaired_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    raw = trimesh.load_mesh(raw_path, force="mesh", process=False)
    repaired = trimesh.load_mesh(repaired_path, force="mesh", process=False)
    if not isinstance(raw, trimesh.Trimesh) or not isinstance(repaired, trimesh.Trimesh):
        raise TypeError("Both files must load as Trimesh objects")
    raw = clean_mesh(raw)
    repaired = clean_mesh(repaired)

    raw_points = deterministic_surface_points(raw, args.maximum_points)
    repaired_points = deterministic_surface_points(repaired, args.maximum_points)
    raw_to_repaired = nearest_distance_stats(raw_points, repaired_points, args.distance_mm)
    repaired_to_raw = nearest_distance_stats(repaired_points, raw_points, args.distance_mm)

    raw_extents = np.asarray(raw.extents, dtype=np.float64)
    repaired_extents = np.asarray(repaired.extents, dtype=np.float64)
    retention = repaired_extents / np.maximum(raw_extents, 1e-9)

    passed = bool(
        raw_to_repaired["coverage_within_threshold"] >= args.minimum_raw_coverage
        and repaired_to_raw["coverage_within_threshold"] >= args.minimum_repaired_coverage
        and raw_to_repaired["p95_mm"] <= args.maximum_p95_mm
        and raw_to_repaired["p99_mm"] <= args.maximum_p99_mm
        and np.all((retention >= 0.90) & (retention <= 1.10))
    )

    return {
        "file": raw_path.name,
        "passed": passed,
        "raw_vertices": int(len(raw.vertices)),
        "raw_triangles": int(len(raw.faces)),
        "repaired_vertices": int(len(repaired.vertices)),
        "repaired_triangles": int(len(repaired.faces)),
        "x_retention": float(retention[0]),
        "y_retention": float(retention[1]),
        "z_retention": float(retention[2]),
        "raw_to_repaired": raw_to_repaired,
        "repaired_to_raw": repaired_to_raw,
        "error": "",
    }


def main() -> int:
    args = parse_args()
    raw_paths = sorted(args.raw.resolve().glob("*.stl"))
    repaired_root = args.repaired.resolve()
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_csv.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for index, raw_path in enumerate(raw_paths, start=1):
        repaired_path = repaired_root / raw_path.name
        try:
            if not repaired_path.exists():
                raise FileNotFoundError(f"Missing repaired file: {repaired_path}")
            row = compare_one(raw_path, repaired_path, args)
        except Exception as exc:
            row = {
                "file": raw_path.name,
                "passed": False,
                "error": repr(exc) + "\n" + traceback.format_exc(),
            }
        rows.append(row)
        print(f"[{index}/{len(raw_paths)}] {raw_path.name}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)

    failures = [row for row in rows if not row.get("passed")]
    report = {
        "raw_folder": str(args.raw.resolve()),
        "repaired_folder": str(repaired_root),
        "expected_files": args.expected,
        "found_raw_files": len(raw_paths),
        "passed": len(raw_paths) == args.expected and not failures,
        "requirements": {
            "distance_threshold_mm": args.distance_mm,
            "minimum_raw_surface_coverage": args.minimum_raw_coverage,
            "minimum_repaired_surface_coverage": args.minimum_repaired_coverage,
            "maximum_raw_to_repaired_p95_mm": args.maximum_p95_mm,
            "maximum_raw_to_repaired_p99_mm": args.maximum_p99_mm,
            "dimension_retention": [0.90, 1.10],
            "maximum_points_per_mesh": args.maximum_points,
        },
        "failed_files": failures,
        "files": rows,
    }
    args.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    flat_rows: list[dict[str, Any]] = []
    for row in rows:
        forward = row.get("raw_to_repaired", {})
        reverse = row.get("repaired_to_raw", {})
        flat_rows.append({
            "file": row.get("file"),
            "passed": row.get("passed"),
            "raw_vertices": row.get("raw_vertices"),
            "raw_triangles": row.get("raw_triangles"),
            "repaired_vertices": row.get("repaired_vertices"),
            "repaired_triangles": row.get("repaired_triangles"),
            "x_retention": row.get("x_retention"),
            "y_retention": row.get("y_retention"),
            "z_retention": row.get("z_retention"),
            "raw_coverage": forward.get("coverage_within_threshold"),
            "raw_p95_mm": forward.get("p95_mm"),
            "raw_p99_mm": forward.get("p99_mm"),
            "raw_maximum_mm": forward.get("maximum_mm"),
            "repaired_coverage": reverse.get("coverage_within_threshold"),
            "repaired_p95_mm": reverse.get("p95_mm"),
            "error": row.get("error"),
        })
    with args.report_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0].keys()) if flat_rows else ["file"])
        writer.writeheader()
        writer.writerows(flat_rows)

    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
