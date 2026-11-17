#!/usr/bin/env python3
import unittest

from catalogue import veille
from donnees_essai import SOURCE


class Newer(unittest.TestCase):
    def sources(self):
        return [
            dict(SOURCE, id=f"u-{v}", base=f"https://example.org/{v}/", sums="SHA256SUMS")
            for v in ("24.04", "26.04")
        ]

    def test_a_published_newer_release_is_reported(self):
        listing = '<a href="22.04/">x</a><a href="24.04/">x</a><a href="26.04/">x</a><a href="26.10/">x</a><a href="27.04/">x</a>'
        pages = {"https://example.org/": listing, "https://example.org/26.10/SHA256SUMS": "ok"}

        def fetch(url):
            if url not in pages:
                raise OSError("404")
            return pages[url]

        self.assertEqual(
            veille.newer_releases(self.sources(), fetch),
            [{"family": "ubuntu", "version": "26.10", "where": "https://example.org/26.10/"}],
        )

    def test_point_releases_and_old_archives_are_not_newer(self):
        sources = self.sources() + [
            dict(SOURCE, id="old", base="https://old.example.org/18.04/", sums="SHA256SUMS")
        ]
        pages = {
            "https://example.org/": '<a href="26.04/">x</a><a href="26.04.1/">x</a>',
            "https://example.org/26.04.1/SHA256SUMS": "ok",
            "https://old.example.org/": '<a href="18.04/">x</a><a href="24.04/">x</a>',
            "https://old.example.org/24.04/SHA256SUMS": "ok",
        }

        def fetch(url):
            if url not in pages:
                raise OSError("404")
            return pages[url]

        self.assertEqual(veille.newer_releases(sources, fetch), [])

    def test_a_shared_listing_is_read_once(self):
        sources = [
            dict(SOURCE, id=f"{v}-{a}", base=f"https://example.org/{v}/", sums=f"SUMS-{a}")
            for v in ("1.0",) for a in ("amd64", "arm64")
        ]
        seen = []

        def fetch(url):
            seen.append(url)
            return '<a href="1.0/">x</a>'

        veille.newer_releases(sources, fetch)
        self.assertEqual(seen, ["https://example.org/"])

    def test_an_unreadable_listing_reports_nothing(self):
        def fetch(url):
            raise OSError("down")

        self.assertEqual(veille.newer_releases(self.sources(), fetch), [])


class NewerNamed(unittest.TestCase):
    SOURCE = {
        "id": "fedora-45-workstation-x86_64", "family": "fedora", "name": "Fedora", "channel": "stable",
        "arch": "x86_64", "homepage": "https://h/",
        "base": "https://m/releases/45/Workstation/x86_64/iso/",
        "listing": "https://dl/releases/45/Workstation/x86_64/iso/",
        "sums_pattern": r"Fedora-Workstation-45-[\d.]+-x86_64-CHECKSUM",
        "pattern": r"Fedora-Workstation-Live-(45-[\d.]+)\.x86_64\.iso",
    }

    def test_a_release_named_in_its_hash_file_is_reported(self):
        pages = {
            "https://dl/releases/": '<a href="44/">x</a><a href="45/">x</a><a href="46/">x</a><a href="47/">x</a>',
            "https://dl/releases/46/Workstation/x86_64/iso/": "Fedora-Workstation-46-1.2-x86_64-CHECKSUM",
            "https://dl/releases/47/Workstation/x86_64/iso/": "rien encore",
        }

        def fetch(url):
            if url not in pages:
                raise OSError("404")
            return pages[url]

        self.assertEqual(
            veille.newer_releases([self.SOURCE], fetch),
            [{"family": "fedora", "version": "46", "where": "https://m/releases/46/Workstation/x86_64/iso/"}],
        )

    def test_a_point_release_of_a_followed_major_is_not_news(self):
        source = dict(SOURCE, id="alma", family="alma", base="https://r/10/isos/x86_64/", sums="CHECKSUM")
        pages = {"https://r/": '<a href="10/">x</a><a href="10.2/">x</a>', "https://r/10.2/isos/x86_64/CHECKSUM": "ok"}
        self.assertEqual(veille.newer_releases([source], pages.__getitem__), [])

    def test_an_archived_release_reports_nothing(self):
        source = dict(SOURCE, id="d11", family="d", channel="archive", base="https://d/archive/11.11.0/amd64/", sums="S")
        pages = {"https://d/archive/": '<a href="11.11.0/">x</a><a href="12.0.0/">x</a>', "https://d/archive/12.0.0/amd64/S": "ok"}
        self.assertEqual(veille.newer_releases([source], pages.__getitem__), [])

    def test_a_release_number_inside_a_word_is_left_alone(self):
        self.assertEqual(veille.with_release("Rocky-8-x86_64-CHECKSUM", "8", "9"), "Rocky-9-x86_64-CHECKSUM")


if __name__ == "__main__":
    unittest.main()
