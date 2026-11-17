#!/usr/bin/env python3
"""Build the signed image catalog from the official sources.

Each source names a publisher's directory and its published hash file: links and hashes are read
from there, never typed by hand. A source that fails keeps its last published entry and is
reported, so one publisher's outage neither removes an image nor publishes an unverified link.
"""

import argparse
import atexit
import calendar
import concurrent.futures
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

import grub
from conformite import catalog_errors

SCHEMA_VERSION = 2
FAMILY_OPTIONAL = ("parent", "homepage", "icon_source", "uses", "ram_mb", "ram_source")
IMAGE_KEPT = (
    "id", "family", "name", "channel", "arch", "homepage", "edition", "note", "usb_boot",
    "desktop", "kind", "media", "ram_mb", "ram_source",
)
# Closed lists: clients translate these names, so a typo would show as a raw identifier.
DESKTOPS = (
    "gnome", "kde", "xfce", "cinnamon", "mate", "lxqt", "lxde", "budgie", "cosmic", "pantheon",
    "deepin", "enlightenment", "i3", "sway", "pixel", "unity", "ukui", "none", "choice",
)
KINDS = ("live", "installer", "netinst", "preinstalled", "rescue")
MEDIA = ("usb", "sd", "optical")
LIFETIME_SECONDS = 14 * 24 * 3600
# Past a month without one good read, the address has moved: the entry leaves rather than show a dead link.
STALE_DAYS = 30
PINNED_KEYS = Path(__file__).resolve().parents[1] / "keys" / "editeurs"
FEDORA_SECURITY = "https://fedoraproject.org/security/"
FEDORA_KEYS = "https://fedoraproject.org/fedora.gpg"
TIMEOUT_SECONDS = 60
WORKERS = 8
USER_AGENT = "bonk-catalog"
PUBLIC_KEYS = [Path(__file__).resolve().parent.parent / "keys" / name for name in ("courante.pub.pem", "secours.pub.pem")]
TEXT_LIMIT = 4 << 20
SUMS_LINE = re.compile(r"^([0-9a-fA-F]{128}|[0-9a-fA-F]{64})\s+\*?(\S+)$")
SUMS_BSD = re.compile(r"^SHA256 \((.+)\) = ([0-9a-fA-F]{64})$")


# Publisher servers drop requests now and then: a network error is tried once more after this pause.
RETRY_PAUSE = 20


class SourceError(Exception):
    pass


def request(url, method="GET"):
    headers = {"User-Agent": USER_AGENT}
    # Without a token GitHub allows 60 API calls an hour per address, shared by every runner on it.
    token = os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS)


def fetch_bytes(url):
    with request(url) as response:
        return response.read(TEXT_LIMIT)


def fetch_text(url):
    return fetch_bytes(url).decode("utf-8", "replace")


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
    names = {match[0] for match in re.finditer(pattern, listing)}
    if not names:
        raise SourceError(f"no hash file matches {pattern}")
    return max(names, key=lambda name: (version_key(name), name))


def newest_folder(source, fetch):
    """The newest folder of the publisher's listing that holds the hash file: a dated folder may receive
    other images first (OpenIndiana opens its cloud images before its installers)."""
    parent = source["folders"]
    found = re.finditer(rf'href="(?:\./)?({source["folder_pattern"]})/"', fetch(parent))
    for name in sorted({match[1] for match in found}, key=lambda name: (version_key(name), name), reverse=True):
        base = f"{parent}{name}/"
        try:
            return base, find_in_listing(fetch(base), source["sums_pattern"])
        except SourceError:
            continue
    raise SourceError(f"{parent}: no folder holds a file matching {source['sums_pattern']}")


def verify_signed(data, signature, signer, keys=PINNED_KEYS):
    """The bytes the publisher's key certifies: `data` itself for a detached `signature`, the signed part of
    `data` when it is signed in clear (`signature` is None); text outside that part is never returned. The key
    is pinned in `keys/<fingerprint>.asc`; a signing subkey of that key is accepted."""
    key = keys / f"{signer}.asc"
    if not key.is_file() and (FETCHED_KEYS / key.name).is_file():
        key = FETCHED_KEYS / key.name
    with tempfile.TemporaryDirectory() as work:
        folder = Path(work)
        (folder / "sums").write_bytes(data)
        dearmored = subprocess.run(
            ["gpg", "--batch", "--dearmor", "--output", str(folder / "keyring"), str(key)], capture_output=True
        )
        if dearmored.returncode != 0:
            raise SourceError(f"{key.name}: pinned key unreadable")
        command = ["gpgv", "--status-fd", "1", "--keyring", str(folder / "keyring")]
        if signature is None:
            command += ["--output", str(folder / "signed"), str(folder / "sums")]
        else:
            (folder / "sums.sig").write_bytes(signature)
            command += [str(folder / "sums.sig"), str(folder / "sums")]
        status = subprocess.run(command, capture_output=True)
        # User ids in the status lines need not be UTF-8; VALIDSIG <signing key> ... <primary key> is ASCII, and
        # either key may be the pinned one.
        valid = any(
            line.split()[2] == signer or line.split()[-1] == signer
            for line in status.stdout.decode("ascii", "replace").splitlines()
            if line.startswith("[GNUPG:] VALIDSIG ")
        )
        if status.returncode != 0 or not valid:
            raise SourceError(f"hash file not signed by {signer}")
        return (folder / "signed").read_bytes() if signature is None else data


