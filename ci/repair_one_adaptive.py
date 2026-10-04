#!/usr/bin/env python3
"""Repair one HOTS hero STL with topology and silhouette-aware candidate selection.

The normal repair is attempted first. If it closes the model by deleting visible
components, progressively finer face-connected voxel reconstructions are tried in
fresh subprocesses. The selected candidate must be one watertight body and must
retain essentially all of the original three-view silhouette.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import numpy as np
import trimesh


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-file", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    parser.add_argument("--normal-script", required=True, type=Path)
    parser.add_argument("--voxel-script", required=True, type=Path)
    parser.add_argument("--validator", required=True, type=Path)
    parser.add_argument("--silhouette", required=True, type=Path)
    parser.add_argument("--max-triangles", type=int, default=500_000)
    parser.add_argument("--target-triangles", type=int, default=440_000)
    parser.add_argument("--minimum-dimension-retention", type=float, default=0.90)
    parser.add_argument("--maximum-dimension-retention", type=float, default=1.10)
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_logged(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    output = completed.stdout or ""
    log_path.write_text(output, encoding="utf-8")
    print(output, end="" if output.endswith("\n") or not output else "\n", flush=True)
    return int(completed.returncode)


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def silhouette_metrics(report: dict[str, Any]) -> dict[str, float]:
    view_rows: list[dict[str, Any]] = []
    for item in report.get("files", []):
        views = item.get("views", {})
        if isinstance(views, dict):
            view_rows.extend(row for row in views.values() if isinstance(row, dict))
    if not view_rows:
        return {
            "minimum_raw_coverage": 0.0,
            "minimum_repaired_coverage": 0.0,
            "minimum_iou": 0.0,
        }
    return {
        "minimum_raw_coverage": min(float(row.get("raw_coverage", 0.0)) for row in view_rows),
        "minimum_repaired_coverage": min(float(row.get("repaired_coverage", 0.0)) for row in view_rows),
        "minimum_iou": min(float(row.get("iou", 0.0)) for row in view_rows),
    }


def candidate_score(metrics: dict[str, float], strict: bool) -> float:
    return (
        (10.0 if strict else 0.0)
        + metrics["minimum_raw_coverage"] * 5.0
        + metrics["minimum_repaired_coverage"] * 2.0
        + metrics["minimum_iou"] * 3.0
    )


def main() -> int:
    args = parse_args()
    source = args.input_file.resolve()
    if not source.is_file():
        raise SystemExit(f"Input STL not found: {source}")

    output_dir = args.output.resolve()
    report_dir = args.report_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    raw_mesh = trimesh.load_mesh(source, force="mesh", process=False)
    if not isinstance(raw_mesh, trimesh.Trimesh):
        raise SystemExit(f"Expected Trimesh for {source.name}")
    raw_extents = [float(value) for value in raw_mesh.extents]
    max_extent = max(raw_extents)

    source_dir = report_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    isolated_source = source_dir / source.name
    shutil.copy2(source, isolated_source)

    candidate_specs: list[tuple[str, Path, float]] = [
        ("normal_0.62", args.normal_script.resolve(), 0.62),
        ("connected_voxel_0.55", args.voxel_script.resolve(), 0.55),
        ("connected_voxel_0.45", args.voxel_script.resolve(), 0.45),
    ]
    # A 0.36 mm grid provides a final high-detail attempt without allowing very
    # large models to exhaust runner memory through multi-gigabyte label arrays.
    if max_extent <= 260.0:
        candidate_specs.append(("connected_voxel_0.36", args.voxel_script.resolve(), 0.36))

    results: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None

    for label, repair_script, pitch in candidate_specs:
        print(f"\n=== {source.name}: candidate {label} ===", flush=True)
        root = report_dir / "candidates" / label
        candidate_output = root / "STL"
        candidate_output.mkdir(parents=True, exist_ok=True)
        repair_json = root / "repair.json"
        repair_csv = root / "repair.csv"

        repair_command = [
            sys.executable,
            str(repair_script),
            "--input", str(source_dir),
            "--output", str(candidate_output),
            "--report-json", str(repair_json),
            "--report-csv", str(repair_csv),
            "--expected", "1",
            "--pitch", str(pitch),
            "--max-triangles", str(args.max_triangles),
            "--target-triangles", str(args.target_triangles),
            "--minimum-dimension-retention", str(args.minimum_dimension_retention),
            "--maximum-dimension-retention", str(args.maximum_dimension_retention),
        ]
        repair_exit = run_logged(repair_command, root / "repair.log")
        candidate_file = candidate_output / source.name

        item: dict[str, Any] = {
            "label": label,
            "pitch_mm": pitch,
            "repair_exit_code": repair_exit,
            "candidate_file_present": candidate_file.is_file(),
            "repair_report": load_json(repair_json),
            "strict_silhouette_passed": False,
            "fallback_silhouette_passed": False,
            "topology_passed": False,
        }

        if not candidate_file.is_file():
            results.append(item)
            continue

        topology_json = root / "topology.json"
        topology_command = [
            sys.executable,
            str(args.validator.resolve()),
            "--input", str(candidate_output),
            "--report-json", str(topology_json),
            "--report-csv", str(root / "topology.csv"),
            "--checksums", str(root / "SHA256SUMS.txt"),
            "--expected", "1",
        ]
        topology_exit = run_logged(topology_command, root / "topology.log")
        topology_report = load_json(topology_json)
        topology_passed = topology_exit == 0 and bool(topology_report.get("passed"))
        item["topology_exit_code"] = topology_exit
        item["topology_passed"] = topology_passed
        item["topology_report"] = topology_report

        silhouette_json = root / "silhouette.json"
        silhouette_command = [
            sys.executable,
            str(args.silhouette.resolve()),
            "--raw", str(source_dir),
            "--repaired", str(candidate_output),
            "--report-json", str(silhouette_json),
            "--report-csv", str(root / "silhouette.csv"),
            "--image", str(root / "silhouette.png"),
            "--expected", "1",
            "--pixel-mm", "0.65",
            "--tolerance-mm", "1.95",
            "--minimum-raw-coverage", "0.985",
            "--minimum-repaired-coverage", "0.970",
            "--minimum-iou", "0.940",
            "--maximum-points", "500000",
        ]
        silhouette_exit = run_logged(silhouette_command, root / "silhouette.log")
        silhouette_report = load_json(silhouette_json)
        metrics = silhouette_metrics(silhouette_report)
        strict_pass = topology_passed and bool(silhouette_report.get("passed"))
        # The fallback still prohibits meaningful missing silhouette. It permits a
        # small amount of added geometry from narrow bridges/supportive unions.
        fallback_pass = bool(
            topology_passed
            and metrics["minimum_raw_coverage"] >= 0.985
            and metrics["minimum_repaired_coverage"] >= 0.90
            and metrics["minimum_iou"] >= 0.85
        )
        item.update({
            "silhouette_exit_code": silhouette_exit,
            "silhouette_report": silhouette_report,
            "silhouette_metrics": metrics,
            "strict_silhouette_passed": strict_pass,
            "fallback_silhouette_passed": fallback_pass,
            "score": candidate_score(metrics, strict_pass),
            "candidate_sha256": sha256_file(candidate_file),
            "candidate_bytes": candidate_file.stat().st_size,
            "candidate_path": str(candidate_file),
        })
        results.append(item)

        if strict_pass:
            selected = item
            break

    if selected is None:
        acceptable = [item for item in results if item.get("fallback_silhouette_passed")]
        if acceptable:
            selected = max(acceptable, key=lambda item: float(item.get("score", 0.0)))

    summary: dict[str, Any] = {
        "file": source.name,
        "input_sha256": sha256_file(source),
        "input_bytes": source.stat().st_size,
        "input_extents_mm": raw_extents,
        "candidates": results,
        "passed": selected is not None,
    }

    if selected is None:
        summary["error"] = "No candidate met topology and silhouette-retention requirements"
        (report_dir / "adaptive_summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(summary["error"], file=sys.stderr)
        return 1

    chosen_path = Path(str(selected["candidate_path"]))
    final_path = output_dir / source.name
    shutil.copy2(chosen_path, final_path)
    summary.update({
        "selected_candidate": selected["label"],
        "selection_tier": (
            "strict" if selected.get("strict_silhouette_passed") else "silhouette-preserving fallback"
        ),
        "output_path": str(final_path),
        "output_sha256": sha256_file(final_path),
        "output_bytes": final_path.stat().st_size,
    })
    (report_dir / "adaptive_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(
        f"SELECTED {selected['label']} ({summary['selection_tier']}) for {source.name}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
