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
import subprocess
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

import grub

SCHEMA_VERSION = 2
FAMILY_OPTIONAL = ("parent", "homepage", "icon_source", "uses", "ram_mb", "ram_source")
IMAGE_KEPT = (
    "id", "family", "name", "channel", "arch", "homepage", "edition", "note", "usb_boot",
    "desktop", "kind", "media", "ram_mb", "ram_source",
)
# Closed lists: clients translate these names, so a typo would show as a raw identifier.
USES = (
    "desktop", "beginner", "gaming", "light", "creative", "advanced", "server", "homelab",
    "privacy", "rescue", "media", "boards", "windows", "other",
)
DESKTOPS = (
    "gnome", "kde", "xfce", "cinnamon", "mate", "lxqt", "lxde", "budgie", "cosmic", "pantheon",
    "deepin", "enlightenment", "i3", "sway", "pixel", "unity", "ukui", "none", "choice",
)
KINDS = ("live", "installer", "netinst", "preinstalled", "rescue")
MEDIA = ("usb", "sd", "optical")
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


def reachable(url):
    with request(url) as response:
        if not response.geturl().startswith("https://"):
            raise SourceError(f"{url}: redirected out of https")


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


def resolve(source, fetch=fetch_text, stat=head, previous=None, compute=hash_url, reach=reachable):
    image = {
        key: source[key]
        for key in IMAGE_KEPT
        if key in source
    }
    if "official_page" in source:
        reach(source["official_page"])
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


DATED_BASE = re.compile(r"^(https://.+/)(\d+(?:\.\d+)+)/(.*)$")
LISTED_VERSION = re.compile(r'href="(\d+(?:\.\d+)+)/"')


def newer_releases(sources, fetch=fetch_text):
    groups = {}
    for source in sources:
        match = DATED_BASE.match(source.get("base", ""))
        if not match or "sums" not in source:
            continue
        parent, version, rest = match.groups()
        group = groups.setdefault((parent, rest, source["sums"]), {"family": source["family"], "versions": []})
        group["versions"].append(version)
    series = lambda version: version_key(version)[:2]
    newest = {}
    for group in groups.values():
        for version in group["versions"]:
            newest[group["family"]] = max(newest.get(group["family"], []), series(version))
    found, listings = [], {}
    for (parent, rest, sums), group in groups.items():
        try:
            if parent not in listings:
                listings[parent] = set(LISTED_VERSION.findall(fetch(parent)))
            listed = listings[parent]
        except (OSError, ValueError) as error:
            print(f"{parent}: release listing unreadable: {error}", file=sys.stderr)
            continue
        for version in sorted(listed, key=version_key):
            if series(version) <= newest[group["family"]]:
                continue
            where = f"{parent}{version}/{rest}"
            try:
                fetch(where + sums)
            except (OSError, ValueError):
                continue
            found.append({"family": group["family"], "version": version, "where": where})
    return found


def previous_catalog(urls):
    """The first that answers."""
    for url in urls or ():
        try:
            return json.loads(fetch_text(url))
        except (OSError, ValueError) as error:
            print(f"no previous catalog at {url}: {error}", file=sys.stderr)
    return {}


def build(sources, families, icon_hashes, previous, now, compute=hash_url):
    kept = {image["id"]: image for image in previous.get("images", [])}
    images, failures = [], []
    today = time.strftime("%Y-%m-%d", time.gmtime(now))
    for source in sources:
        try:
            image = resolve(source, previous=kept.get(source["id"]), compute=compute)
            images.append(dict(image, status="online", checked=today))
        except (SourceError, OSError, ValueError) as error:
            failures.append({"id": source["id"], "error": str(error)})
            if source["id"] in kept:
                images.append(dict(kept[source["id"]], status="stale"))
    catalog = {
        "schema_version": SCHEMA_VERSION,
        # The clock only moves forward between runs: a mirror cannot pass off an older file.
        "sequence": now,
        "expires_unix": now + LIFETIME_SECONDS,
        "families": [family_entry(family, icon_hashes) for family in families],
        "images": images,
    }
    return catalog, failures


def family_entry(family, icon_hashes):
    entry = {
        "id": family["id"],
        "name": family["name"],
        "category": family["category"],
        "icon": "icons/" + family["icon"],
        "icon_sha256": icon_hashes[family["icon"]],
    }
    entry.update({key: family[key] for key in FAMILY_OPTIONAL if key in family})
    return entry


