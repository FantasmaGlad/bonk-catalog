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

    def test_official_page_needs_no_network(self):
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
        self.assertEqual(generer.resolve(source, None, None)["official_page"], "https://p/")


class Building(unittest.TestCase):
    def test_failed_source_keeps_its_last_entry_and_is_reported(self):
        previous = {"images": [{"id": SOURCE["id"], "url": "https://old/"}]}
        with mock.patch.object(generer, "resolve", side_effect=generer.SourceError("down")):
            catalog, failures = generer.build([SOURCE], [], {}, previous, 100)
        self.assertEqual(catalog["images"], previous["images"])
        self.assertEqual(failures, [{"id": SOURCE["id"], "error": "down"}])
        self.assertEqual(catalog["sequence"], 100)
        self.assertEqual(catalog["expires_unix"], 100 + generer.LIFETIME_SECONDS)

    def test_failed_source_without_history_is_left_out(self):
        with mock.patch.object(generer, "resolve", side_effect=OSError("down")):
            catalog, failures = generer.build([SOURCE], [], {}, {}, 100)
        self.assertEqual(catalog["images"], [])
        self.assertEqual(len(failures), 1)


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


if __name__ == "__main__":
    unittest.main()
