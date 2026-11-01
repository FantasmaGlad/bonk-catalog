#!/usr/bin/env python3
"""Build the signed image catalog from the official sources.

Each source names a publisher's directory and its published hash file: links and hashes are read
from there, never typed by hand. A source that fails keeps its last published entry and is
reported, so one publisher's outage neither removes an image nor publishes an unverified link.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

SCHEMA_VERSION = 1
LIFETIME_SECONDS = 14 * 24 * 3600
TIMEOUT_SECONDS = 60
USER_AGENT = "bonk-catalog"
TEXT_LIMIT = 4 << 20
SUMS_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S+)$")
SUMS_BSD = re.compile(r"^SHA256 \((.+)\) = ([0-9a-fA-F]{64})$")


class SourceError(Exception):
    """A source that cannot be trusted or read."""


def request(url, method="GET"):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS)


def fetch_text(url):
    with request(url) as response:
        return response.read(TEXT_LIMIT).decode("utf-8", "replace")


def head(url):
    """Size and date of a file; the final address must still be https after redirects."""
    with request(url, "HEAD") as response:
        if not response.geturl().startswith("https://"):
            raise SourceError(f"{url}: redirected out of https")
        size = int(response.headers.get("Content-Length") or 0)
        modified = response.headers.get("Last-Modified")
    if size <= 0:
        raise SourceError(f"{url}: no size")
    if not modified:
        raise SourceError(f"{url}: no date")
    return size, parsedate_to_datetime(modified).date().isoformat()


def parse_sums(text):
    """File name to hash, from either format publishers use."""
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if match := SUMS_LINE.match(line):
            found[match[2]] = match[1].lower()
        elif match := SUMS_BSD.match(line):
            found[match[1]] = match[2].lower()
    return found


def version_key(version):
    return [int(part) for part in re.findall(r"\d+", version)]


def pick_file(hashes, pattern):
    """The highest version matching the pattern, as (file, version, hash)."""
    matches = []
    for name, sha256 in hashes.items():
        if match := re.fullmatch(pattern, name):
            matches.append((version_key(match[1]), name, match[1], sha256))
    if not matches:
        raise SourceError(f"no file matches {pattern}")
    _, name, version, sha256 = max(matches)
    return name, version, sha256


def find_in_listing(listing, pattern):
    names = sorted({match[0] for match in re.finditer(pattern, listing)})
    if not names:
        raise SourceError(f"no hash file matches {pattern}")
    return names[-1]


def resolve(source, fetch=fetch_text, stat=head):
    """Turn a source into a catalog image, reading only official files."""
    image = {
        key: source[key]
        for key in ("id", "family", "name", "channel", "arch", "homepage", "edition")
        if key in source
    }
    if "official_page" in source:
        image.update(
            version=source["version"],
            released=source["released"],
            official_page=source["official_page"],
        )
        return image
    base = source["base"]
    if not base.startswith("https://"):
        raise SourceError(f"{base}: not https")
    sums = source.get("sums") or find_in_listing(fetch(base), source["sums_pattern"])
    name, version, sha256 = pick_file(parse_sums(fetch(base + sums)), source["pattern"])
    url = base + name
    size, released = stat(url)
    image.update(version=version, released=released, url=url, sha256=sha256, size_bytes=size)
    return image


def previous_catalog(url):
    if not url:
        return {}
    try:
        return json.loads(fetch_text(url))
    except (OSError, ValueError) as error:
        print(f"no previous catalog: {error}", file=sys.stderr)
        return {}


def build(sources, families, icon_hashes, previous, now):
    """The catalog and the sources that failed."""
    kept = {image["id"]: image for image in previous.get("images", [])}
    images, failures = [], []
    for source in sources:
        try:
            images.append(resolve(source))
        except (SourceError, OSError, ValueError) as error:
            failures.append({"id": source["id"], "error": str(error)})
            if source["id"] in kept:
                images.append(kept[source["id"]])
    catalog = {
        "schema_version": SCHEMA_VERSION,
        # The clock only moves forward between runs: a mirror cannot pass off an older file.
        "sequence": now,
        "expires_unix": now + LIFETIME_SECONDS,
        "families": [
            {
                "id": family["id"],
                "name": family["name"],
                "icon": "icons/" + family["icon"],
                "icon_sha256": icon_hashes[family["icon"]],
            }
            for family in families
        ],
        "images": images,
    }
    return catalog, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("site/v1"))
    parser.add_argument("--previous", help="address of the catalog published so far")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    sources = json.loads((root / "sources/images.json").read_text())
    families = json.loads((root / "sources/families.json").read_text())
    icons = args.output / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for family in families:
        source = root / "sources/icons" / family["icon"]
        hashes[family["icon"]] = hashlib.sha256(source.read_bytes()).hexdigest()
        shutil.copyfile(source, icons / family["icon"])

    catalog, failures = build(
        sources, families, hashes, previous_catalog(args.previous), int(time.time())
    )
    if not any("url" in image for image in catalog["images"]):
        print("no image could be read: nothing to publish", file=sys.stderr)
        return 1
    (args.output / "catalog.json").write_text(json.dumps(catalog, indent=1) + "\n")
    (args.output.parent / "failures.json").write_text(json.dumps(failures, indent=1) + "\n")
    for failure in failures:
        print(f"FAILED {failure['id']}: {failure['error']}", file=sys.stderr)
    print(f"{len(catalog['images'])} images, {len(failures)} failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