# Keys read from the publisher during this run; never written to the repository.
FETCHED_KEYS = Path(tempfile.mkdtemp(prefix="bonk-cles-"))
atexit.register(shutil.rmtree, FETCHED_KEYS, ignore_errors=True)


def key_owners(keys):
    """User id of each key in `keys`, by fingerprint."""
    owners = {}
    for key in sorted(keys.glob("*.asc")):
        listing = subprocess.run(["gpg", "--show-keys", "--with-colons", str(key)], capture_output=True)
        fingerprint = None
        for line in listing.stdout.decode("utf-8", "replace").splitlines():
            fields = line.split(":")
            if fields[0] == "fpr" and fingerprint is None:
                fingerprint = fields[9]
            elif fields[0] == "uid" and fingerprint:
                owners.setdefault(fingerprint, []).append(fields[9])
    return owners


def fedora_signer(release, fetch_text=fetch_text, fetch_raw=fetch_bytes, pinned=PINNED_KEYS):
    """Fedora signs each release with its own key. A pinned key named `Fedora (N)` is used first; otherwise the
    fingerprint printed under `Fedora N` on fedoraproject.org/security selects, in fedoraproject.org/fedora.gpg,
    the key that carries both that fingerprint and that user id."""
    owner = f"Fedora ({release}) <fedora-{release}-primary@fedoraproject.org>"
    for fingerprint, uids in key_owners(pinned).items():
        if owner in uids:
            return fingerprint
    page = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fetch_text(FEDORA_SECURITY)))
    found = re.search(rf"\bFedora {re.escape(release)} ID: \S+ \S+ Fingerprint: ((?:[0-9A-F]{{4}} ?){{10}})", page)
    if not found:
        raise SourceError(f"{FEDORA_SECURITY}: no fingerprint for Fedora {release}")
    fingerprint = found[1].replace(" ", "")
    with tempfile.TemporaryDirectory() as work:
        home = Path(work)
        home.chmod(0o700)
        (home / "keys").write_bytes(fetch_raw(FEDORA_KEYS))
        gpg = ["gpg", "--batch", "--homedir", str(home)]
        subprocess.run(gpg + ["--import", str(home / "keys")], capture_output=True)
        exported = subprocess.run(gpg + ["--armor", "--export", fingerprint], capture_output=True).stdout
    candidate = FETCHED_KEYS / f"{fingerprint}.asc"
    candidate.write_bytes(exported)
    if owner not in key_owners(FETCHED_KEYS).get(fingerprint, []):
        candidate.unlink()
        raise SourceError(f"{FEDORA_KEYS}: no key {fingerprint} for {owner}")
    return fingerprint


def signer_of(signature):
    if "signer" in signature:
        return signature["signer"]
    if signature.get("signer_from") == "fedora":
        return fedora_signer(signature["release"])
    raise SourceError("signature without a signer")


def signed_text(source, address, fetch_raw, verify):
    """The hash file at `address`, read only once its signature holds. `clearsigned`: the file carries its
    own signature, or a signed copy is published at that address (FreeBSD, on its own website)."""
    signature = source["signature"]
    if signature.get("clearsigned") is True:
        return verify(fetch_raw(address), None, signer_of(signature)).decode()
    if "clearsigned" in signature:
        return verify(fetch_raw(signature["clearsigned"]), None, signer_of(signature)).decode()
    data = fetch_raw(address)
    return verify(data, fetch_raw(address + signature["suffix"]), signer_of(signature)).decode()


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
    with request(url) as response:
        if not response.geturl().startswith("https://"):
            raise SourceError(f"{url}: redirected out of https")
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            if check:
                check.update(chunk)
    if check and check.hexdigest() != expected:
        raise SourceError(f"{url}: does not match the publisher's {algorithm}")
    return digest.hexdigest()


