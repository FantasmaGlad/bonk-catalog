#!/usr/bin/env python3
import unittest

from catalogue.banc import bench_errors, matrix, merge, signer_of

DEBIAN = {
    "image": "debian",
    "firmware": ["bios", "uefi", "secureboot"],
    "loader": {"bios": "isolinux", "uefi": "grub"},
    "proof": "login:",
    "options": ["--delai", "300"],
    "bios_options": ["--attente", "8"],
}
RUN = "https://github.com/o/r/actions/runs/1"


def result(firmware, ok, info=None):
    return {"image": "debian", "firmware": firmware, "ok": ok, "info": info}


class Bench(unittest.TestCase):
    def test_each_firmware_is_a_boot_with_its_loader_and_options(self):
        boots = matrix([DEBIAN])
        self.assertEqual([boot["firmware"] for boot in boots], ["bios", "uefi", "secureboot"])
        self.assertEqual([boot["loader"] for boot in boots], ["isolinux", "grub", "grub"])
        self.assertEqual(boots[0]["options"], "--delai 300 --attente 8")
        self.assertEqual(boots[1]["options"], "--delai 300")

    def test_a_bench_entry_outside_the_rules_is_refused(self):
        self.assertEqual(list(bench_errors([DEBIAN], {"debian"})), [])
        cases = [
            (dict(DEBIAN, image="absente"), "not an image"),
            (dict(DEBIAN, firmware=["coreboot"]), "firmware"),
            (dict(DEBIAN, firmware=[]), "firmware"),
            (dict(DEBIAN, loader={"uefi": "grub"}), "loader"),
            (dict(DEBIAN, proof=""), "proof"),
        ]
        for entry, reason in cases:
            errors = list(bench_errors([entry], {"debian"}))
            self.assertTrue(any(reason in error for error in errors), (entry, errors))

    def test_a_proof_names_what_booted_this_run_and_what_failed_is_reported(self):
        info = {"route": "raw", "efi_loaders": [{"machine": "x86_64", "signer": "unsigned"}]}
        results = [result("bios", True, info), result("uefi", True), result("secureboot", False)]
        proofs, failures = merge([DEBIAN], results, {}, RUN, "2026-11-23")
        self.assertEqual(
            proofs["debian"],
            {
                "firmware": ["bios", "uefi"],
                "proved": {"date": "2026-11-23", "bench": RUN},
                "route": "raw",
                "secure_boot": "unsigned",
            },
        )
        self.assertEqual(failures, [{"id": "debian", "error": "boot failed: secureboot"}])

    def test_an_image_that_no_longer_boots_keeps_its_last_proof(self):
        old = {"firmware": ["uefi"], "proved": {"date": "2026-11-16", "bench": "https://old"}}
        proofs, failures = merge([DEBIAN], [result("uefi", False)], {"debian": old, "partie": old}, RUN, "2026-11-23")
        self.assertEqual(proofs, {"debian": old})
        self.assertEqual(failures, [{"id": "debian", "error": "boot failed: bios, uefi, secureboot"}])

    def test_the_signer_of_a_windows_drive_comes_from_its_extraction(self):
        self.assertEqual(signer_of({"extraction": {"signer": "windows_uefi_ca2023"}, "efi_loaders": []}), "windows_uefi_ca2023")
        self.assertIsNone(signer_of({"efi_loaders": []}))


if __name__ == "__main__":
    unittest.main()
