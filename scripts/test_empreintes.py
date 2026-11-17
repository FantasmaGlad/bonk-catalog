#!/usr/bin/env python3
import unittest

from catalogue import empreintes, reseau
from donnees_essai import HASH_A, HASH_B


class Parsing(unittest.TestCase):
    def test_both_hash_formats(self):
        text = f"{HASH_A} *a.iso\n{HASH_B}  b.iso\nSHA256 (c.iso) = {HASH_A}\n# comment\n"
        self.assertEqual(
            empreintes.parse_sums(text), {"a.iso": HASH_A, "b.iso": HASH_B, "c.iso": HASH_A}
        )

    def test_highest_version_is_numeric_not_textual(self):
        hashes = {"x-9.iso": HASH_A, "x-10.iso": HASH_B, "other.iso": HASH_A}
        self.assertEqual(empreintes.pick_file(hashes, r"x-(\d+)\.iso"), ("x-10.iso", "10", HASH_B))

    def test_no_match_is_an_error(self):
        with self.assertRaises(reseau.SourceError):
            empreintes.pick_file({"a.iso": HASH_A}, r"b-(\d+)\.iso")


class ListingOrder(unittest.TestCase):
    def test_the_newest_hash_file_is_chosen_by_number(self):
        listing = "F-9-CHECKSUM F-10-CHECKSUM F-10-CHECKSUM"
        self.assertEqual(empreintes.find_in_listing(listing, r"F-\d+-CHECKSUM"), "F-10-CHECKSUM")

    def test_a_listing_without_hash_file_is_refused(self):
        with self.assertRaisesRegex(reseau.SourceError, "no hash file matches"):
            empreintes.find_in_listing("rien", r"F-\d+-CHECKSUM")


if __name__ == "__main__":
    unittest.main()