def first_hash(text, length, name=None):
    """The hash on the line naming the file when the publisher lists several, else the first one."""
    lines = [line for line in text.splitlines() if name and re.search(rf"[\s(/*]{re.escape(name)}(?:[\s)]|$)", line)]
    found = re.findall(rf"\b[0-9a-fA-F]{{{length}}}\b", "\n".join(lines) or text)
    if not found or (not lines and len(set(found)) > 1):
        raise SourceError(f"no hash for {name} in the publisher's file")
    return found[0].lower()


def resolve_read(source, image, previous, fetch, stat, compute, fetch_raw=fetch_bytes, verify=verify_signed):
    """Publishers without a hash list for a directory: the address comes from a page, a JSON answer or
    a redirect to the latest file, and the hash from that answer or from a file next to the image."""
    if "latest" in source:
        with request(source["latest"], "HEAD") as response:
            text = response.geturl()
    else:
        text = fetch(source["page"])
    matches = list(re.finditer(source["pattern"], text, re.DOTALL))
    if not matches:
        raise SourceError(f"no address matches {source['pattern']}")
    # A listing that keeps every release names the newest anywhere: `newest` picks it by version.
    match = max(matches, key=lambda m: version_key(m["version"])) if source.get("newest") else matches[0]
    groups = match.groupdict()
    # A share that names its files by number (kDrive) gives the address and the hash file from the listing.
    if "url_template" in source:
        url = source["url_template"].format(**groups)
    else:
        url = source.get("url_prefix", "") + groups.get("url", match[0])
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("https://"):
        raise SourceError(f"{url}: not https")
    size_bytes, released = stat(url)
    if groups.get("size"):
        size_bytes = int(groups["size"])
    # Some publishers name the hash file after the image without its extension, or keep it in another folder.
    hash_base = re.sub(*source["hash_replace"], url) if "hash_replace" in source else url
    if groups.get("sha256"):
        sha256 = groups["sha256"].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise SourceError(f"{url}: published SHA-256 is not 64 hexadecimal digits")
    elif "hash_template" in source:
        sha256 = first_hash(fetch(source["hash_template"].format(**groups)), 64, groups.get("file"))
    elif "hash_suffix" in source:
        sha256 = first_hash(fetch(hash_base + source["hash_suffix"]), 64, url.rsplit("/", 1)[-1])
    elif previous.get("url") == url and previous.get("size_bytes") == size_bytes:
        sha256 = previous["sha256"]
    elif "verify_suffix" in source:
        named = re.search(r"sha(?:1|512)|md5", source["verify_suffix"].lower())
        if not named:
            raise SourceError(f"{source['verify_suffix']}: no known hash algorithm in the name")
        algorithm = named[0]
        lengths = {"md5": 32, "sha1": 40, "sha512": 128}
        address = hash_base + source["verify_suffix"]
        text = signed_text(source, address, fetch_raw, verify) if "signature" in source else fetch(address)
        expected = first_hash(text, lengths[algorithm], url.rsplit("/", 1)[-1])
        sha256 = compute(url, algorithm, expected)
    else:
        sha256 = compute(url)
    image.update(
        version=source.get("version", groups.get("version")),
        released=released,
        url=url,
        sha256=sha256,
        size_bytes=size_bytes,
    )
    return image


def resolve(
    source, fetch=fetch_text, stat=head, previous=None, compute=hash_url, reach=reachable,
    fetch_raw=fetch_bytes, verify=verify_signed,
):
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
        return resolve_read(source, image, previous or {}, fetch, stat, compute, fetch_raw, verify)
    if "folders" in source:
        base, sums = newest_folder(source, fetch)
    else:
        base = source["base"]
    if not base.startswith("https://"):
        raise SourceError(f"{base}: not https")
    if "folders" not in source:
        sums = source.get("sums") or find_in_listing(
            fetch(source.get("listing", base)), source["sums_pattern"]
        )
    if "signature" in source:
        text = signed_text(source, base + sums, fetch_raw, verify)
    else:
        text = fetch(base + sums)
    name, version, published = pick_file(parse_sums(text), source["pattern"])
    url = base + name
    size, released = stat(url)
    version = source.get("version", version)
    previous = previous or {}
    if len(published) == 64:
        sha256 = published
    elif previous.get("url") == url and previous.get("size_bytes") == size:
        sha256 = previous["sha256"]
    else:
        # Only a SHA-512 list (Kicksecure): the SHA-256 is computed while checking it.
        sha256 = compute(url, "sha512", published)
    image.update(version=version, released=released, url=url, sha256=sha256, size_bytes=size)
    return image


