#!/usr/bin/env python3
import unittest

from catalogue import page as catalogue_page
from donnees_essai import SOURCE


class Page(unittest.TestCase):
    def test_lists_every_image_with_its_publisher_link_and_no_script(self):
        catalog = {
            "sequence": 0,
            "expires_unix": 86400,
            "families": [{"id": "ubuntu", "name": "Ubuntu", "category": "ubuntu"}],
            "taxonomy": {"bases": [{"id": "debian", "en": "Debian", "fr": "Debian"},
                                   {"id": "ubuntu", "en": "Ubuntu <LTS>", "fr": "Ubuntu"}]},
            "images": [
                dict(SOURCE, version="26.04.1", url="https://example.org/a.iso", size_bytes=10**9),
                {"id": "w", "family": "ubuntu", "name": "W", "arch": "x86_64", "version": "1",
                 "homepage": "https://example.org/", "official_page": "https://example.org/get"},
            ],
        }
        page = catalogue_page.index_page(catalog)
        self.assertIn('href="https://example.org/a.iso"', page)
        self.assertIn('href="https://example.org/get"', page)
        self.assertIn("<h2>Ubuntu &lt;LTS&gt;</h2>", page)
        self.assertNotIn("<h2>Debian</h2>", page)
        self.assertIn("1970-01-02", page)
        self.assertIn("stale, last read 1970-01-01", catalogue_page.index_page(dict(catalog, images=[dict(catalog["images"][0], status="stale", checked="1970-01-01")])))
        self.assertNotIn("<script", page)
        update = {"family": "ubuntu", "version": "26.10", "where": "https://example.org/26.10/"}
        self.assertIn("ubuntu 26.10", catalogue_page.index_page(catalog, [update]))


class Sizes(unittest.TestCase):
    def test_megabytes_under_a_gigabyte(self):
        self.assertEqual(catalogue_page.size_label(17_350_656), "17 MB")
        self.assertEqual(catalogue_page.size_label(400), "1 MB")
        self.assertEqual(catalogue_page.size_label(8_191_000_000), "8.2 GB")


if __name__ == "__main__":
    unittest.main()
