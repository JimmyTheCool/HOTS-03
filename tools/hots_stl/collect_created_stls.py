#!/usr/bin/env python3
"""Collect the best available HOTS STL per hero from Actions artifacts and releases.

The script is designed for GitHub Actions. It downloads non-expired artifacts and
release ZIP assets, recursively scans nested ZIPs, validates STL structure, selects
one strongest candidate for each known hero, and creates a single ZIP package with
manifests and provenance.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import time
from typing import BinaryIO, Dict, Iterable, Iterator, List, Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request
import zipfile

API_ROOT = "https://api.github.com"
USER_AGENT = "HOTS-STL-Consolidator/1.0"

HEROES = [
    "Abathur", "Alarak", "Alexstrasza", "Ana", "Anduin", "Anub'arak",
    "Artanis", "Arthas", "Auriel", "Azmodan", "Blaze", "Brightwing",
    "The Butcher", "Cassia", "Chen", "Cho", "Chromie", "D.Va",
    "Deathwing", "Deckard", "Dehaka", "Diablo", "E.T.C.", "Falstad",
    "Fenix", "Gall", "Garrosh", "Gazlowe", "Genji", "Greymane",
    "Gul'dan", "Hanzo", "Hogger", "Illidan", "Imperius", "Jaina",
    "Johanna", "Junkrat", "Kael'thas", "Kel'Thuzad", "Kerrigan",
    "Kharazim", "Leoric", "Li Li", "Li-Ming", "Lt. Morales", "Lucio",
    "Lunara", "Maiev", "Mal'Ganis", "Malfurion", "Malthael", "Medivh",
    "Mei", "Mephisto", "Muradin", "Murky", "Nazeebo", "Nova", "Orphea",
    "Probius", "Qhira", "Ragnaros", "Raynor", "Rehgar", "Rexxar",
    "Samuro", "Sgt. Hammer", "Sonya", "Stitches", "Stukov", "Sylvanas",
    "Tassadar", "The Lost Vikings", "Thrall", "Tracer", "Tychus", "Tyrael",
    "Tyrande", "Uther", "Valeera", "Valla", "Varian", "Whitemane",
    "Xal'atath", "Xul", "Yrel", "Zagara", "Zarya", "Zeratul", "Zul'jin",
]

# Internal Blizzard source identifiers and common generated filename variants.
ALIASES = {
    "firebat": "Blaze",
    "butcher": "The Butcher",
    "thebutcher": "The Butcher",
    "d2amazonf": "Cassia",
    "d2necrom": "Xul",
    "d3barbarianf": "Sonya",
    "d3crusaderf": "Johanna",
    "d3demonhunterf": "Valla",
    "d3monkm": "Kharazim",
    "d3witchdoctorm": "Nazeebo",
    "d3wizardf": "Li-Ming",
    "dryad": "Lunara",
    "kingleoric": "Leoric",
    "medic": "Lt. Morales",
    "meiow": "Mei",
    "nexushunter": "Qhira",
    "lostvikings": "The Lost Vikings",
    "lostvikingscomposite": "The Lost Vikings",
    "dva": "D.Va",
    "dvacomposite": "D.Va",
    "sgthammer": "Sgt. Hammer",
    "ltmorales": "Lt. Morales",
    "chogall": "Cho",
}


def canonical(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


HERO_BY_KEY = {canonical(hero): hero for hero in HEROES}
HERO_BY_KEY.update(ALIASES)


def safe_filename(name: str) -> str:
    result = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")
    return result or "unnamed"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def log(message: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {message}", flush=True)


class GitHubClient:
    def __init__(self, token: str):
        self.token = token

    def request(self, url: str, accept: str = "application/vnd.github+json") -> urllib.response.addinfourl:
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        req = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(req, timeout=180)

    def get_json(self, url: str) -> object:
        with self.request(url) as response:
            return json.load(response)

    def paginate(self, url: str, array_key: Optional[str] = None) -> List[dict]:
        items: List[dict] = []
        page = 1
        separator = "&" if "?" in url else "?"
        while True:
            page_url = f"{url}{separator}per_page=100&page={page}"
            payload = self.get_json(page_url)
            batch = payload.get(array_key, []) if array_key else payload
            if not isinstance(batch, list):
                raise RuntimeError(f"Expected list from {page_url}")
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items

    def download(self, url: str, target: Path, accept: str = "application/octet-stream") -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.request(url, accept=accept) as response, target.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)


def parse_timestamp(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def archive_priority(name: str, created_at: str, source_type: str) -> Tuple[int, float]:
    lower = name.casefold()
    score = 0
    if "v8" in lower:
        score += 800
    elif "v7" in lower:
        score += 700
    elif "v6" in lower:
        score += 600
    elif "v5" in lower:
        score += 500
    elif "v4" in lower:
        score += 400
    elif "v3" in lower:
        score += 300
    elif "v2" in lower:
        score += 200
    if "final" in lower or "complete" in lower or "all_91" in lower:
        score += 500
    if "repair" in lower or "fixed" in lower:
        score += 350
    if "shape" in lower or "silhouette" in lower:
        score += 250
    if "smoke" in lower:
        score += 50
    if source_type == "artifact":
        score += 25
    return score, parse_timestamp(created_at)


def should_download_artifact(artifact: dict) -> bool:
    if artifact.get("expired"):
        return False
    name = str(artifact.get("name", ""))
    lower = name.casefold()
    patterns = (
        "stl", "hots", "repair", "shape", "silhouette", "smoke", "fenix",
        "print", "mesh", "hero", "complete", "chunk",
    )
    if any(token in lower for token in patterns):
        return True
    if re.search(r"(?:^|[-_])(?:v|r)\d+[-_]c[-_]?\d+(?:$|[-_])", lower):
        return True
    if re.fullmatch(r"(?:c|chunk)[-_]?\d+", lower):
        return True
    workflow = artifact.get("workflow_run") or {}
    workflow_name = str(workflow.get("name", "")).casefold()
    return any(token in workflow_name for token in ("stl", "hots", "hero", "repair"))


def path_quality(path_text: str, source_name: str, source_type: str, created_at: str) -> Tuple[int, float]:
    lower = f"{source_name}/{path_text}".casefold().replace("\\", "/")
    score = 0
    if "02_print_ready_watertight" in lower:
        score += 3000
    elif "print_ready_watertight" in lower:
        score += 2900
    elif "print_ready" in lower:
        score += 2700
    elif "watertight" in lower:
        score += 2500
    if "validated" in lower or "validation_passed" in lower:
        score += 700
    if "repaired" in lower or "fixed" in lower:
        score += 600
    elif "repair" in lower:
        score += 450
    if "shape_preservation" in lower or "silhouette_preservation" in lower:
        score += 350
    if "best" in lower or "final" in lower:
        score += 300
    if "detail_preserving" in lower or "01_detail" in lower:
        score -= 600
    if "/.build_cache/" in lower or "build_cache" in lower:
        score -= 1000
    archive_score, timestamp = archive_priority(source_name, created_at, source_type)
    score += archive_score
    return score, timestamp


def identify_hero(filename: str) -> Optional[str]:
    stem = Path(filename).stem
    key = canonical(stem)
    # Remove common output suffixes and prefixes before matching.
    for token in (
        "printreadywatertight", "printready", "watertight", "repaired",
        "repair", "fixed", "final", "detailpreserving", "detail", "print",
        "stormhero", "base", "mesh", "model",
    ):
        key = key.replace(token, "")
    if key in HERO_BY_KEY:
        return HERO_BY_KEY[key]
    # Conservative containment: longest hero key wins.
    matches = [(len(hero_key), hero) for hero_key, hero in HERO_BY_KEY.items() if len(hero_key) >= 4 and hero_key in key]
    if matches:
        matches.sort(reverse=True)
        return matches[0][1]
    return None


def inspect_stl_bytes(data: bytes) -> dict:
    result = {
        "valid": False,
        "format": None,
        "triangles": None,
        "bytes": len(data),
        "reason": None,
    }
    if len(data) >= 84:
        tri_count = struct.unpack_from("<I", data, 80)[0]
        expected = 84 + tri_count * 50
        if expected == len(data) and tri_count > 0:
            result.update({"valid": True, "format": "binary", "triangles": tri_count})
            return result
    stripped = data.lstrip()
    if stripped[:5].lower() == b"solid" and b"facet" in stripped[: min(len(stripped), 1024 * 1024)].lower():
        facet_count = stripped.lower().count(b"facet normal")
        result.update({"valid": facet_count > 0, "format": "ascii", "triangles": facet_count})
        return result
    result["reason"] = "not a structurally recognisable STL"
    return result


class Collector:
    def __init__(self, output_root: Path):
        self.output_root = output_root
        self.stl_dir = output_root / "STL"
        self.reports_dir = output_root / "Reports"
        self.stl_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.best: Dict[str, dict] = {}
        self.unclassified: List[dict] = []
        self.scanned_archives: List[dict] = []
        self.candidate_count = 0
        self.invalid_count = 0
        self.hashes_seen: set[str] = set()

    def consider(self, data: bytes, member_path: str, source: dict) -> None:
        self.candidate_count += 1
        info = inspect_stl_bytes(data)
        if not info["valid"]:
            self.invalid_count += 1
            return
        digest = hashlib.sha256(data).hexdigest()
        hero = identify_hero(Path(member_path).name)
        quality, timestamp = path_quality(member_path, source["name"], source["type"], source.get("created_at", ""))
        record = {
            "hero": hero,
            "member_path": member_path,
            "source_type": source["type"],
            "source_name": source["name"],
            "source_id": source.get("id"),
            "source_created_at": source.get("created_at"),
            "source_url": source.get("url"),
            "quality_score": quality,
            "source_timestamp": timestamp,
            "sha256": digest,
            **info,
        }
        if hero is None:
            if digest not in self.hashes_seen:
                self.unclassified.append(record)
                self.hashes_seen.add(digest)
            return
        key = canonical(hero)
        current = self.best.get(key)
        ranking = (quality, timestamp, info.get("triangles") or 0, len(data))
        current_ranking = None if current is None else (
            current["quality_score"], current["source_timestamp"], current.get("triangles") or 0, current["bytes"]
        )
        if current is None or ranking > current_ranking:
            target = self.stl_dir / f"{safe_filename(hero)}.stl"
            target.write_bytes(data)
            record["output_file"] = target.name
            self.best[key] = record
            log(f"Selected {hero}: {source['name']} :: {member_path} ({info.get('triangles')} triangles)")

    def scan_zip(self, zip_path: Path, source: dict, depth: int = 0, prefix: str = "") -> None:
        if depth > 4:
            return
        try:
            with zipfile.ZipFile(zip_path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    name = member.filename
                    lower = name.casefold()
                    display_path = f"{prefix}{name}"
                    try:
                        if lower.endswith(".stl"):
                            # 200 MB per STL is a generous safety ceiling.
                            if member.file_size > 200 * 1024 * 1024:
                                continue
                            with archive.open(member) as handle:
                                self.consider(handle.read(), display_path, source)
                        elif lower.endswith(".zip") and member.file_size <= 3 * 1024 * 1024 * 1024:
                            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp:
                                temp_path = Path(temp.name)
                                with archive.open(member) as nested:
                                    shutil.copyfileobj(nested, temp, length=1024 * 1024)
                            try:
                                self.scan_zip(temp_path, source, depth + 1, prefix=f"{display_path}!/")
                            finally:
                                temp_path.unlink(missing_ok=True)
                    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                        log(f"WARNING: could not inspect {display_path}: {exc}")
        except zipfile.BadZipFile as exc:
            log(f"WARNING: invalid ZIP {zip_path}: {exc}")

    def record_archive(self, source: dict, status: str, detail: str = "") -> None:
        self.scanned_archives.append({**source, "status": status, "detail": detail})

    def finalise(self) -> dict:
        # Cho and Gall deliberately share one canonical in-game body. Ensure both
        # roster aliases are present whenever either one was recovered.
        cho_key, gall_key = canonical("Cho"), canonical("Gall")
        if cho_key in self.best and gall_key not in self.best:
            source_file = self.stl_dir / self.best[cho_key]["output_file"]
            target = self.stl_dir / "Gall.stl"
            shutil.copy2(source_file, target)
            duplicate = dict(self.best[cho_key])
            duplicate.update({"hero": "Gall", "output_file": target.name, "derived_alias_of": "Cho"})
            self.best[gall_key] = duplicate
        elif gall_key in self.best and cho_key not in self.best:
            source_file = self.stl_dir / self.best[gall_key]["output_file"]
            target = self.stl_dir / "Cho.stl"
            shutil.copy2(source_file, target)
            duplicate = dict(self.best[gall_key])
            duplicate.update({"hero": "Cho", "output_file": target.name, "derived_alias_of": "Gall"})
            self.best[cho_key] = duplicate

        rows = sorted(self.best.values(), key=lambda row: HEROES.index(row["hero"]) if row["hero"] in HEROES else 999)
        missing = [hero for hero in HEROES if canonical(hero) not in self.best]
        manifest = {
            "generated_utc": utc_now(),
            "requested_roster_count": len(HEROES),
            "recovered_hero_count": len(rows),
            "missing_heroes": missing,
            "candidate_stls_scanned": self.candidate_count,
            "invalid_stls_rejected": self.invalid_count,
            "archives_scanned": self.scanned_archives,
            "selected_files": rows,
            "unclassified_unique_stls": self.unclassified,
        }
        (self.reports_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        fieldnames = [
            "hero", "output_file", "triangles", "bytes", "sha256", "format",
            "quality_score", "source_type", "source_name", "source_created_at",
            "source_id", "source_url", "member_path", "derived_alias_of",
        ]
        with (self.reports_dir / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

        with (self.reports_dir / "source_inventory.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            fieldnames_sources = ["type", "name", "id", "created_at", "size", "url", "status", "detail"]
            writer = csv.DictWriter(handle, fieldnames=fieldnames_sources, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.scanned_archives)

        readme = f"""Heroes of the Storm STL files created so far
