#!/usr/bin/env python3
"""Recover generated HOTS STL files using GitHub CLI artifact downloads."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse


HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "collect_created_stls.py"
SPEC = importlib.util.spec_from_file_location("hots_stl_collector_base_r3", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Could not load {BASE_PATH}")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)


_original_download = base.GitHubClient.download


def authenticated_download(self, url: str, target: Path, accept: str = "application/octet-stream") -> None:
    """Download Actions artifacts through gh, which follows GitHub's signed redirect."""
    if "/actions/artifacts/" not in url or not url.endswith("/zip"):
        return _original_download(self, url, target, accept=accept)

    parsed = urllib.parse.urlparse(url)
    endpoint = parsed.path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.setdefault("GH_TOKEN", self.token)
    with target.open("wb") as handle:
        process = subprocess.run(
            ["gh", "api", endpoint],
            stdout=handle,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
    if process.returncode != 0:
        target.unlink(missing_ok=True)
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"gh api artifact download failed ({process.returncode}): {message}")
    if not target.exists() or target.stat().st_size < 22:
        target.unlink(missing_ok=True)
        raise RuntimeError("gh api artifact download produced an empty or invalid file")


base.GitHubClient.download = authenticated_download


_original_archive_priority = base.archive_priority


def corrected_archive_priority(name: str, created_at: str, source_type: str):
    score, timestamp = _original_archive_priority(name, created_at, source_type)
    lower = name.casefold()
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