DATED_BASE = re.compile(r"^(https://.+/)(\d+(?:\.\d+)*)/(.*)$")
LISTED_VERSION = re.compile(r'href="(?:\./)?(\d+(?:\.\d+)*)/"')


def with_release(text, old, new):
    """The release number swapped where it stands alone: `8` in `x86_64` is no release."""
    return re.sub(rf"(?<![\w.]){re.escape(old)}(?![\d])", lambda _: new, text)


def published(source, version, old, fetch):
    """The hash file of another release, where this source would read it: a fixed name, or the one a
    listing gives when the file name carries the release (Fedora, FreeBSD)."""
    where = source["base"].replace(f"/{old}/", f"/{version}/")
    if "sums" in source:
        fetch(where + source["sums"])
    else:
        listing = source.get("listing", source["base"]).replace(f"/{old}/", f"/{version}/")
        find_in_listing(fetch(listing), with_release(source["sums_pattern"], old, version))
    return where


def newer_releases(sources, fetch=fetch_text):
    groups = {}
    for source in sources:
        match = DATED_BASE.match(source.get("base", ""))
        # An archive keeps an old release on purpose: newer ones are the current channels' business.
        if not match or source.get("channel") == "archive" or not ("sums" in source or "sums_pattern" in source):
            continue
        parent, version, rest = match.groups()
        key = (parent, rest, source.get("sums") or with_release(source["sums_pattern"], version, "{}"))
        # The parent folder is read where the listing is readable: a redirecting host may list nothing.
        index = DATED_BASE.match(source.get("listing", "")) or match
        group = groups.setdefault(
            key, {"family": source["family"], "versions": [], "sample": (source, version), "index": index[1]}
        )
        group["versions"].append(version)
    series = lambda version: version_key(version)[:2]
    newest = {}
    for group in groups.values():
        for version in group["versions"]:
            newest[group["family"]] = max(newest.get(group["family"], []), series(version))
    found, listings = [], {}
    for group in groups.values():
        index = group["index"]
        try:
            if index not in listings:
                listings[index] = set(LISTED_VERSION.findall(fetch(index)))
            listed = listings[index]
        except (OSError, ValueError) as error:
            print(f"{index}: release listing unreadable: {error}", file=sys.stderr)
            continue
        source, old = group["sample"]
        tracked = newest[group["family"]]
        for version in sorted(listed, key=version_key):
            # A folder named by the major release already follows its point releases (AlmaLinux `10/`).
            if series(version) <= tracked or (len(tracked) == 1 and series(version)[0] == tracked[0]):
                continue
            try:
                where = published(source, version, old, fetch)
            except (OSError, ValueError, SourceError):
                continue
            found.append({"family": group["family"], "version": version, "where": where})
    return found


def signed(manifest, signature, keys):
    with tempfile.TemporaryDirectory() as folder:
        data, sig = Path(folder, "manifest"), Path(folder, "signature")
        data.write_bytes(manifest)
        sig.write_bytes(signature)
        return any(
            subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", str(key),
                 "-in", str(data), "-sigfile", str(sig)],
                capture_output=True,
            ).returncode == 0
            for key in keys
        )


def previous_catalog(urls, keys=PUBLIC_KEYS):
    """The first that answers with a valid signature: its hashes are carried into the new catalog."""
    for url in urls or ():
        try:
            manifest = fetch_bytes(url)
            if not signed(manifest, fetch_bytes(url + ".sig"), keys):
                print(f"previous catalog at {url}: bad signature, ignored", file=sys.stderr)
                continue
            return json.loads(manifest)
        except (OSError, ValueError) as error:
            print(f"no previous catalog at {url}: {error}", file=sys.stderr)
    return {}


def build(sources, families, icon_hashes, previous, now, compute=hash_url):
    kept = {image["id"]: image for image in previous.get("images", [])}
    images, failures = [], []
    today = time.strftime("%Y-%m-%d", time.gmtime(now))

    def attempt(source):
        for retry in (True, False):
            try:
                return resolve(source, previous=kept.get(source["id"]), compute=compute), None
            except OSError as error:
                if not retry:
                    return None, error
                time.sleep(RETRY_PAUSE)
            except (SourceError, ValueError) as error:
                return None, error
        raise AssertionError("unreachable")

    # Each source waits on its publisher's servers: read them side by side, in the sources' order.
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(attempt, sources))
    for source, (image, error) in zip(sources, results):
        if image is not None:
            images.append(dict(image, status="online", checked=today))
            continue
        old = kept.get(source["id"])
        age = stale_days(old, now) if old else 0
        if old and age > STALE_DAYS:
            failures.append({"id": source["id"], "error": f"{error}; removed after {age} days without a good read"})
            continue
        failures.append({"id": source["id"], "error": str(error)})
        if old:
            images.append(dict(old, status="stale"))
    catalog = {
        "schema_version": SCHEMA_VERSION,
        # The clock only moves forward between runs: a mirror cannot pass off an older file.
        "sequence": now,
        "expires_unix": now + LIFETIME_SECONDS,
        "families": [family_entry(family, icon_hashes) for family in families],
        "images": images,
    }
    return catalog, failures


