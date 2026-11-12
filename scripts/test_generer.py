#!/usr/bin/env python3
import unittest
from unittest import mock

import generer

HASH_A = "a" * 64
HASH_B = "b" * 64
SOURCE = {
    "id": "ubuntu-26.04-desktop-x86_64",
    "family": "ubuntu",
    "name": "Ubuntu",
    "channel": "lts",
    "arch": "x86_64",
    "homepage": "https://ubuntu.com/",
    "base": "https://example.org/26.04/",
    "sums": "SHA256SUMS",
    "pattern": r"ubuntu-(26\.04(?:\.\d+)*)-desktop-amd64\.iso",
}


class Parsing(unittest.TestCase):
    def test_both_hash_formats(self):
        text = f"{HASH_A} *a.iso\n{HASH_B}  b.iso\nSHA256 (c.iso) = {HASH_A}\n# comment\n"
        self.assertEqual(
            generer.parse_sums(text), {"a.iso": HASH_A, "b.iso": HASH_B, "c.iso": HASH_A}
        )

    def test_highest_version_is_numeric_not_textual(self):
        hashes = {"x-9.iso": HASH_A, "x-10.iso": HASH_B, "other.iso": HASH_A}
        self.assertEqual(generer.pick_file(hashes, r"x-(\d+)\.iso"), ("x-10.iso", "10", HASH_B))

    def test_no_match_is_an_error(self):
        with self.assertRaises(generer.SourceError):
            generer.pick_file({"a.iso": HASH_A}, r"b-(\d+)\.iso")


class Resolving(unittest.TestCase):
    def test_link_and_hash_come_from_the_publisher(self):
        fetch = lambda url: f"{HASH_A} *ubuntu-26.04.1-desktop-amd64.iso\n"
        image = generer.resolve(SOURCE, fetch, lambda url: (5, "2026-08-01"))
        self.assertEqual(image["url"], "https://example.org/26.04/ubuntu-26.04.1-desktop-amd64.iso")
        self.assertEqual((image["version"], image["sha256"]), ("26.04.1", HASH_A))
        self.assertEqual((image["size_bytes"], image["released"]), (5, "2026-08-01"))

    def test_plain_http_is_refused(self):
        with self.assertRaises(generer.SourceError):
            generer.resolve({**SOURCE, "base": "http://example.org/"}, None, None)

    def test_official_page_is_checked_not_downloaded(self):
        source = {
            "id": "w",
            "family": "windows",
            "name": "Windows",
            "channel": "stable",
            "arch": "x86_64",
            "homepage": "https://h/",
            "version": "11",
            "released": "2021-10-05",
            "official_page": "https://p/",
        }
        seen = []
        image = generer.resolve(source, None, None, reach=seen.append)
        self.assertEqual(image["official_page"], "https://p/")
        self.assertEqual(seen, ["https://p/"])


class Building(unittest.TestCase):
    def test_failed_source_keeps_its_last_entry_marked_stale(self):
        old = {"id": SOURCE["id"], "url": "https://old/", "status": "online", "checked": "1970-01-01"}
        previous = {"images": [old]}
        with mock.patch.object(generer, "resolve", side_effect=generer.SourceError("down")):
            catalog, failures = generer.build([SOURCE], [], {}, previous, 100)
        self.assertEqual(catalog["images"], [dict(old, status="stale")])
        self.assertEqual(failures, [{"id": SOURCE["id"], "error": "down"}])
        self.assertEqual(catalog["sequence"], 100)
        self.assertEqual(catalog["expires_unix"], 100 + generer.LIFETIME_SECONDS)

    def test_a_resolved_link_is_online_and_dated(self):
        with mock.patch.object(generer, "resolve", return_value={"id": SOURCE["id"], "url": "https://new/"}):
            catalog, _ = generer.build([SOURCE], [], {}, {}, 86400 * 2)
        self.assertEqual(catalog["images"][0]["status"], "online")
        self.assertEqual(catalog["images"][0]["checked"], "1970-01-03")

    def test_failed_source_without_history_is_left_out(self):
        with mock.patch.object(generer, "resolve", side_effect=OSError("down")):
            catalog, failures = generer.build([SOURCE], [], {}, {}, 100)
        self.assertEqual(catalog["images"], [])
        self.assertEqual(len(failures), 1)


class Families(unittest.TestCase):
    def test_a_flavour_names_its_parent_and_its_logo_source(self):
        family = {"id": "kubuntu", "name": "Kubuntu", "category": "Debian derivatives", "icon": "kubuntu.png",
                  "parent": "ubuntu", "icon_source": "https://kubuntu.org/"}
        entry = generer.family_entry(family, {"kubuntu.png": "c" * 64})
        self.assertEqual(entry["parent"], "ubuntu")
        self.assertEqual(entry["icon"], "icons/kubuntu.png")
        self.assertNotIn("homepage", entry)

    def test_the_first_previous_catalog_that_answers_is_read(self):
        answers = {"https://c/v2/": OSError("404"), "https://c/v1/": '{"images": []}'}

        def fetch(url):
            answer = answers[url]
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(generer, "fetch_text", side_effect=fetch):
            self.assertEqual(generer.previous_catalog(["https://c/v2/", "https://c/v1/"]), {"images": []})
            self.assertEqual(generer.previous_catalog(None), {})


