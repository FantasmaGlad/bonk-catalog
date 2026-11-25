#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

from conformite import catalog_errors

FAMILY = {"id": "debian", "icon": "icons/debian.png", "icon_sha256": "a" * 64}
IMAGE = {
    "id": "debian-13-netinst-x86_64",
    "family": "debian",
    "released": "2026-09-12",
    "homepage": "https://www.debian.org/",
    "url": "https://cdimage.debian.org/x.iso",
    "sha256": "b" * 64,
}


def errors(families=(FAMILY,), images=(IMAGE,)):
    return catalog_errors({"families": list(families), "images": list(images)})


class Reader(unittest.TestCase):
    def test_a_valid_catalog_has_no_error(self):
        self.assertEqual(errors(), [])

    def test_each_rule_of_bonk_is_enforced(self):
        cases = {
            "duplicate identifier": dict(images=(IMAGE, IMAGE)),
            "unknown family": dict(images=(dict(IMAGE, family="arch"),)),
            "identifier must be lowercase ASCII": dict(images=(dict(IMAGE, id="Debian"),)),
            "release date must be YYYY-MM-DD": dict(images=(dict(IMAGE, released="12/09/2026"),)),
            "homepage must be https": dict(images=(dict(IMAGE, homepage="http://www.debian.org/"),)),
            "url must be https": dict(images=(dict(IMAGE, url="http://x/x.iso"),)),
            "sha256 must be 64 hexadecimal characters": dict(images=(dict(IMAGE, sha256="0"),)),
            "mirror must be https or an onion address": dict(images=(dict(IMAGE, mirrors=["http://m/x.iso"]),)),
            "icon must be a relative path": dict(families=(dict(FAMILY, icon="/etc/x.png"),)),
        }
        for reason, catalog in cases.items():
            with self.subTest(reason):
                self.assertTrue(any(reason in error for error in errors(**catalog)), errors(**catalog))

    def test_an_onion_mirror_and_an_official_page_pass(self):
        page = {key: IMAGE[key] for key in ("id", "family", "released", "homepage")}
        image = dict(IMAGE, mirrors=["http://abc.onion/x.iso"])
        self.assertEqual(errors(images=(image, dict(page, id="w", official_page="https://w/"))), [])



VAULT = json.loads((Path(__file__).resolve().parent.parent / "sources/vault.json").read_text())


def vault_errors(families):
    return catalog_errors({"families": [FAMILY], "images": [IMAGE], "vault_families": families})


class VaultFamilies(unittest.TestCase):
    def test_the_published_families_pass(self):
        self.assertEqual(vault_errors(VAULT), [])

    def test_a_value_that_could_leave_the_menu_is_refused(self):
        debian = VAULT[0]
        cases = {
            "identifier": dict(debian, id="Debian"),
            "name": dict(debian, name='Debian" ; reboot'),
            "detect": dict(debian, detect=[["../live/vmlinuz"]]),
            "label prefix": dict(debian, label_prefix="Fedora.*"),
            "kernel": dict(debian, kernel="/live/vmlinuz"),
            "options": dict(debian, options="boot=live $root"),
            "initrd extra": dict(debian, initrd_extra="a b"),
        }
        for reason, family in cases.items():
            with self.subTest(reason):
                self.assertTrue(any(reason in error for error in vault_errors([family])), vault_errors([family]))
        self.assertTrue(any("duplicate" in error for error in vault_errors([debian, debian])))


if __name__ == "__main__":
    unittest.main()
