#!/usr/bin/env python3
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import grub

LISTING = """
<a href="grub-2.12.tar.xz">grub-2.12.tar.xz</a>
<a href="grub-2.14.tar.xz">grub-2.14.tar.xz</a> <a href="grub-2.14.tar.xz.sig">grub-2.14.tar.xz.sig</a>
<a href="grub-2.9.tar.xz">grub-2.9.tar.xz</a> <a href="grub-2.14.1.tar.gz">grub-2.14.1.tar.gz</a>
"""


def entry_for(data):
    return {"path": "", "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


class Listing(unittest.TestCase):
    def test_newest_xz_archive_by_number_not_text(self):
        self.assertEqual(grub.newest(LISTING), "2.14")

    def test_signatures_and_other_formats_are_ignored(self):
        self.assertEqual(grub.newest('<a href="grub-3.0.tar.xz.sig">'), None)


class Prepare(unittest.TestCase):
    def setUp(self):
        self.output = Path(tempfile.mkdtemp())
        self.source = json.loads(grub.SOURCE.read_text())

    def previous(self, files):
        folder = f"grub/{self.source['version']}/"
        names = {"efi_x86_64": "BOOTX64.EFI", "source": self.source["archive"],
                 "source_signature": self.source["archive"] + ".sig"}
        entry = {"version": self.source["version"], "released": self.source["released"],
                 "build": grub.build_id()}
        for key, name in names.items():
            entry[key] = dict(entry_for(files[key]), path=folder + name)
        return entry

    def test_published_bytes_are_reused_when_nothing_changed(self):
        files = {"efi_x86_64": b"efi", "source": b"tar", "source_signature": b"sig"}
        previous = self.previous(files)
        by_path = {"https://x/" + previous[k]["path"]: v for k, v in files.items()}
        build = mock.Mock()
        with mock.patch.object(grub, "download", side_effect=lambda url, limit=None: by_path[url]):
            entry, error = grub.prepare(self.output, previous, published="https://x/", build=build)
        self.assertEqual(entry, previous)
        build.assert_not_called()
        self.assertEqual((self.output / previous["efi_x86_64"]["path"]).read_bytes(), b"efi")

    def test_a_tampered_published_file_is_rebuilt(self):
        files = {"efi_x86_64": b"efi", "source": b"tar", "source_signature": b"sig"}
        previous = self.previous(files)
        served = lambda url, limit=None: b"autre" if url.endswith("EFI") else files["source"]
        built = []
        with mock.patch.object(grub, "download", side_effect=served), \
                mock.patch.object(grub, "fetch_archive", side_effect=self.fake_archive):
            entry, error = grub.prepare(self.output, previous, published="https://x/",
                                 build=lambda archive, efi: (built.append(efi), efi.write_bytes(b"neuf")))
        self.assertEqual(len(built), 1)
        self.assertEqual(entry["efi_x86_64"]["sha256"], hashlib.sha256(b"neuf").hexdigest())

    def test_a_new_build_id_rebuilds(self):
        previous = dict(self.previous({"efi_x86_64": b"e", "source": b"s", "source_signature": b"g"}),
                        build="0" * 16)
        with mock.patch.object(grub, "download") as download, \
                mock.patch.object(grub, "fetch_archive", side_effect=self.fake_archive):
            entry, error = grub.prepare(self.output, previous, build=lambda a, efi: efi.write_bytes(b"x"))
        download.assert_not_called()
        self.assertEqual(entry["build"], grub.build_id())
        self.assertEqual(entry["source"]["sha256"], hashlib.sha256(b"archive").hexdigest())

    def test_a_failed_build_keeps_the_published_one(self):
        files = {"efi_x86_64": b"efi", "source": b"tar", "source_signature": b"sig"}
        previous = dict(self.previous(files), build="0" * 16)
        by_path = {"https://x/" + previous[k]["path"]: v for k, v in files.items()}
        with mock.patch.object(grub, "download", side_effect=lambda url, limit=None: by_path[url]), \
                mock.patch.object(grub, "fetch_archive", side_effect=OSError("miroir absent")):
            entry, error = grub.prepare(self.output, previous, published="https://x/")
        self.assertEqual(entry, previous)
        self.assertIn("miroir absent", error)

    def fake_archive(self, source, folder):
        (folder / source["archive"]).write_bytes(b"archive")
        (folder / (source["archive"] + ".sig")).write_bytes(b"signature")


class Status(unittest.TestCase):
    def test_a_checked_entry_is_ok_and_names_its_urls(self):
        output = Path(tempfile.mkdtemp())
        files = {"efi_x86_64": b"efi", "source": b"tar", "source_signature": b"sig"}
        entry = {"version": "2.14", "released": "2026-01-14", "build": "b"}
        for key, data in files.items():
            (output / key).write_bytes(data)
            entry[key] = dict(entry_for(data), path=key)
        with mock.patch.object(grub, "verify_signature", return_value="2026-01-14"):
            report = grub.status_report(entry, output, None, "2026-11-09", published="https://c/")
        self.assertEqual((report["status"], report["signed"]), ("ok", "2026-01-14"))
        self.assertEqual(report["efi_x86_64"]["url"], "https://c/efi_x86_64")
        self.assertIn("GNU signature: valid", grub.status_text(report))

    def test_a_bad_signature_is_failed(self):
        output = Path(tempfile.mkdtemp())
        entry = {"version": "2.14", "released": "2026-01-14", "build": "b"}
        for key in ("efi_x86_64", "source", "source_signature"):
            (output / key).write_bytes(b"x")
            entry[key] = dict(entry_for(b"x"), path=key)
        report = grub.status_report(entry, output, None, "2026-11-09")
        self.assertEqual(report["status"], "failed")
        self.assertIn("NOT VERIFIED", grub.status_text(report))

    def test_no_entry_is_missing(self):
        report = grub.status_report(None, Path("."), "miroirs absents", "2026-11-09")
        self.assertEqual(report["status"], "missing")
        self.assertIn("miroirs absents", grub.status_text(report))


class Archive(unittest.TestCase):
    def test_a_hash_different_from_the_source_is_refused_before_the_signature(self):
        source = {"archive": "grub-2.14.tar.xz", "mirrors": ["https://m/"], "sha256": "0" * 64,
                  "signer": "X"}
        with mock.patch.object(grub, "download", return_value=b"data"), \
                mock.patch.object(grub, "verify_signature") as verify:
            with self.assertRaises(grub.GrubError):
                grub.fetch_archive(source, Path(tempfile.mkdtemp()))
        verify.assert_not_called()


class Watch(unittest.TestCase):
    def test_a_newer_signed_release_updates_the_source(self):
        original = grub.SOURCE.read_text()
        listing = LISTING.replace("2.12", "2.16")
        served = lambda url, limit=None: listing.encode() if url.endswith("/") else b"new"
        try:
            with mock.patch.object(grub, "verify_signature", return_value="2026-07-01"):
                self.assertEqual(grub.watch(served), "2.16")
            source = json.loads(grub.SOURCE.read_text())
            self.assertEqual((source["version"], source["released"]), ("2.16", "2026-07-01"))
            self.assertEqual(source["sha256"], hashlib.sha256(b"new").hexdigest())
        finally:
            grub.SOURCE.write_text(original)

    def test_nothing_newer_changes_nothing(self):
        original = grub.SOURCE.read_text()
        self.assertIsNone(grub.watch(lambda url, limit=None: LISTING.encode()))
        self.assertEqual(grub.SOURCE.read_text(), original)

    def test_an_unsigned_release_is_refused(self):
        original = grub.SOURCE.read_text()
        listing = LISTING.replace("2.12", "2.16")
        served = lambda url, limit=None: listing.encode() if url.endswith("/") else b"new"
        try:
            with self.assertRaises(grub.GrubError):
                grub.watch(served)
        finally:
            self.assertEqual(grub.SOURCE.read_text(), original)
            grub.SOURCE.write_text(original)


if __name__ == "__main__":
    unittest.main()
