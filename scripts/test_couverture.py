#!/usr/bin/env python3
import stat
import tempfile
import unittest
from pathlib import Path

from couverture import bonk_info

from catalogue.couverture import is_iso, measure, merge, summary, vault_of

DAY = "2026-11-30"


def image(identifier, url, **extra):
    return dict({"id": identifier, "url": url}, **extra)


def report(family):
    return {"vault": {"family": family, "name": family, "arch": "x86_64"} if family else None}


class Coverage(unittest.TestCase):
    def test_only_an_iso_address_is_measured(self):
        cases = [
            (image("a", "https://h/a.iso"), True),
            (image("b", "https://h/B.ISO"), True),
            (image("c", "https://h/c.img.xz"), False),
            ({"id": "d", "official_page": "https://h/"}, False),
        ]
        for entry, expected in cases:
            self.assertEqual(is_iso(entry), expected, entry)

    def test_each_iso_is_asked_to_bonk_by_its_address(self):
        asked = []

        def info(url):
            asked.append(url)
            return {"https://h/live.iso": report("debian_live"), "https://h/bsd.iso": report(None)}.get(url)

        images = [image("live", "https://h/live.iso"), image("bsd", "https://h/bsd.iso"), image("x", "https://h/x.xz")]
        self.assertEqual(measure(images, info, workers=2), {"live": report("debian_live"), "bsd": report(None)})
        self.assertEqual(sorted(asked), ["https://h/bsd.iso", "https://h/live.iso"])

    def test_an_unreadable_iso_keeps_its_last_measure(self):
        previous = {"gone": {"family": "arch", "checked": "2026-11-23"}}
        measures = {"live": report("debian_live"), "bsd": report(None), "gone": None, "new": None}
        coverage, unreadable = merge(measures, previous, DAY)
        self.assertEqual(
            coverage,
            {
                "live": {"family": "debian_live", "checked": DAY},
                "bsd": {"family": None, "checked": DAY},
                "gone": {"family": "arch", "checked": "2026-11-23"},
            },
        )
        self.assertEqual(unreadable, ["gone", "new"])

    def test_a_bonk_without_the_vault_field_measures_nothing(self):
        coverage, unreadable = merge({"live": {"route": "raw"}}, {}, DAY)
        self.assertEqual((coverage, unreadable), ({}, ["live"]))

    def test_the_family_goes_to_the_image_only_when_recognised(self):
        coverage = {"live": {"family": "debian_live", "checked": DAY}, "bsd": {"family": None, "checked": DAY}}
        self.assertEqual(vault_of(coverage, "live"), {"vault_family": "debian_live"})
        self.assertEqual(vault_of(coverage, "bsd"), {})
        self.assertEqual(vault_of(coverage, "other"), {})

    def test_the_rate_counts_the_isos_of_the_catalog(self):
        images = [
            image("live", "https://h/live.iso", vault_family="debian_live"),
            image("bsd", "https://h/bsd.iso"),
            image("arm", "https://h/arm.img.xz"),
        ]
        self.assertEqual(summary({"images": images}), (1, 2))


class Command(unittest.TestCase):
    def test_bonk_is_asked_its_json_and_a_failure_is_no_measure(self):
        with tempfile.TemporaryDirectory() as folder:
            bonk = Path(folder) / "bonk"
            bonk.write_text(
                '#!/bin/sh\n'
                'case "$2" in *fail*) echo refused >&2; exit 3;; esac\n'
                'printf \'{"args":"%s %s %s"}\' "$1" "$2" "$3"\n'
            )
            bonk.chmod(bonk.stat().st_mode | stat.S_IXUSR)
            info = bonk_info(str(bonk))
            self.assertEqual(info("https://h/a.iso"), {"args": "info https://h/a.iso --json"})
            self.assertIsNone(info("https://h/fail.iso"))


if __name__ == "__main__":
    unittest.main()