================================================

Generated: {manifest['generated_utc']}
Recovered hero STL files: {len(rows)} of {len(HEROES)} roster entries
Candidate STL files scanned: {self.candidate_count}
Invalid STL files rejected: {self.invalid_count}

This package consolidates the strongest structurally valid STL found for each hero
across the repository's available GitHub Actions artifacts and release ZIP files.
When several versions existed, print-ready/watertight/repaired outputs were preferred
over detail-only exports, followed by the newest source.

The STL directory contains one selected file per recovered hero. Cho and Gall may
share identical geometry because they use one canonical in-game body. D.Va and The
Lost Vikings use composite files when such files were available.

Missing heroes at packaging time:
{chr(10).join('- ' + hero for hero in missing) if missing else '- None'}

Reports/manifest.csv and manifest.json identify the source archive, nested path,
triangle count, SHA-256 digest and selection score for every included STL.

Important: automated structural validation does not guarantee that every tiny visual
feature, support requirement or wall thickness is ideal for every printer. Inspect
models in your slicer before printing. STL stores geometry only and cannot retain
textures, materials, transparency, glow or normal-map detail.

Heroes of the Storm and its characters/assets are owned by Blizzard Entertainment.
These derivative files are intended for personal, non-commercial fan use.
"""
        (self.output_root / "README_FIRST.txt").write_text(readme, encoding="utf-8")
        return manifest


def process_sources(client: GitHubClient, repo: str, collector: Collector, work_dir: Path, max_download_gb: float) -> None:
    max_bytes = int(max_download_gb * 1024 ** 3)
    downloaded_bytes = 0

    artifacts_url = f"{API_ROOT}/repos/{repo}/actions/artifacts"
    artifacts = client.paginate(artifacts_url, array_key="artifacts")
    artifacts = [item for item in artifacts if should_download_artifact(item)]
    artifacts.sort(
        key=lambda item: archive_priority(str(item.get("name", "")), str(item.get("created_at", "")), "artifact"),
        reverse=True,
    )
    log(f"Eligible non-expired Actions artifacts: {len(artifacts)}")

    for index, artifact in enumerate(artifacts, start=1):
        size = int(artifact.get("size_in_bytes") or 0)
        source = {
            "type": "artifact",
            "name": str(artifact.get("name", f"artifact-{artifact.get('id')}")),
            "id": artifact.get("id"),
            "created_at": artifact.get("created_at"),
            "size": size,
            "url": artifact.get("archive_download_url"),
        }
        if downloaded_bytes + size > max_bytes:
            collector.record_archive(source, "skipped-budget", f"download budget {max_download_gb:.1f} GiB")
            continue
        target = work_dir / f"artifact_{artifact.get('id')}.zip"
        try:
            log(f"Downloading artifact {index}/{len(artifacts)}: {source['name']} ({size / 1024**2:.1f} MiB)")
            client.download(str(artifact["archive_download_url"]), target)
            downloaded_bytes += target.stat().st_size
            collector.scan_zip(target, source)
            collector.record_archive(source, "scanned")
        except Exception as exc:
            collector.record_archive(source, "download-or-scan-failed", str(exc))
            log(f"WARNING: artifact {source['name']} failed: {exc}")
        finally:
            target.unlink(missing_ok=True)

    releases = client.paginate(f"{API_ROOT}/repos/{repo}/releases")
    release_assets: List[Tuple[dict, dict]] = []
    for release in releases:
        for asset in release.get("assets", []):
            name = str(asset.get("name", ""))
            if name.casefold().endswith(".zip") and any(token in name.casefold() for token in ("stl", "hots", "hero")):
                release_assets.append((release, asset))
    release_assets.sort(key=lambda pair: parse_timestamp(pair[0].get("published_at")), reverse=True)
    log(f"Eligible release ZIP assets: {len(release_assets)}")

    for release, asset in release_assets:
        source = {
            "type": "release",
            "name": f"{release.get('tag_name')}__{asset.get('name')}",
            "id": asset.get("id"),
            "created_at": release.get("published_at"),
            "size": int(asset.get("size") or 0),
            "url": asset.get("browser_download_url"),
        }
        target = work_dir / f"release_{asset.get('id')}.zip"
        try:
            log(f"Downloading release asset: {source['name']}")
            # The release-asset API endpoint supports authenticated octet-stream download.
            api_url = str(asset.get("url"))
            client.download(api_url, target)
            collector.scan_zip(target, source)
            collector.record_archive(source, "scanned")
        except Exception as exc:
            collector.record_archive(source, "download-or-scan-failed", str(exc))
            log(f"WARNING: release asset {source['name']} failed: {exc}")
        finally:
            target.unlink(missing_ok=True)


def create_zip(source_root: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=True) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_root.parent).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/repository")
    parser.add_argument("--token", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--max-download-gb", type=float, default=11.0)
    args = parser.parse_args()

    output_root = Path(args.output_dir).resolve()
    archive_path = Path(args.archive).resolve()
    shutil.rmtree(output_root, ignore_errors=True)
    output_root.mkdir(parents=True, exist_ok=True)

    client = GitHubClient(args.token)
    collector = Collector(output_root)
    with tempfile.TemporaryDirectory(prefix="hots-stl-collect-") as temp:
        process_sources(client, args.repo, collector, Path(temp), args.max_download_gb)
    manifest = collector.finalise()
    create_zip(output_root, archive_path)
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_suffix(archive_path.suffix + ".sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")

    summary = {
        "archive": str(archive_path),
        "archive_bytes": archive_path.stat().st_size,
        "archive_sha256": digest,
        "hero_count": manifest["recovered_hero_count"],
        "missing_heroes": manifest["missing_heroes"],
    }
    print("COLLECTION_SUMMARY=" + json.dumps(summary, separators=(",", ":")))
    if manifest["recovered_hero_count"] == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
