#!/usr/bin/env python3
"""Run the HOTS STL consolidator with corrected artifact downloads and ranking."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "collect_created_stls.py"
SPEC = importlib.util.spec_from_file_location("hots_stl_collector_base", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {BASE_PATH}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)


_original_download = base.GitHubClient.download


def corrected_download(self, url: str, target: Path, accept: str = "application/octet-stream") -> None:
    """GitHub Actions artifact downloads require the standard GitHub media type."""
    if "/actions/artifacts/" in url and url.endswith("/zip"):
        accept = "application/vnd.github+json"
    return _original_download(self, url, target, accept=accept)


base.GitHubClient.download = corrected_download


_original_archive_priority = base.archive_priority


def corrected_archive_priority(name: str, created_at: str, source_type: str):
    score, timestamp = _original_archive_priority(name, created_at, source_type)
    lower = name.casefold()
    # Strongly prefer later adaptive versions. The original scores were deliberately
    # conservative and allowed an older "fixed" archive to outrank a v8 final group.
    version_bonus = 0
    for version in range(8, 1, -1):
        if f"v{version}" in lower:
            version_bonus = version * 1000
            break
    if "rescue" in lower:
        version_bonus += 400
    return score + version_bonus, timestamp


base.archive_priority = corrected_archive_priority


_original_path_quality = base.path_quality


def corrected_path_quality(path_text: str, source_name: str, source_type: str, created_at: str):
    score, timestamp = _original_path_quality(path_text, source_name, source_type, created_at)
    normalized = f"{source_name}/{path_text}".casefold().replace("\\", "/")

    # Adaptive v8 groups put their selected and validated output directly in
    # STL/<Hero>.stl. Diagnostic candidates and source copies are also retained in
    # the same artifact, so explicitly rank the final top-level output highest.
    is_top_level_final = (
        re.search(r"(?:^|/)stl/[^/]+\.stl$", normalized) is not None
        and "/candidates/" not in normalized
        and "/source/" not in normalized
    )
    if is_top_level_final:
        score += 5000
    if "/candidates/" in normalized:
        score += 750
    if "/source/" in normalized:
        score -= 5000
    return score, timestamp


base.path_quality = corrected_path_quality


if __name__ == "__main__":
    raise SystemExit(base.main())
