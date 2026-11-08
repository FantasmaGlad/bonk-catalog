#!/usr/bin/env python3
"""Build the signed image catalog from the official sources.

Each source names a publisher's directory and its published hash file: links and hashes are read
from there, never typed by hand. A source that fails keeps its last published entry and is
reported, so one publisher's outage neither removes an image nor publishes an unverified link.
"""

import argparse
import hashlib
import html
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
    pass


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
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if match := SUMS_LINE.match(line):
            found[match[2].removeprefix("./")] = match[1].lower()
        elif match := SUMS_BSD.match(line):
            found[match[1]] = match[2].lower()
    return found


def version_key(version):
    return [int(part) for part in re.findall(r"\d+", version)]


def pick_file(hashes, pattern):
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


def resolve_computed(source, image, previous, compute):
    """The publisher lists no SHA-256 (Microsoft evaluation images, TempleOS): it is computed once from
    its own file, and kept as long as the address and the size do not change."""
    if "direct" in source:
        link = source["direct"]
    else:
        link = f"https://go.microsoft.com/fwlink/?linkid={source['fwlink']}&clcid=0x409&culture=en-us&country=us"
    with request(link, "HEAD") as response:
        url = response.geturl()
        size = int(response.headers.get("Content-Length") or 0)
        modified = response.headers.get("Last-Modified")
    trusted_host = "fwlink" not in source or ".microsoft.com/" in url
    if not url.startswith("https://") or not trusted_host or not url.lower().endswith(".iso"):
        raise SourceError(f"{link}: leads to {url}")
    if size <= 0 or not modified:
        raise SourceError(f"{url}: no size or date")
    known = previous.get("url") == url and previous.get("size_bytes") == size
    sha256 = previous["sha256"] if known else compute(url)
    image.update(
        version=source["version"],
        released=parsedate_to_datetime(modified).date().isoformat(),
        url=url,
        sha256=sha256,
        size_bytes=size,
    )
    return image


def hash_url(url, algorithm=None, expected=None):
    """The file's SHA-256, while checking the weaker or longer hash its publisher lists, if any."""
    digest = hashlib.sha256()
    check = hashlib.new(algorithm) if algorithm else None
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=TIMEOUT_SECONDS
    ) as response:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            if check:
                check.update(chunk)
    if check and check.hexdigest() != expected:
        raise SourceError(f"{url}: does not match the publisher's {algorithm}")
    return digest.hexdigest()


def first_hash(text, length):
    match = re.search(rf"\b[0-9a-fA-F]{{{length}}}\b", text)
    if not match:
        raise SourceError("no hash in the publisher's file")
    return match[0].lower()


def resolve_read(source, image, previous, fetch, stat, compute):
    """Publishers without a hash list for a directory: the address comes from a page, a JSON answer or
    a redirect to the latest file, and the hash from that answer or from a file next to the image."""
    if "latest" in source:
        with request(source["latest"], "HEAD") as response:
            text = response.geturl()
    else:
        text = fetch(source["page"])
    match = re.search(source["pattern"], text, re.DOTALL)
    if not match:
        raise SourceError(f"no address matches {source['pattern']}")
    groups = match.groupdict()
    url = source.get("url_prefix", "") + groups.get("url", match[0])
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("https://"):
        raise SourceError(f"{url}: not https")
    size = groups.get("size")
    released = None
    if groups.get("sha256"):
        sha256 = groups["sha256"].lower()
    elif "hash_suffix" in source:
        sha256 = first_hash(fetch(url + source["hash_suffix"]), 64)
    elif previous.get("url") == url:
        sha256 = previous["sha256"]
    elif "verify_suffix" in source:
        algorithm = source["verify_suffix"].strip(".").removesuffix("sum")
        lengths = {"sha1": 40, "sha512": 128}
        expected = first_hash(fetch(url + source["verify_suffix"]), lengths[algorithm])
        sha256 = compute(url, algorithm, expected)
    else:
        sha256 = compute(url)
    size_bytes, released = stat(url)
    image.update(
        version=source.get("version", groups.get("version")),
        released=released,
        url=url,
        sha256=sha256,
        size_bytes=int(size) if size else size_bytes,
    )
    return image


def resolve(source, fetch=fetch_text, stat=head, previous=None, compute=hash_url):
    image = {
        key: source[key]
        for key in ("id", "family", "name", "channel", "arch", "homepage", "edition", "note", "usb_boot")
        if key in source
    }
    if "official_page" in source:
        image.update(
            version=source["version"],
            released=source["released"],
            official_page=source["official_page"],
        )
        return image
    if "fwlink" in source or "direct" in source:
        return resolve_computed(source, image, previous or {}, compute)
    if "page" in source or "latest" in source:
        return resolve_read(source, image, previous or {}, fetch, stat, compute)
    base = source["base"]
    if not base.startswith("https://"):
        raise SourceError(f"{base}: not https")
    sums = source.get("sums") or find_in_listing(
        fetch(source.get("listing", base)), source["sums_pattern"]
    )
    name, version, sha256 = pick_file(parse_sums(fetch(base + sums)), source["pattern"])
    url = base + name
    size, released = stat(url)
    version = source.get("version", version)
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