def stale_days(image, now):
    """Days since the last good read; an entry without that date counts from today."""
    try:
        checked = calendar.timegm(time.strptime(image["checked"], "%Y-%m-%d"))
    except (KeyError, ValueError):
        return 0
    return int((now - checked) // 86400)


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


def size_label(size):
    """Megabytes under a gigabyte: a 17 MB image is not shown as 0.0 GB."""
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{max(1, round(size / 1e6))} MB"


def status(image):
    state, checked = image.get("status", "unknown"), image.get("checked")
    # Checked at publication, whose date heads the page: repeating it on every row only widens the table.
    if state == "online":
        return "online"
    return f"{state}, last read {checked}" if checked else state


def index_page(catalog, updates=()):
    """A readable view of the catalog, for people: Bonk itself only reads the signed JSON."""
    e = html.escape
    day = lambda unix: time.strftime("%Y-%m-%d", time.gmtime(unix))
    names = {family["id"]: family for family in catalog["families"]}
    bases = {base["id"]: base["en"] for base in catalog.get("taxonomy", {}).get("bases", [])}
    sections = []
    present = {family["category"] for family in catalog["families"]}
    for category in [base for base in bases if base in present] + sorted(present - bases.keys()):
        rows = []
        for image in catalog["images"]:
            family = names.get(image["family"])
            if family is None or family["category"] != category:
                continue
            link = image.get("url") or image.get("official_page", "")
            label = link.rsplit("/", 1)[-1] or link
            size = size_label(image["size_bytes"]) if "size_bytes" in image else ""
            rows.append(
                f'<tr><td><a href="{e(image["homepage"])}">{e(image["name"])}</a></td>'
                f"<td>{e(image['version'])}</td><td>{e(image.get('edition', ''))}</td>"
                f"<td>{e(image['arch'])}</td><td>{size}</td>"
                f'<td class="file"><a href="{e(link)}" title="{e(label)}">{e(label)}</a></td><td>{e(status(image))}</td></tr>'
            )
        sections.append(
            f"<section>\n<h2>{e(bases.get(category, category))}</h2>\n<table>\n<thead><tr><th>System</th><th>Version</th>"
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


def taxonomy_errors(taxonomy):
    """Each list in menu order, every name labelled in both languages the clients show."""
    errors = []
    for kind in ("uses", "bases"):
        terms = taxonomy.get(kind, [])
        if not terms:
            errors.append(f"taxonomy: no {kind}")
        ids = [term.get("id") for term in terms]
        errors += [f"taxonomy: {kind} {name} listed twice" for name in sorted({i for i in ids if ids.count(i) > 1})]
        for term in terms:
            if not (isinstance(term.get("id"), str) and re.fullmatch(r"[a-z]+", term["id"])):
                errors.append(f"taxonomy: {kind} {term.get('id')!r} is not a lowercase name")
            errors += [f"taxonomy: {kind} {term.get('id')} has no {lang} label"
                       for lang in ("en", "fr") if not term.get(lang)]
    return errors


def source_errors(families, sources, taxonomy):
    """Mistakes a reviewer would miss: unknown names in the closed lists, memory without its source."""
    errors, known = taxonomy_errors(taxonomy), {family["id"] for family in families}
    seen = set()
    for source in sources:
        if source["id"] in seen:
            errors.append(f"image {source['id']}: duplicate identifier")
        seen.add(source["id"])
    uses = {term.get("id") for term in taxonomy.get("uses", [])}
    bases = {term.get("id") for term in taxonomy.get("bases", [])}
    for family in families:
        where = f"family {family['id']}"
        errors += [f"{where}: unknown use {use}" for use in family.get("uses", []) if use not in uses]
        if family.get("category") not in bases:
            errors.append(f"{where}: unknown base {family.get('category')}")
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
    taxonomy = json.loads((root / "sources/taxonomy.json").read_text())
    errors = source_errors(families, sources, taxonomy)
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
    catalog["taxonomy"] = taxonomy
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
