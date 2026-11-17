import atexit
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from catalogue.reseau import SourceError, fetch_bytes, fetch_text


PINNED_KEYS = Path(__file__).resolve().parents[2] / "keys" / "editeurs"


FEDORA_SECURITY = "https://fedoraproject.org/security/"


FEDORA_KEYS = "https://fedoraproject.org/fedora.gpg"


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
