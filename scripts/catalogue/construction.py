import calendar
import concurrent.futures
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from catalogue.controles import FAMILY_OPTIONAL
from catalogue.empreintes import hash_url
from catalogue.reseau import SourceError, fetch_bytes
from catalogue.couverture import vault_of
from catalogue.sources import resolve


SCHEMA_VERSION = 2


LIFETIME_SECONDS = 14 * 24 * 3600


# Past a month without one good read, the address has moved: the entry leaves rather than show a dead link.
STALE_DAYS = 30


WORKERS = 8


PUBLIC_KEYS = [Path(__file__).resolve().parents[2] / "keys" / name for name in ("courante.pub.pem", "secours.pub.pem")]


# Publisher servers drop requests now and then: a network error is tried once more after this pause.
RETRY_PAUSE = 20


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


def build(sources, families, icon_hashes, previous, now, compute=hash_url, proofs=None, coverage=None):
    proofs, coverage = proofs or {}, coverage or {}
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
            images.append(
                dict(image, status="online", checked=today, **boot_of(proofs, source["id"]), **vault_of(coverage, source["id"]))
            )
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


def boot_of(proofs, image_id):
    return {"boot": proofs[image_id]} if image_id in proofs else {}


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
