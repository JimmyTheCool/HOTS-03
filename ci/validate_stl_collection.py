#!/usr/bin/env python3
"""Independently validate a folder of final printable STL files."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
import traceback
from typing import Any

import numpy as np
import trimesh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report-json", required=True, type=Path)
    parser.add_argument("--report-csv", required=True, type=Path)
    parser.add_argument("--checksums", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=int)
    parser.add_argument("--max-triangles", type=int, default=500_000)
    parser.add_argument("--minimum-dimension", type=float, default=2.0)
    parser.add_argument("--maximum-dimension", type=float, default=350.0)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    try:
        trimesh.repair.fix_normals(mesh, multibody=True)
    except TypeError:
        trimesh.repair.fix_normals(mesh)
    return mesh


def validate_one(path: Path, args: argparse.Namespace) -> dict[str, Any]:
    mesh = trimesh.load_mesh(path, force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(f"Expected Trimesh, got {type(mesh)!r}")
    mesh = clean_mesh(mesh)
    extents = [float(value) for value in mesh.extents]
    try:
        bodies = int(mesh.body_count)
    except Exception:
        bodies = int(len(mesh.split(only_watertight=False)))
    row = {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "vertices": int(len(mesh.vertices)),
        "triangles": int(len(mesh.faces)),
        "watertight": bool(mesh.is_watertight),
        "winding_consistent": bool(mesh.is_winding_consistent),
        "finite_coordinates": bool(np.isfinite(mesh.vertices).all()),
        "volume_mm3": float(abs(mesh.volume)),
        "x_mm": extents[0],
        "y_mm": extents[1],
        "z_mm": extents[2],
        "connected_bodies": bodies,
        "error": "",
    }
    row["passed"] = bool(
        row["watertight"]
        and row["winding_consistent"]
        and row["finite_coordinates"]
        and row["volume_mm3"] > 1.0
        and 100 <= row["triangles"] <= args.max_triangles
        and min(extents) >= args.minimum_dimension
        and max(extents) <= args.maximum_dimension
        and row["connected_bodies"] == 1
    )
    return row


def main() -> int:
    args = parse_args()
    args.input = args.input.resolve()
    for output in (args.report_json, args.report_csv, args.checksums):
        output.parent.mkdir(parents=True, exist_ok=True)

    paths = sorted(args.input.glob("*.stl"))
    rows: list[dict[str, Any]] = []
    names_seen: set[str] = set()
    duplicates: list[str] = []

    for index, path in enumerate(paths, start=1):
        key = path.name.casefold()
        if key in names_seen:
            duplicates.append(path.name)
        names_seen.add(key)
        try:
            row = validate_one(path, args)
        except Exception as exc:
            row = {
                "file": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "vertices": 0,
                "triangles": 0,
                "watertight": False,
                "winding_consistent": False,
                "finite_coordinates": False,
                "volume_mm3": 0.0,
                "x_mm": 0.0,
                "y_mm": 0.0,
                "z_mm": 0.0,
                "connected_bodies": 0,
                "error": repr(exc) + "\n" + traceback.format_exc(),
                "passed": False,
            }
        rows.append(row)
        print(
            f"[{index}/{len(paths)}] {path.name}: "
            f"{'PASS' if row['passed'] else 'FAIL'}",
            flush=True,
        )

    failures = [row for row in rows if not row.get("passed")]
    report = {
        "input_folder": str(args.input),
        "expected_files": args.expected,
        "found_files": len(rows),
        "duplicate_filenames": duplicates,
        "passed": len(rows) == args.expected and not duplicates and not failures,
        "quality_requirements": {
            "watertight": True,
            "winding_consistent": True,
            "finite_coordinates": True,
            "positive_volume_mm3": True,
            "triangle_range": [100, args.max_triangles],
            "dimension_range_mm": [args.minimum_dimension, args.maximum_dimension],
            "connected_bodies": 1,
            "vertex_weld_tolerance_approx_mm": 0.01,
        },
        "failed_files": failures,
        "files": rows,
    }
    args.report_json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    with args.report_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        fieldnames = list(rows[0].keys()) if rows else ["file"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    args.checksums.write_text(
        "".join(f"{row['sha256']}  {row['file']}\n" for row in rows),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
