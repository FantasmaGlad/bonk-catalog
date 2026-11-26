#!/usr/bin/env python3
"""Which vault family starts each ISO of the published catalog, read by the published Bonk."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from catalogue.couverture import measure, merge

TIMEOUT_SECONDS = 300


def bonk_info(bonk):
    def info(url):
        try:
            done = subprocess.run(
                [bonk, "info", url, "--json"], capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
            )
        except subprocess.TimeoutExpired:
            print(f"UNREAD {url}: no answer in {TIMEOUT_SECONDS} s", file=sys.stderr)
            return None
        if done.returncode != 0:
            print(f"UNREAD {url}: {done.stderr.strip()[:300]}", file=sys.stderr)
            return None
        try:
            return json.loads(done.stdout)
        except ValueError:
            print(f"UNREAD {url}: not JSON", file=sys.stderr)
            return None

    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--bonk", required=True)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    previous = json.loads(args.previous.read_text(encoding="utf-8")) if args.previous.exists() else {}
    coverage, unreadable = merge(measure(catalog["images"], bonk_info(args.bonk)), previous, args.date)
    args.out.write_text(json.dumps(coverage, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    recognised = sum(1 for entry in coverage.values() if entry["family"])
    print(f"{recognised} of {len(coverage)} measured ISO images recognised, {len(unreadable)} not read this time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
