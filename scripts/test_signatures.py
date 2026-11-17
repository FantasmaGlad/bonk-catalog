#!/usr/bin/env python3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catalogue import reseau, signatures
from donnees_essai import HASH_A, HASH_B


class GpgvStatus(unittest.TestCase):
    def test_a_user_id_that_is_not_utf8_does_not_hide_a_valid_signature(self):
        signer = "F" * 40
        status = b"[GNUPG:] GOODSIG 00 Patrick \xc0 Schleizer\n[GNUPG:] VALIDSIG " + b"A" * 40 + b" 2026 0 0 4 0 1 10 00 " + signer.encode() + b"\n"
        answers = iter([
            subprocess.CompletedProcess([], 0, b"", b""),
            subprocess.CompletedProcess([], 0, status, b""),
        ])
        with tempfile.TemporaryDirectory() as keys, mock.patch.object(signatures.subprocess, "run", lambda *a, **k: next(answers)):
            Path(keys, f"{signer}.asc").write_text("key")
            self.assertEqual(signatures.verify_signed(b"sums", b"sig", signer, keys=Path(keys)), b"sums")


@unittest.skipUnless(subprocess.run(["which", "gpgv"], capture_output=True).returncode == 0, "gpgv absent")
class GpgHome(unittest.TestCase):
    """A throwaway key whose signing subkey signs, as the publishers do."""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory()
        cls.home = Path(cls.work.name, "gnupg")
        cls.home.mkdir(mode=0o700)
        cls.keys = Path(cls.work.name, "keys")
        cls.keys.mkdir()
        cls.gpg("--quick-gen-key", "Editeur <e@example.org>", "ed25519", "cert", "never")
        listing = cls.gpg("--with-colons", "--list-keys").stdout
        cls.primary = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr"))
        cls.gpg("--quick-add-key", cls.primary, "ed25519", "sign", "never")
        (cls.keys / f"{cls.primary}.asc").write_bytes(cls.gpg("--armor", "--export", cls.primary, text=False).stdout)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    @classmethod
    def gpg(cls, *args, data=None, text=True):
        command = ["gpg", "--batch", "--homedir", str(cls.home), "--passphrase", "", "--pinentry-mode", "loopback"]
        result = subprocess.run(command + list(args), input=data, capture_output=True, text=text, check=True)
        return result


class RealSignatures(GpgHome):
    def clearsign(self, text):
        return self.gpg("--clearsign", data=text).stdout.encode()

    def test_only_the_signed_part_of_a_clearsigned_file_is_kept(self):
        signed = self.clearsign(f"{HASH_A}  a.iso\n")
        smuggled = f"{HASH_B}  b.iso\n".encode() + signed + f"{HASH_B}  c.iso\n".encode()
        kept = signatures.verify_signed(smuggled, None, self.primary, keys=self.keys)
        self.assertEqual(kept.decode().strip(), f"{HASH_A}  a.iso")

    def test_a_changed_clearsigned_file_is_refused(self):
        signed = self.clearsign(f"{HASH_A}  a.iso\n").replace(HASH_A.encode(), HASH_B.encode())
        with self.assertRaises(reseau.SourceError):
            signatures.verify_signed(signed, None, self.primary, keys=self.keys)

    def test_a_detached_signature_by_the_subkey_holds_for_its_primary_key(self):
        data = f"{HASH_A}  a.iso\n".encode()
        signature = self.gpg("--detach-sign", data=data, text=False).stdout
        self.assertEqual(signatures.verify_signed(data, signature, self.primary, keys=self.keys), data)
        with self.assertRaises(reseau.SourceError):
            signatures.verify_signed(data + b"x", signature, self.primary, keys=self.keys)

    def test_another_key_is_refused(self):
        other = "0" * 40
        (self.keys / f"{other}.asc").write_bytes((self.keys / f"{self.primary}.asc").read_bytes())
        with self.assertRaises(reseau.SourceError):
            signatures.verify_signed(self.clearsign("x\n"), None, other, keys=self.keys)


class FedoraKeys(GpgHome):
    """A release key the repository does not pin, read from fedoraproject.org."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gpg("--quick-gen-key", "Fedora (46) <fedora-46-primary@fedoraproject.org>", "ed25519", "sign", "never")
        listing = cls.gpg("--with-colons", "--list-keys", "fedora-46").stdout
        cls.fedora = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr"))
        cls.keyring = cls.gpg("--export", cls.fedora, text=False).stdout

    def setUp(self):
        self.fetched = tempfile.TemporaryDirectory()
        patch = mock.patch.object(signatures, "FETCHED_KEYS", Path(self.fetched.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.fetched.cleanup)

    def page(self, release, fingerprint):
        spaced = " ".join(fingerprint[i:i + 4] for i in range(0, 40, 4))
        return f"<p>Fedora {release}</p><p>ID: rsa4096/00 2026-01-01</p><p>Fingerprint: {spaced}</p>"

    def signer(self, release, page, pinned):
        return signatures.fedora_signer(release, fetch_text=lambda _: page, fetch_raw=lambda _: self.keyring, pinned=pinned)

    def test_an_unpinned_release_key_is_taken_from_the_official_page_and_keyring(self):
        self.assertEqual(self.signer("46", self.page("46", self.fedora), self.keys), self.fedora)
        self.assertTrue((Path(self.fetched.name) / f"{self.fedora}.asc").is_file())

    def test_a_pinned_key_is_used_without_reading_the_network(self):
        pinned = Path(self.fetched.name, "pinned")
        pinned.mkdir()
        (pinned / f"{self.fedora}.asc").write_bytes(self.gpg("--armor", "--export", self.fedora, text=False).stdout)
        self.assertEqual(signatures.fedora_signer("46", fetch_text=self.fail, fetch_raw=self.fail, pinned=pinned), self.fedora)

    def test_a_fingerprint_the_keyring_does_not_carry_is_refused(self):
        with self.assertRaises(reseau.SourceError):
            self.signer("46", self.page("46", "A" * 40), self.keys)

    def test_a_key_of_another_release_is_refused(self):
        with self.assertRaises(reseau.SourceError):
            self.signer("47", self.page("47", self.fedora), self.keys)


if __name__ == "__main__":
    unittest.main()