def build(sources, families, icon_hashes, previous, now, compute=hash_url):
    kept = {image["id"]: image for image in previous.get("images", [])}
    images, failures = [], []
    for source in sources:
        try:
            images.append(resolve(source, previous=kept.get(source["id"]), compute=compute))
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
                "category": family["category"],
                "icon": "icons/" + family["icon"],
                "icon_sha256": icon_hashes[family["icon"]],
            }
            for family in families
        ],
        "images": images,
    }
    return catalog, failures


def index_page(catalog):
    """A readable view of the catalog, for people: Bonk itself only reads the signed JSON."""
    e = html.escape
    day = lambda unix: time.strftime("%Y-%m-%d", time.gmtime(unix))
    names = {family["id"]: family for family in catalog["families"]}
    sections = []
    for category in dict.fromkeys(family["category"] for family in catalog["families"]):
        rows = []
        for image in catalog["images"]:
            family = names.get(image["family"])
            if family is None or family["category"] != category:
                continue
            link = image.get("url") or image.get("official_page", "")
            label = link.rsplit("/", 1)[-1] or link
            size = f"{image['size_bytes'] / 1e9:.1f} GB" if "size_bytes" in image else ""
            rows.append(
                f'<tr><td><a href="{e(image["homepage"])}">{e(image["name"])}</a></td>'
                f"<td>{e(image['version'])}</td><td>{e(image.get('edition', ''))}</td>"
                f"<td>{e(image['arch'])}</td><td>{size}</td>"
                f'<td><a href="{e(link)}">{e(label)}</a></td></tr>'
            )
        sections.append(
            f"<section>\n<h2>{e(category)}</h2>\n<table>\n<thead><tr><th>System</th><th>Version</th>"
            "<th>Edition</th><th>Architecture</th><th>Size</th><th>Download</th></tr></thead>\n"
            "<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>\n</section>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bonk image catalog</title>
<meta name="description" content="Signed list of the bootable images Bonk can write, with each publisher's link.">
<link rel="canonical" href="https://catalog.bonk-os.app/">
<link rel="icon" href="/logo.png">
<link rel="stylesheet" href="/site.css">
</head>
<body>
<main>
<header>
<h1>Bonk image catalog</h1>
<p class="lead">Signed list of the bootable images Bonk can write. Every link points to the publisher.</p>
</header>
<section>
<h2>API</h2>
<dl>
<dt>Catalog</dt><dd><a href="/v1/catalog.json">/v1/catalog.json</a></dd>
<dt>Signature</dt><dd><a href="/v1/catalog.json.sig">/v1/catalog.json.sig</a> (Ed25519, over the exact bytes)</dd>
<dt>Keys</dt><dd><a href="https://github.com/FantasmaGlad/bonk-catalog/tree/main/keys">keys/</a></dd>
<dt>Published</dt><dd>{day(catalog["sequence"])}</dd>
<dt>Expires</dt><dd>{day(catalog["expires_unix"])}</dd>
<dt>Content</dt><dd>{len(catalog["images"])} images, {len(catalog["families"])} systems</dd>
</dl>
</section>
{chr(10).join(sections)}
</main>
<footer>
<p><a href="https://bonk-os.app">bonk-os.app</a> · <a href="https://bonk-os.app/images/">Systems</a> · <a href="https://packages.bonk-os.app">packages</a> · <a href="https://security.bonk-os.app">security</a> · <a href="https://github.com/FantasmaGlad/bonk-catalog">GitHub</a></p>
</footer>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("site/v1"))
    parser.add_argument("--previous", help="address of the catalog published so far")
    parser.add_argument(
        "--no-compute",
        action="store_true",
        help="check links only: do not download images to compute a missing hash",
    )
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
        sources, families, hashes, previous_catalog(args.previous), int(time.time()),
        (lambda *_: "0" * 64) if args.no_compute else hash_url,
    )
    if not any("url" in image for image in catalog["images"]):
        print("no image could be read: nothing to publish", file=sys.stderr)
        return 1
    (args.output / "catalog.json").write_text(json.dumps(catalog, indent=1) + "\n")
    page = index_page(catalog)
    for target in (args.output.parent, args.output):
        (target / "index.html").write_text(page)
    for asset in ("site.css", "logo.png"):
        shutil.copyfile(root / "page" / asset, args.output.parent / asset)
    (args.output.parent / "failures.json").write_text(json.dumps(failures, indent=1) + "\n")
    for failure in failures:
        print(f"FAILED {failure['id']}: {failure['error']}", file=sys.stderr)
    print(f"{len(catalog['images'])} images, {len(failures)} failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
