#!/usr/bin/env python3
"""Summarize and gate the generated manifest outside Blender."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--minimum-success", type=int, default=1)
    parser.add_argument("--require-roster", type=int, default=0)
    args = parser.parse_args()

    path = Path(args.manifest)
    data = json.loads(path.read_text(encoding="utf-8"))
    heroes = data.get("heroes", [])
    success = [h for h in heroes if h.get("status") == "success"]
    missing = [h for h in heroes if h.get("status") == "missing-source"]
    failed = [h for h in heroes if h.get("status") not in ("success", "missing-source")]
    watertight = [h for h in success if h.get("print_ready", {}).get("validation", {}).get("watertight_edge_test")]

    print(f"Roster entries: {len(heroes)}")
    print(f"Successful: {len(success)}")
    print(f"Strictly watertight print-ready files: {len(watertight)}")
    print(f"Missing source: {len(missing)}")
    print(f"Failed: {len(failed)}")
    if missing:
        print("Missing:", ", ".join(h.get("hero", "?") for h in missing))
    if failed:
        print("Failed:")
        for item in failed:
            print(f"  - {item.get('hero')}: {item.get('error', item.get('status'))}")

    if len(success) < args.minimum_success:
        print(f"ERROR: only {len(success)} successes; required at least {args.minimum_success}", file=sys.stderr)
        return 2
    if args.require_roster and len(heroes) != args.require_roster:
        print(f"ERROR: manifest contains {len(heroes)} roster entries; expected {args.require_roster}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
