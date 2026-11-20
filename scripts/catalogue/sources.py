import re
from email.utils import parsedate_to_datetime

from catalogue.empreintes import find_in_listing, first_hash, hash_url, newest_folder, parse_sums, pick_file, version_key
from catalogue.reseau import SourceError, fetch_bytes, fetch_text, head, reachable, request
from catalogue.signatures import signed_text, verify_signed


IMAGE_KEPT = (
    "id", "family", "name", "channel", "arch", "homepage", "edition", "note", "usb_boot", "vault", "hybrid",
    "desktop", "kind", "media", "ram_mb", "ram_source",
)


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


def read_address(source, fetch):
    """The image address and the named groups of the pattern that found it."""
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
    return url, groups


def read_hash(source, url, groups, known, fetch, compute, fetch_raw, verify):
    # Some publishers name the hash file after the image without its extension, or keep it in another folder.
    hash_base = re.sub(*source["hash_replace"], url) if "hash_replace" in source else url
    if groups.get("sha256"):
        sha256 = groups["sha256"].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise SourceError(f"{url}: published SHA-256 is not 64 hexadecimal digits")
        return sha256
    if "hash_template" in source:
        return first_hash(fetch(source["hash_template"].format(**groups)), 64, groups.get("file"))
    if "hash_suffix" in source:
        return first_hash(fetch(hash_base + source["hash_suffix"]), 64, url.rsplit("/", 1)[-1])
    if known:
        return known
    if "verify_suffix" in source:
        named = re.search(r"sha(?:1|512)|md5", source["verify_suffix"].lower())
        if not named:
            raise SourceError(f"{source['verify_suffix']}: no known hash algorithm in the name")
        algorithm = named[0]
        lengths = {"md5": 32, "sha1": 40, "sha512": 128}
        address = hash_base + source["verify_suffix"]
        text = signed_text(source, address, fetch_raw, verify) if "signature" in source else fetch(address)
        expected = first_hash(text, lengths[algorithm], url.rsplit("/", 1)[-1])
        return compute(url, algorithm, expected)
    return compute(url)


def file_name(url, groups):
    """A share link names its file by number: the listing's name is kept for the download."""
    named = groups.get("file")
    if not named or url.rsplit("/", 1)[-1] == named:
        return {}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", named):
        raise SourceError(f"{named}: not a plain file name")
    return {"file_name": named}


def resolve_read(source, image, previous, fetch, stat, compute, fetch_raw=fetch_bytes, verify=verify_signed):
    """Publishers without a hash list for a directory: the address comes from a page, a JSON answer or
    a redirect to the latest file, and the hash from that answer or from a file next to the image."""
    url, groups = read_address(source, fetch)
    size_bytes, released = stat(url)
    if groups.get("size"):
        size_bytes = int(groups["size"])
    unchanged = previous.get("url") == url and previous.get("size_bytes") == size_bytes
    known = previous["sha256"] if unchanged else None
    sha256 = read_hash(source, url, groups, known, fetch, compute, fetch_raw, verify)
    image.update(
        version=source.get("version", groups.get("version")),
        released=released,
        url=url,
        sha256=sha256,
        size_bytes=size_bytes,
        **file_name(url, groups),
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
