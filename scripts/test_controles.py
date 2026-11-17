#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

from catalogue import controles, sources
from donnees_essai import HASH_A, SOURCE


class Duplicates(unittest.TestCase):
    def test_a_duplicate_identifier_is_refused(self):
        family = {"id": "ubuntu", "name": "Ubuntu", "category": "ubuntu", "icon": "ubuntu.png", "uses": ["office"]}
        taxonomy = {"uses": [{"id": "office"}], "bases": [{"id": "ubuntu"}]}
        errors = controles.source_errors([family], [SOURCE, dict(SOURCE)], taxonomy)
        self.assertIn(f"image {SOURCE['id']}: duplicate identifier", errors)

    def test_the_published_sources_have_unique_identifiers(self):
        sources = json.loads((Path(__file__).resolve().parents[1] / "sources/images.json").read_text())
        ids = [source["id"] for source in sources]
        self.assertEqual(len(ids), len(set(ids)))


class SourceErrors(unittest.TestCase):
    FAMILY = {"id": "debian", "name": "Debian", "category": "debian", "icon": "debian.png"}
    TAXONOMY = {
        "uses": [{"id": "office", "en": "Office", "fr": "Bureautique"},
                 {"id": "server", "en": "Servers", "fr": "Serveurs"}],
        "bases": [{"id": "debian", "en": "Debian", "fr": "Debian"}],
    }

    def errors(self, family=None, image=None, taxonomy=None):
        return controles.source_errors([dict(self.FAMILY, **(family or {}))],
                                     [dict(SOURCE, family="debian", **(image or {}))],
                                     taxonomy or self.TAXONOMY)

    def test_known_names_and_sourced_memory_pass(self):
        self.assertEqual(self.errors(
            {"uses": ["office", "server"], "ram_mb": 1024, "ram_source": "https://www.debian.org/"},
            {"desktop": "xfce", "kind": "live", "media": "usb"},
        ), [])

    def test_unknown_names_are_refused(self):
        errors = self.errors({"uses": ["daily"], "category": "Debian derivatives"},
                             {"desktop": "plasma", "kind": "iso", "media": "cd"})
        self.assertEqual(len(errors), 5)

    def test_a_term_without_a_label_or_listed_twice_is_refused(self):
        uses = [{"id": "office", "en": "Office", "fr": ""}, {"id": "office", "en": "Office", "fr": "Bureautique"}]
        errors = self.errors(taxonomy=dict(self.TAXONOMY, uses=uses))
        self.assertEqual(errors, ["taxonomy: uses office listed twice", "taxonomy: uses office has no fr label"])

    def test_the_published_taxonomy_labels_every_family(self):
        root = Path(__file__).resolve().parent.parent
        load = lambda name: json.loads((root / "sources" / name).read_text())
        errors = controles.source_errors(load("families.json"), load("images.json"), load("taxonomy.json"))
        self.assertEqual(errors, [])
        self.assertTrue(all(family.get("uses") for family in load("families.json")))

    def test_memory_needs_its_publisher_page(self):
        self.assertEqual(len(self.errors({"ram_mb": 1024})), 1)
        self.assertEqual(len(self.errors(image={"ram_mb": "1 GB", "ram_source": "https://x.org/"})), 1)

    def test_an_image_of_an_unknown_family_is_refused(self):
        errors = controles.source_errors([self.FAMILY], [SOURCE], self.TAXONOMY)
        self.assertEqual(errors, ["image ubuntu-26.04-desktop-x86_64: unknown family ubuntu"])

    def test_variant_fields_reach_the_catalog(self):
        source = dict(SOURCE, desktop="gnome", kind="live", ram_mb=6144, ram_source="https://ubuntu.com/")
        fetch = lambda url: f"{HASH_A}  ubuntu-26.04.1-desktop-amd64.iso"
        image = sources.resolve(source, fetch=fetch, stat=lambda url: (1, "2026-01-01"))
        self.assertEqual((image["desktop"], image["kind"], image["ram_mb"]), ("gnome", "live", 6144))


if __name__ == "__main__":
    unittest.main()
