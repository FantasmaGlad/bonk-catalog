#!/usr/bin/env python3
"""GRUB for the Bonk vault: verified GNU archive, built EFI image, catalog entry, and the watch for new releases."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "sources/grub.json"
EARLY = ROOT / "sources/grub/depart.cfg"
BUILD = ROOT / "scripts/grub-build.sh"
KEY = ROOT / "keys/grub-signataire.asc"
PUBLISHED = "https://catalog.bonk-os.app/v2/"
ARCHIVE = re.compile(r"grub-(\d+\.\d+(?:\.\d+)?)\.tar\.xz(?![.\w])")
# Removable-media names: the firmware of each processor loads its own.
ARCHITECTURES = {"x86_64": "BOOTX64.EFI", "aarch64": "BOOTAA64.EFI"}
TIMEOUT_SECONDS = 120
LISTING_LIMIT = 4 << 20


class GrubError(Exception):
    pass


def download(url, limit=None):
    request = urllib.request.Request(url, headers={"User-Agent": "bonk-catalog"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(limit) if limit else response.read()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def build_id():
    """Changes when anything that shapes the EFI image changes: then it is built again."""
    digest = hashlib.sha256()
    for path in (SOURCE, EARLY, BUILD):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def verify_signature(archive, signature, signer):
    with tempfile.TemporaryDirectory() as work:
        keyring = Path(work) / "keyring.gpg"
        subprocess.run(["gpg", "--dearmor", "--output", str(keyring), str(KEY)], check=True)
        status = subprocess.run(
            ["gpgv", "--status-fd", "1", "--keyring", str(keyring), str(signature), str(archive)],
            capture_output=True, text=True,
        )
    dates = dict(re.findall(r"^\[GNUPG:\] VALIDSIG (\w+) (\d{4}-\d{2}-\d{2})", status.stdout, re.MULTILINE))
    if status.returncode != 0 or signer not in dates:
        raise GrubError(f"{archive.name}: no valid signature by {signer}")
    return dates[signer]


def fetch_archive(source, work):
    """The archive and its signature from the first mirror that serves both."""
    errors = []
    for mirror in source["mirrors"]:
        try:
            archive = download(mirror + source["archive"])
            signature = download(mirror + source["archive"] + ".sig")
        except OSError as error:
            errors.append(f"{mirror}: {error}")
            continue
        if sha256(archive) != source["sha256"]:
            raise GrubError(f"{mirror}{source['archive']}: hash differs from sources/grub.json")
        (work / source["archive"]).write_bytes(archive)
        (work / (source["archive"] + ".sig")).write_bytes(signature)
        verify_signature(work / source["archive"], work / (source["archive"] + ".sig"), source["signer"])
        return
    raise GrubError("no mirror served the archive: " + "; ".join(errors))


def asset(output, path):
    data = (output / path).read_bytes()
    return {"path": path, "sha256": sha256(data), "size_bytes": len(data)}


def files(entry):
    """Every published file of an entry: one EFI image per architecture, then the source and its signature."""
    return [entry["efi"][arch] for arch in sorted(entry["efi"])] + [entry["source"], entry["source_signature"]]


def reuse(previous, output, published):
    """The bytes already published, checked against the signed catalog: no rebuild, no new hash."""
    for item in files(previous):
        data = download(published + item["path"])
        if sha256(data) != item["sha256"]:
            raise GrubError(f"published {item['path']} differs from the previous catalog")
        target = output / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def prepare(output, previous, published=PUBLISHED, build=None):
    """Writes GRUB under `output`; returns its catalog entry and, when the previous one was kept, why."""
    source = json.loads(SOURCE.read_text())
    folder = f"grub/{source['version']}/"
    entry = {
        "version": source["version"],
        "released": source["released"],
        "build": build_id(),
        "efi": {arch: {"path": folder + name} for arch, name in ARCHITECTURES.items()},
        "source": {"path": folder + source["archive"]},
        "source_signature": {"path": folder + source["archive"] + ".sig"},
    }
    same = previous and all(previous.get(k) == entry[k] for k in ("version", "build"))
    if same and set(previous.get("efi", {})) == set(ARCHITECTURES):
        try:
            reuse(previous, output, published)
            return previous, None
        except (OSError, KeyError, GrubError) as error:
            print(f"previous GRUB not reused, built again: {error}", file=sys.stderr)
    (output / folder).mkdir(parents=True, exist_ok=True)
    try:
        fetch_archive(source, output / folder)
        for arch, item in entry["efi"].items():
            (build or run_build)(output / entry["source"]["path"], arch, output / item["path"])
    except (OSError, GrubError, subprocess.CalledProcessError) as error:
        # A mirror outage must not take GRUB out of the catalog: the last published one stays.
        if not previous:
            raise
        print(f"GRUB {source['version']} not built, previous one kept: {error}", file=sys.stderr)
        reuse(previous, output, published)
        return previous, f"GRUB {source['version']} not built: {error}"
    entry["efi"] = {arch: asset(output, item["path"]) for arch, item in entry["efi"].items()}
    for key in ("source", "source_signature"):
        entry[key] = asset(output, entry[key]["path"])
    return entry, None


def run_build(archive, arch, efi):
    subprocess.run(["sh", str(BUILD), str(archive), str(EARLY), arch, str(efi)], check=True)


def check(entry, output, signer):
    """What is about to be published, checked again: every hash, and the GNU signature of the archive."""
    for item in files(entry):
        if asset(output, item["path"])["sha256"] != item["sha256"]:
            raise GrubError(f"{item['path']}: hash differs from the catalog entry")
    return verify_signature(output / entry["source"]["path"], output / entry["source_signature"]["path"], signer)


def status_report(entry, output, error, today, published=PUBLISHED):
    """Contents of /grub.json: what the vault downloads, and whether it held at this run."""
    signer = json.loads(SOURCE.read_text())["signer"]
    report = {"checked": today, "signer": signer, "error": error}
    if not entry:
        return dict(report, status="missing")
    try:
        signed = check(entry, output, signer)
        state = "ok" if error is None else "stale"
    except (OSError, GrubError, subprocess.CalledProcessError) as failure:
        signed, state, report["error"] = None, "failed", str(failure)
    report.update(status=state, version=entry["version"], released=entry["released"], build=entry["build"],
                  signature_verified=signed is not None, signed=signed)
    with_url = lambda item: dict(item, url=published + item["path"])
    report["efi"] = {arch: with_url(item) for arch, item in entry["efi"].items()}
    for key in ("source", "source_signature"):
        report[key] = with_url(entry[key])
    return report


def check_published(base=PUBLISHED.removesuffix("v2/")):
    """After deployment: the public status page, then every file it names, downloaded and checked."""
    report = json.loads(download(base + "grub.json"))
    if report["status"] != "ok":
        raise GrubError(f"published GRUB status is {report['status']}: {report['error']}")
    with tempfile.TemporaryDirectory() as work:
        fetched = {}
        for item in files(report):
            fetched[item["path"]] = download(item["url"])
            if sha256(fetched[item["path"]]) != item["sha256"]:
                raise GrubError(f"{item['url']}: published bytes differ from grub.json")
        for key in ("source", "source_signature"):
            (Path(work) / key).write_bytes(fetched[report[key]["path"]])
        verify_signature(Path(work) / "source", Path(work) / "source_signature", report["signer"])
    return report["version"]


def newest(listing):
    versions = set(ARCHIVE.findall(listing))
    return max(versions, key=version_key) if versions else None


def watch(fetch=download):
    """Updates sources/grub.json when a newer, correctly signed release is listed; prints the new version."""
    source = json.loads(SOURCE.read_text())
    mirror = source["mirrors"][-1]
    found = newest(fetch(mirror, LISTING_LIMIT).decode("utf-8", "replace"))
    if not found or version_key(found) <= version_key(source["version"]):
        return None
    name = f"grub-{found}.tar.xz"
    with tempfile.TemporaryDirectory() as work:
        archive, signature = Path(work) / name, Path(work) / (name + ".sig")
        archive.write_bytes(fetch(mirror + name))
        signature.write_bytes(fetch(mirror + name + ".sig"))
        signed = verify_signature(archive, signature, source["signer"])
        source.update(version=found, archive=name, sha256=sha256(archive.read_bytes()), released=signed)
    SOURCE.write_text(json.dumps(source, indent=1) + "\n")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build GRUB into a site folder, without a catalog")
    build.add_argument("--output", type=Path, required=True)
    commands.add_parser("watch", help="update sources/grub.json to a newer signed release")
    commands.add_parser("check-published", help="download the published GRUB and check it against its status page")
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(prepare(args.output, None)[0], indent=1))
    elif args.command == "check-published":
        print(f"published GRUB {check_published()} checked")
    else:
        found = watch()
        print(found or "no newer GRUB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