class Listing(unittest.TestCase):
    def test_the_hash_file_is_found_in_the_stable_listing(self):
        source = {
            "id": "f", "family": "f", "name": "F", "channel": "stable", "arch": "x86_64", "homepage": "https://h/",
            "base": "https://mirror/", "listing": "https://primary/",
            "sums_pattern": r"F-\d+-CHECKSUM", "pattern": r"f-(\d+)\.iso",
        }
        pages = {
            "https://primary/": "F-1-CHECKSUM",
            "https://mirror/": "F-1-CHEC..>",
            "https://mirror/F-1-CHECKSUM": f"{HASH_A}  f-1.iso",
        }
        image = generer.resolve(source, fetch=pages.__getitem__, stat=lambda _: (5, "2026-01-02"))
        self.assertEqual(image["url"], "https://mirror/f-1.iso")


class Read(unittest.TestCase):
    SOURCE = {"id": "r", "family": "f", "name": "F", "channel": "stable", "arch": "x86_64", "homepage": "https://h/"}

    def resolve(self, extra, fetch, **kwargs):
        return generer.resolve({**self.SOURCE, **extra}, fetch=fetch, stat=lambda _: (5, "2026-01-02"), **kwargs)

    def test_address_and_hash_from_a_json_answer(self):
        answer = f'{{"url":"https://h/a-7.iso","sha_sum":"{HASH_A}"}}'
        image = self.resolve(
            {"page": "https://h/j", "pattern": r'"url":"(?P<url>[^"]*a-(?P<version>\d+)\.iso)","sha_sum":"(?P<sha256>\w+)"'},
            lambda _: answer,
        )
        self.assertEqual((image["url"], image["version"], image["sha256"]), ("https://h/a-7.iso", "7", HASH_A))

    def test_hash_file_next_to_the_image(self):
        pages = {"https://h/p": "//h/a-7.iso", "https://h/a-7.iso.sha256": f"{HASH_B}  a-7.iso"}
        image = self.resolve(
            {"page": "https://h/p", "pattern": r"(?P<url>//h/a-(?P<version>\d+)\.iso)", "hash_suffix": ".sha256"},
            pages.__getitem__,
        )
        self.assertEqual((image["url"], image["sha256"]), ("https://h/a-7.iso", HASH_B))

    def test_a_listed_sha512_is_checked_during_the_computation(self):
        expected = "c" * 128
        seen = []
        pages = {"https://h/p": "https://h/a.iso", "https://h/a.iso.sha512": expected}

        def compute(*args):
            seen.append(args)
            return HASH_A

        extra = {"page": "https://h/p", "pattern": r"https://h/a\.iso", "verify_suffix": ".sha512", "version": "1"}
        self.resolve(extra, pages.__getitem__, compute=compute)
        self.assertEqual(seen, [("https://h/a.iso", "sha512", expected)])

    def test_a_wrong_publisher_hash_refuses_the_file(self):
        body = mock.MagicMock()
        body.__enter__.return_value = body
        body.read.side_effect = [b"data", b""]
        with mock.patch.object(generer.urllib.request, "urlopen", return_value=body):
            with self.assertRaises(generer.SourceError):
                generer.hash_url("https://h/a.iso", "sha1", "0" * 40)

    def test_plain_http_is_refused(self):
        with self.assertRaises(generer.SourceError):
            self.resolve({"page": "https://h/p", "pattern": r"http://h/a\.iso", "hash_suffix": ".s"}, lambda _: "http://h/a.iso")


class Windows(unittest.TestCase):
    SOURCE = {
        "id": "w",
        "family": "windows",
        "name": "Windows",
        "channel": "lts",
        "arch": "x86_64",
        "homepage": "https://h/",
        "version": "11",
        "fwlink": 1,
        "note": "evaluation",
    }

    def response(self, url):
        answer = mock.MagicMock()
        answer.__enter__.return_value = answer
        answer.geturl.return_value = url
        answer.headers = {"Content-Length": "5", "Last-Modified": "Thu, 17 Sep 2026 10:00:00 GMT"}
        return answer

    def test_hash_computed_once_then_kept(self):
        url = "https://software-static.download.prss.microsoft.com/x/w.iso"
        with mock.patch.object(generer, "request", return_value=self.response(url)):
            first = generer.resolve(self.SOURCE, compute=lambda _: HASH_A)
            kept = generer.resolve(
                self.SOURCE,
                previous={"url": url, "size_bytes": 5, "sha256": HASH_B},
                compute=lambda _: self.fail("must not download again"),
            )
        self.assertEqual((first["sha256"], kept["sha256"]), (HASH_A, HASH_B))
        self.assertEqual(first["note"], "evaluation")

    def test_a_link_leaving_microsoft_is_refused(self):
        with mock.patch.object(generer, "request", return_value=self.response("https://evil.example/w.iso")):
            with self.assertRaises(generer.SourceError):
                generer.resolve(self.SOURCE, compute=lambda _: HASH_A)


