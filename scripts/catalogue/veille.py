import re
import sys

from catalogue.empreintes import find_in_listing, version_key
from catalogue.reseau import SourceError, fetch_text


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
