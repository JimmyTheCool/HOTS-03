#!/usr/bin/env python3
"""Compare raw and repaired STL silhouettes in three orthographic projections.

Unlike a raw surface-distance comparison, silhouette comparison ignores deleted
interior/duplicate shells while still detecting missing limbs, wings, weapons,
mechs, and other externally visible geometry.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import traceback
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from scipy import ndimage
import trimesh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--repaired", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--report-csv", required=True, type=Path)
    parser.add_argument("--image", type=Path, default=None)
    parser.add_argument("--expected", required=True, type=int)
    parser.add_argument("--pixel-mm", type=float, default=0.65)
    parser.add_argument("--tolerance-mm", type=float, default=1.95)
    parser.add_argument("--minimum-raw-coverage", type=float, default=0.985)
    parser.add_argument("--minimum-repaired-coverage", type=float, default=0.970)
    parser.add_argument("--minimum-iou", type=float, default=0.940)
    parser.add_argument("--maximum-points", type=int, default=500_000)
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


def deterministic_points(mesh: trimesh.Trimesh, maximum: int) -> np.ndarray:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    centroids = np.asarray(mesh.triangles_center, dtype=np.float64)
    if len(vertices) > maximum // 2:
        indices = np.linspace(0, len(vertices) - 1, maximum // 2, dtype=np.int64)
        vertices = vertices[indices]
    remaining = max(0, maximum - len(vertices))
    if remaining > 0 and len(centroids):
        areas = np.asarray(mesh.area_faces, dtype=np.float64)
        valid = np.isfinite(areas) & (areas > 0)
        centroids = centroids[valid]
        areas = areas[valid]
        if len(centroids) > remaining:
            cumulative = np.cumsum(areas)
            if cumulative[-1] > 0:
                targets = np.linspace(0.0, float(cumulative[-1]), remaining, endpoint=False)
                indices = np.searchsorted(cumulative, targets, side="left")
                centroids = centroids[np.clip(indices, 0, len(centroids) - 1)]
            else:
                centroids = centroids[np.linspace(0, len(centroids) - 1, remaining, dtype=np.int64)]
        points = np.vstack((vertices, centroids))
    else:
        points = vertices
    points = points[np.isfinite(points).all(axis=1)]
    if len(points) == 0:
        raise RuntimeError("No finite mesh points available")
    return points


def projection_mask(
    points: np.ndarray,
    axes: tuple[int, int],
    minimum: np.ndarray,
    shape: tuple[int, int],
    pixel_mm: float,
) -> np.ndarray:
    projected = points[:, axes]
    indices = np.rint((projected - minimum) / pixel_mm).astype(np.int64)
    valid = np.all((indices >= 0) & (indices < np.asarray(shape)), axis=1)
    indices = indices[valid]
    mask = np.zeros(shape, dtype=bool)
    if len(indices):
        mask[indices[:, 0], indices[:, 1]] = True
    # Fill sparse sampling holes without materially extending the silhouette.
    mask = ndimage.binary_dilation(mask, iterations=1)
    mask = ndimage.binary_closing(mask, iterations=1)
    mask = ndimage.binary_fill_holes(mask)
    return mask


def compare_projection(raw: np.ndarray, repaired: np.ndarray, tolerance_pixels: int) -> dict[str, float]:
    raw_tolerance = ndimage.binary_dilation(raw, iterations=tolerance_pixels)
    repaired_tolerance = ndimage.binary_dilation(repaired, iterations=tolerance_pixels)
    raw_count = max(1, int(raw.sum()))
    repaired_count = max(1, int(repaired.sum()))
    raw_coverage = float(np.logical_and(raw, repaired_tolerance).sum() / raw_count)
    repaired_coverage = float(np.logical_and(repaired, raw_tolerance).sum() / repaired_count)
    raw_soft = ndimage.binary_dilation(raw, iterations=max(1, tolerance_pixels // 2))
    repaired_soft = ndimage.binary_dilation(repaired, iterations=max(1, tolerance_pixels // 2))
    intersection = int(np.logical_and(raw_soft, repaired_soft).sum())
    union = max(1, int(np.logical_or(raw_soft, repaired_soft).sum()))
    return {
        "raw_pixel_count": int(raw.sum()),
        "repaired_pixel_count": int(repaired.sum()),
        "raw_coverage": raw_coverage,
        "repaired_coverage": repaired_coverage,
        "iou": float(intersection / union),
    }


def compare_one(raw_path: Path, repaired_path: Path, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, tuple[np.ndarray, np.ndarray]]]:
    raw = trimesh.load_mesh(raw_path, force="mesh", process=False)
    repaired = trimesh.load_mesh(repaired_path, force="mesh", process=False)
    if not isinstance(raw, trimesh.Trimesh) or not isinstance(repaired, trimesh.Trimesh):
        raise TypeError("Both inputs must load as Trimesh")
    raw = clean_mesh(raw)
    repaired = clean_mesh(repaired)
    raw_points = deterministic_points(raw, args.maximum_points)
    repaired_points = deterministic_points(repaired, args.maximum_points)

    all_points = np.vstack((raw_points, repaired_points))
    minimum_3d = all_points.min(axis=0) - args.tolerance_mm * 2.0
    maximum_3d = all_points.max(axis=0) + args.tolerance_mm * 2.0
    tolerance_pixels = max(1, int(math.ceil(args.tolerance_mm / args.pixel_mm)))

    views = {
        "front_xz": (0, 2),
        "side_yz": (1, 2),
        "top_xy": (0, 1),
    }
    results: dict[str, Any] = {}
    masks: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    passed = True
    for name, axes in views.items():
        minimum = minimum_3d[list(axes)]
        maximum = maximum_3d[list(axes)]
        shape_array = np.ceil((maximum - minimum) / args.pixel_mm).astype(int) + 1
        shape = (max(3, int(shape_array[0])), max(3, int(shape_array[1])))
        raw_mask = projection_mask(raw_points, axes, minimum, shape, args.pixel_mm)
        repaired_mask = projection_mask(repaired_points, axes, minimum, shape, args.pixel_mm)
        view_result = compare_projection(raw_mask, repaired_mask, tolerance_pixels)
        view_result["passed"] = bool(
            view_result["raw_coverage"] >= args.minimum_raw_coverage
            and view_result["repaired_coverage"] >= args.minimum_repaired_coverage
            and view_result["iou"] >= args.minimum_iou
        )
        passed = passed and view_result["passed"]
        results[name] = view_result
        masks[name] = (raw_mask, repaired_mask)

    return {
        "file": raw_path.name,
        "passed": bool(passed),
        "views": results,
        "error": "",
    }, masks


def save_contact_sheet(
    image_path: Path,
    visual_rows: list[tuple[str, dict[str, tuple[np.ndarray, np.ndarray]]]],
) -> None:
    if not visual_rows:
        return
    views = ("front_xz", "side_yz", "top_xy")
    columns = len(views) * 3
    figure, axes = plt.subplots(len(visual_rows), columns, figsize=(columns * 2.1, len(visual_rows) * 2.4), squeeze=False)
    for row_index, (file_name, masks) in enumerate(visual_rows):
        for view_index, view_name in enumerate(views):
            raw, repaired = masks[view_name]
            overlay = np.zeros((*raw.shape, 3), dtype=np.float32)
            overlay[..., 0] = raw.astype(float)
            overlay[..., 1] = repaired.astype(float)
            items = (
                (raw, f"{file_name}\n{view_name} raw"),
                (repaired, f"{view_name} repaired"),
                (overlay, f"overlay\nred raw / green repaired"),
            )
            for item_index, (image, title) in enumerate(items):
                axis = axes[row_index, view_index * 3 + item_index]
                axis.imshow(np.swapaxes(image, 0, 1), origin="lower", interpolation="nearest")
                axis.set_title(title, fontsize=7)
                axis.axis("off")
    figure.tight_layout()
    image_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(image_path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def main() -> int:
    args = parse_args()
    raw_paths = sorted(args.raw.resolve().glob("*.stl"))
    repaired_root = args.repaired.resolve()
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_csv.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    visual_rows: list[tuple[str, dict[str, tuple[np.ndarray, np.ndarray]]]] = []
    for index, raw_path in enumerate(raw_paths, start=1):
        try:
            repaired_path = repaired_root / raw_path.name
            if not repaired_path.exists():
                raise FileNotFoundError(repaired_path)
            row, masks = compare_one(raw_path, repaired_path, args)
            visual_rows.append((raw_path.name, masks))
        except Exception as exc:
            row = {
                "file": raw_path.name,
                "passed": False,
                "views": {},
                "error": repr(exc) + "\n" + traceback.format_exc(),
            }
        rows.append(row)
        print(f"[{index}/{len(raw_paths)}] {raw_path.name}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)

    failures = [row for row in rows if not row.get("passed")]
    report = {
        "raw_folder": str(args.raw.resolve()),
        "repaired_folder": str(repaired_root),
        "expected_files": args.expected,
        "found_files": len(raw_paths),
        "passed": len(raw_paths) == args.expected and not failures,
        "requirements": {
            "pixel_mm": args.pixel_mm,
            "tolerance_mm": args.tolerance_mm,
            "minimum_raw_coverage": args.minimum_raw_coverage,
            "minimum_repaired_coverage": args.minimum_repaired_coverage,
            "minimum_iou": args.minimum_iou,
            "views": ["front_xz", "side_yz", "top_xy"],
        },
        "failed_files": failures,
        "files": rows,
    }
    args.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    flat_rows = []
    for row in rows:
        flat = {"file": row.get("file"), "passed": row.get("passed"), "error": row.get("error")}
        for view_name in ("front_xz", "side_yz", "top_xy"):
            view = row.get("views", {}).get(view_name, {})
            flat[f"{view_name}_raw_coverage"] = view.get("raw_coverage")
            flat[f"{view_name}_repaired_coverage"] = view.get("repaired_coverage")
            flat[f"{view_name}_iou"] = view.get("iou")
        flat_rows.append(flat)
    with args.report_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0].keys()) if flat_rows else ["file"])
        writer.writeheader()
        writer.writerows(flat_rows)

    if args.image:
        save_contact_sheet(args.image, visual_rows)
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
