import hashlib
import re

from catalogue.reseau import SourceError, request


SUMS_LINE = re.compile(r"^([0-9a-fA-F]{128}|[0-9a-fA-F]{64})\s+\*?(\S+)$")


SUMS_BSD = re.compile(r"^SHA256 \((.+)\) = ([0-9a-fA-F]{64})$")


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