class Page(unittest.TestCase):
    def test_lists_every_image_with_its_publisher_link_and_no_script(self):
        catalog = {
            "sequence": 0,
            "expires_unix": 86400,
            "families": [{"id": "ubuntu", "name": "Ubuntu", "category": "Debian <derivatives>"}],
            "images": [
                dict(SOURCE, version="26.04.1", url="https://example.org/a.iso", size_bytes=10**9),
                {"id": "w", "family": "ubuntu", "name": "W", "arch": "x86_64", "version": "1",
                 "homepage": "https://example.org/", "official_page": "https://example.org/get"},
            ],
        }
        page = generer.index_page(catalog)
        self.assertIn('href="https://example.org/a.iso"', page)
        self.assertIn('href="https://example.org/get"', page)
        self.assertIn("Debian &lt;derivatives&gt;", page)
        self.assertIn("1970-01-02", page)
        self.assertIn("stale since 1970-01-01", generer.index_page(dict(catalog, images=[dict(catalog["images"][0], status="stale", checked="1970-01-01")])))
        self.assertNotIn("<script", page)
        update = {"family": "ubuntu", "version": "26.10", "where": "https://example.org/26.10/"}
        self.assertIn("ubuntu 26.10", generer.index_page(catalog, [update]))


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
            generer.newer_releases(self.sources(), fetch),
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

        self.assertEqual(generer.newer_releases(sources, fetch), [])

    def test_a_shared_listing_is_read_once(self):
        sources = [
            dict(SOURCE, id=f"{v}-{a}", base=f"https://example.org/{v}/", sums=f"SUMS-{a}")
            for v in ("1.0",) for a in ("amd64", "arm64")
        ]
        seen = []

        def fetch(url):
            seen.append(url)
            return '<a href="1.0/">x</a>'

        generer.newer_releases(sources, fetch)
        self.assertEqual(seen, ["https://example.org/"])

    def test_an_unreadable_listing_reports_nothing(self):
        def fetch(url):
            raise OSError("down")

        self.assertEqual(generer.newer_releases(self.sources(), fetch), [])


class Sizes(unittest.TestCase):
    def test_megabytes_under_a_gigabyte(self):
        self.assertEqual(generer.size_label(17_350_656), "17 MB")
        self.assertEqual(generer.size_label(400), "1 MB")
        self.assertEqual(generer.size_label(8_191_000_000), "8.2 GB")


class SourceErrors(unittest.TestCase):
    FAMILY = {"id": "debian", "name": "Debian", "category": "Debian derivatives", "icon": "debian.png"}

    def errors(self, family=None, image=None):
        return generer.source_errors([dict(self.FAMILY, **(family or {}))],
                                     [dict(SOURCE, family="debian", **(image or {}))])

    def test_known_names_and_sourced_memory_pass(self):
        self.assertEqual(self.errors(
            {"uses": ["desktop", "server"], "ram_mb": 1024, "ram_source": "https://www.debian.org/"},
            {"desktop": "xfce", "kind": "live", "media": "usb"},
        ), [])

    def test_unknown_names_are_refused(self):
        errors = self.errors({"uses": ["daily"]}, {"desktop": "plasma", "kind": "iso", "media": "cd"})
        self.assertEqual(len(errors), 4)

    def test_memory_needs_its_publisher_page(self):
        self.assertEqual(len(self.errors({"ram_mb": 1024})), 1)
        self.assertEqual(len(self.errors(image={"ram_mb": "1 GB", "ram_source": "https://x.org/"})), 1)

    def test_an_image_of_an_unknown_family_is_refused(self):
        errors = generer.source_errors([self.FAMILY], [SOURCE])
        self.assertEqual(errors, ["image ubuntu-26.04-desktop-x86_64: unknown family ubuntu"])

    def test_variant_fields_reach_the_catalog(self):
        source = dict(SOURCE, desktop="gnome", kind="live", ram_mb=6144, ram_source="https://ubuntu.com/")
        fetch = lambda url: f"{HASH_A}  ubuntu-26.04.1-desktop-amd64.iso"
        image = generer.resolve(source, fetch=fetch, stat=lambda url: (1, "2026-01-01"))
        self.assertEqual((image["desktop"], image["kind"], image["ram_mb"]), ("gnome", "live", 6144))


if __name__ == "__main__":
    unittest.main()