def newer_section(updates):
    if not updates:
        return ""
    e = html.escape
    items = "\n".join(
        f'<li>{e(u["family"])} {e(u["version"])}: <a href="{e(u["where"])}">{e(u["where"])}</a></li>'
        for u in updates
    )
    return f"<section>\n<h2>Newer releases not yet in the catalog</h2>\n<ul>\n{items}\n</ul>\n</section>"


def status(image):
    state, checked = image.get("status", "unknown"), image.get("checked")
    # Checked at publication, whose date heads the page: repeating it on every row only widens the table.
    if state == "online":
        return "online"
    return f"{state} since {checked}" if checked else state


def index_page(catalog, updates=()):
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
                f'<td class="file"><a href="{e(link)}" title="{e(label)}">{e(label)}</a></td><td>{e(status(image))}</td></tr>'
            )
        sections.append(
            f"<section>\n<h2>{e(category)}</h2>\n<table>\n<thead><tr><th>System</th><th>Version</th>"
            "<th>Edition</th><th>Architecture</th><th>Size</th><th>Download</th><th>Link</th></tr></thead>\n"
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
<dt>Catalog</dt><dd><a href="/v2/catalog.json">/v2/catalog.json</a></dd>
<dt>Updates</dt><dd><a href="/v2/updates.json">/v2/updates.json</a> (newer releases found at publishers, not yet in the catalog)</dd>
<dt>Signature</dt><dd><a href="/v2/catalog.json.sig">/v2/catalog.json.sig</a> (Ed25519, over the exact bytes)</dd>
<dt>Keys</dt><dd><a href="https://github.com/FantasmaGlad/bonk-catalog/tree/main/keys">keys/</a></dd>
<dt>Published</dt><dd>{day(catalog["sequence"])}</dd>
<dt>Expires</dt><dd>{day(catalog["expires_unix"])}</dd>
<dt>Content</dt><dd>{len(catalog["images"])} images, {len(catalog["families"])} systems</dd>
<dt>Links</dt><dd>{sum(i.get("status") == "online" for i in catalog["images"])} online, {sum(i.get("status") == "stale" for i in catalog["images"])} stale (checked at each daily run)</dd>
</dl>
</section>
{newer_section(updates)}
{chr(10).join(sections)}
</main>
<footer>
<p><a href="https://bonk-os.app">bonk-os.app</a> · <a href="https://bonk-os.app/images/">Systems</a> · <a href="https://packages.bonk-os.app">packages</a> · <a href="https://security.bonk-os.app">security</a> · <a href="https://github.com/FantasmaGlad/bonk-catalog">GitHub</a></p>
</footer>
</body>
</html>
"""


def memory_errors(where, entry):
    if "ram_mb" not in entry and "ram_source" not in entry:
        return []
    ram, source = entry.get("ram_mb"), entry.get("ram_source", "")
    if not isinstance(ram, int) or ram <= 0 or not source.startswith("https://"):
        return [f"{where}: ram_mb needs a positive whole number and an https ram_source"]
    return []


def source_errors(families, sources):
    """Mistakes a reviewer would miss: unknown names in the closed lists, memory without its source."""
    errors, known = [], {family["id"] for family in families}
    for family in families:
        where = f"family {family['id']}"
        errors += [f"{where}: unknown use {use}" for use in family.get("uses", []) if use not in USES]
        errors += memory_errors(where, family)
    for source in sources:
        where = f"image {source['id']}"
        if source["family"] not in known:
            errors.append(f"{where}: unknown family {source['family']}")
        for key, allowed in (("desktop", DESKTOPS), ("kind", KINDS), ("media", MEDIA)):
            if key in source and source[key] not in allowed:
                errors.append(f"{where}: unknown {key} {source[key]}")
        errors += memory_errors(where, source)
    return errors


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
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    sources = json.loads((root / "sources/images.json").read_text())
    families = json.loads((root / "sources/families.json").read_text())
    errors = source_errors(families, sources)
    for error in errors:
        print(f"INVALID {error}", file=sys.stderr)
    if errors:
        return 1
    hashes = copy_icons(root, families, args.output)
    previous = previous_catalog(args.previous)
    catalog, failures = build(
        sources, families, hashes, previous, int(time.time()),
        (lambda *_: "0" * 64) if args.no_compute else hash_url,
    )
    if args.grub:
        add_grub(catalog, args.output, previous, failures)
    if not any("url" in image for image in catalog["images"]):
        print("no image could be read: nothing to publish", file=sys.stderr)
        return 1
    write_site(root, args.output, catalog, sources, failures)
    for failure in failures:
        print(f"FAILED {failure['id']}: {failure['error']}", file=sys.stderr)
    print(f"{len(catalog['images'])} images, {len(failures)} failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
