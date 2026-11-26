#!/usr/bin/env python3
"""Build the signed image catalog from the official sources.

Each source names a publisher's directory and its published hash file: links and hashes are read
from there, never typed by hand. A source that fails keeps its last published entry and is
reported, so one publisher's outage neither removes an image nor publishes an unverified link.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import grub
from conformite import catalog_errors

from catalogue.construction import build, previous_catalog
from catalogue.banc import bench_errors
from catalogue.controles import source_errors
from catalogue.empreintes import hash_url
from catalogue.page import index_page
from catalogue.veille import newer_releases


def copy_icons(root, families, output):
    hashes = {}
    (output / "icons").mkdir(parents=True, exist_ok=True)
    for family in families:
        source = root / "sources/icons" / family["icon"]
        hashes[family["icon"]] = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copyfile(source, output / "icons" / family["icon"])
    return hashes


def add_grub(catalog, output, previous, failures):
    error = None
    try:
        catalog["grub"], error = grub.prepare(output, previous.get("grub"))
    except (grub.GrubError, OSError, subprocess.CalledProcessError) as failure:
        error = str(failure)
    if error:
        failures.append({"id": "grub", "error": error})
    today = time.strftime("%Y-%m-%d", time.gmtime())
    report = grub.status_report(catalog.get("grub"), output, error, today)
    (output.parent / "grub.json").write_text(json.dumps(report, indent=1) + "\n")


def write_site(root, output, catalog, sources, failures):
    """The catalog in its folder, signed later; the page and the reports at the root."""
    (output / "catalog.json").write_text(json.dumps(catalog, indent=1) + "\n")
    updates = newer_releases(sources)
    (output / "updates.json").write_text(json.dumps(updates, indent=1) + "\n")
    page = index_page(catalog, updates)
    for target in (output.parent, output):
        (target / "index.html").write_text(page)
    for asset in ("site.css", "logo.png"):
        shutil.copyfile(root / "page" / asset, output.parent / asset)
    (output.parent / "failures.json").write_text(json.dumps(failures, indent=1) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("site/v2"))
    parser.add_argument(
        "--previous", action="append", help="address of the catalog published so far; the first that answers"
    )
    parser.add_argument(
        "--no-compute",
        action="store_true",
        help="check links only: do not download images to compute a missing hash",
    )
    parser.add_argument("--grub", action="store_true", help="add GRUB for the vault (built or reused)")
    parser.add_argument("--proofs", type=Path, help="boot proofs written by the weekly bench")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    sources = json.loads((root / "sources/images.json").read_text())
    families = json.loads((root / "sources/families.json").read_text())
    taxonomy = json.loads((root / "sources/taxonomy.json").read_text())
    bench = json.loads((root / "sources/banc.json").read_text())
    errors = source_errors(families, sources, taxonomy) + list(bench_errors(bench, {source["id"] for source in sources}))
    for error in errors:
        print(f"INVALID {error}", file=sys.stderr)
    if errors:
        return 1
    hashes = copy_icons(root, families, args.output)
    previous = previous_catalog(args.previous)
    proofs = json.loads(args.proofs.read_text()) if args.proofs else {}
    catalog, failures = build(
        sources, families, hashes, previous, int(time.time()),
        (lambda *_: "0" * 64) if args.no_compute else hash_url,
        proofs,
    )
    catalog["taxonomy"] = taxonomy
    catalog["vault_families"] = json.loads((root / "sources/vault.json").read_text())
    if args.grub:
        add_grub(catalog, args.output, previous, failures)
    if not any("url" in image for image in catalog["images"]):
        print("no image could be read: nothing to publish", file=sys.stderr)
        return 1
    refused = catalog_errors(catalog)
    for error in refused:
        print(f"INVALID {error}", file=sys.stderr)
    if refused:
        return 1
    write_site(root, args.output, catalog, sources, failures)
    for failure in failures:
        print(f"FAILED {failure['id']}: {failure['error']}", file=sys.stderr)
    print(f"{len(catalog['images'])} images, {len(failures)} failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
