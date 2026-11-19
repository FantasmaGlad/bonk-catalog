#!/usr/bin/env python3
import json
import unittest
from pathlib import Path
from unittest import mock

from catalogue import empreintes, reseau, sources
from donnees_essai import HASH_A, HASH_B, SOURCE


class Resolving(unittest.TestCase):
    def test_link_and_hash_come_from_the_publisher(self):
        fetch = lambda url: f"{HASH_A} *ubuntu-26.04.1-desktop-amd64.iso\n"
        image = sources.resolve(SOURCE, fetch, lambda url: (5, "2026-08-01"))
        self.assertEqual(image["url"], "https://example.org/26.04/ubuntu-26.04.1-desktop-amd64.iso")
        self.assertEqual((image["version"], image["sha256"]), ("26.04.1", HASH_A))
        self.assertEqual((image["size_bytes"], image["released"]), (5, "2026-08-01"))

    def test_plain_http_is_refused(self):
        with self.assertRaises(reseau.SourceError):
            sources.resolve({**SOURCE, "base": "http://example.org/"}, None, None)

    def test_an_official_page_redirected_out_of_https_is_refused(self):
        answer = mock.MagicMock()
        answer.__enter__.return_value = answer
        answer.geturl.return_value = "http://h/p"
        with mock.patch.object(reseau, "request", return_value=answer):
            with self.assertRaisesRegex(reseau.SourceError, "redirected out of https"):
                reseau.reachable("https://h/p")

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
        image = sources.resolve(source, None, None, reach=seen.append)
        self.assertEqual(image["official_page"], "https://p/")
        self.assertEqual(seen, ["https://p/"])


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
        image = sources.resolve(source, fetch=pages.__getitem__, stat=lambda _: (5, "2026-01-02"))
        self.assertEqual(image["url"], "https://mirror/f-1.iso")


class Folders(unittest.TestCase):
    SOURCE = {
        "id": "oi", "family": "oi", "name": "OI", "channel": "stable", "arch": "x86_64", "homepage": "https://h/",
        "folders": "https://h/hipster/", "folder_pattern": r"\d{8}",
        "sums_pattern": r"oi-gui-\d{8}\.usb\.sha256sum", "pattern": r"oi-gui-(\d{8})\.usb",
    }

    def test_the_newest_folder_holding_the_image_is_chosen(self):
        pages = {
            "https://h/hipster/": '<a href="./20260430/">x</a><a href="./20261030/">x</a><a href="./20261107/">x</a><a href="./test/">x</a>',
            "https://h/hipster/20261107/": "oi-cloud-20261107.img.zst.sha256sum",
            "https://h/hipster/20261030/": "oi-gui-20261030.usb.sha256sum",
            "https://h/hipster/20261030/oi-gui-20261030.usb.sha256sum": f"{HASH_A}  oi-gui-20261030.usb",
        }
        image = sources.resolve(self.SOURCE, fetch=pages.__getitem__, stat=lambda _: (5, "2026-10-30"))
        self.assertEqual(image["url"], "https://h/hipster/20261030/oi-gui-20261030.usb")
        self.assertEqual((image["version"], image["sha256"]), ("20261030", HASH_A))

    def test_no_folder_with_the_image_is_an_error(self):
        pages = {"https://h/hipster/": '<a href="./20261107/">x</a>', "https://h/hipster/20261107/": "autre"}
        with self.assertRaisesRegex(reseau.SourceError, "no folder holds"):
            sources.resolve(self.SOURCE, fetch=pages.__getitem__, stat=None)


class Sha512(unittest.TestCase):
    SOURCE = dict(SOURCE, id="k", family="k", base="https://k/18.2/", sums="k.iso.sha512sums", pattern=r"k-([\d.]+)\.iso")

    def test_a_sha512_list_is_checked_while_computing_the_sha256(self):
        computed = []
        image = sources.resolve(
            self.SOURCE,
            fetch=lambda _: f"{'c' * 128}  k-18.2.iso",
            stat=lambda _: (5, "2026-01-02"),
            compute=lambda url, algorithm=None, expected=None: computed.append((algorithm, expected)) or HASH_A,
        )
        self.assertEqual(computed, [("sha512", "c" * 128)])
        self.assertEqual(image["sha256"], HASH_A)

    def test_an_unchanged_file_keeps_its_computed_hash(self):
        previous = {"url": "https://k/18.2/k-18.2.iso", "size_bytes": 5, "sha256": HASH_B}
        image = sources.resolve(
            self.SOURCE,
            fetch=lambda _: f"{'c' * 128}  k-18.2.iso",
            stat=lambda _: (5, "2026-01-02"),
            previous=previous,
            compute=lambda *_: self.fail("downloaded again"),
        )
        self.assertEqual(image["sha256"], HASH_B)


class Signed(unittest.TestCase):
    SOURCE = dict(SOURCE, signature={"suffix": ".gpg", "signer": "F" * 40})
    SUMS = f"{HASH_A} *ubuntu-26.04.1-desktop-amd64.iso\n".encode()

    def resolve(self, verify):
        raw = {"https://example.org/26.04/SHA256SUMS": self.SUMS, "https://example.org/26.04/SHA256SUMS.gpg": b"sig"}
        return sources.resolve(
            self.SOURCE, fetch=None, stat=lambda _: (5, "2026-08-01"), fetch_raw=raw.__getitem__, verify=verify
        )

    def test_the_hash_file_is_read_only_after_its_signature(self):
        seen = []
        image = self.resolve(lambda data, signature, signer: seen.append((data, signature, signer)) or data)
        self.assertEqual(seen, [(self.SUMS, b"sig", "F" * 40)])
        self.assertEqual(image["sha256"], HASH_A)

    def test_a_bad_signature_refuses_the_source(self):
        def refuse(*_):
            raise reseau.SourceError("hash file not signed")

        with self.assertRaises(reseau.SourceError):
            self.resolve(refuse)

    def test_a_pinned_key_exists_for_every_signer(self):
        sources = json.loads((Path(__file__).resolve().parents[1] / "sources/images.json").read_text())
        keys = Path(__file__).resolve().parents[1] / "keys/editeurs"
        for signer in {s["signature"]["signer"] for s in sources if "signer" in s.get("signature", {})}:
            self.assertTrue((keys / f"{signer}.asc").is_file(), signer)
        for source in sources:
            if source.get("signature", {}).get("signer_from") == "fedora":
                self.assertIn(f"/{source['signature']['release']}/", source["base"], source["id"])


class SignedElsewhere(unittest.TestCase):
    def test_a_clearsigned_hash_file_is_read_from_its_signed_part(self):
        source = dict(SOURCE, signature={"clearsigned": True, "signer": "F" * 40})
        raw = {"https://example.org/26.04/SHA256SUMS": b"signed"}
        seen = []

        def verify(data, signature, signer):
            seen.append((data, signature, signer))
            return f"{HASH_A} *ubuntu-26.04.1-desktop-amd64.iso\n".encode()

        image = sources.resolve(source, fetch=None, stat=lambda _: (5, "2026-08-01"), fetch_raw=raw.__getitem__, verify=verify)
        self.assertEqual(seen, [(b"signed", None, "F" * 40)])
        self.assertEqual(image["sha256"], HASH_A)

    def test_a_signed_copy_on_another_site_replaces_the_mirror_file(self):
        copy = "https://www.example.org/26.04/CHECKSUM.asc"
        source = dict(SOURCE, signature={"clearsigned": copy, "signer": "F" * 40})
        raw = {copy: b"signed copy"}
        verify = lambda data, signature, signer: f"SHA256 (ubuntu-26.04.1-desktop-amd64.iso) = {HASH_A}".encode()
        image = sources.resolve(source, fetch=None, stat=lambda _: (5, "2026-08-01"), fetch_raw=raw.__getitem__, verify=verify)
        self.assertEqual(image["sha256"], HASH_A)

    def test_a_hash_file_next_to_the_image_is_signed_too(self):
        source = {
            **Read.SOURCE,
            "latest": "https://l/leap.iso",
            "pattern": "(?P<url>https://\\S+\\.iso)",
            "verify_suffix": ".sha512",
            "signature": {"suffix": ".asc", "signer": "F" * 40},
        }
        raw = {"https://l/leap-1.iso.sha512": b"sums", "https://l/leap-1.iso.sha512.asc": b"sig"}
        seen = []

        def verify(data, signature, signer):
            seen.append((data, signature))
            return f"{'c' * 128}  leap-1.iso".encode()

        with mock.patch.object(sources, "request") as request:
            request.return_value.__enter__.return_value.geturl.return_value = "https://l/leap-1.iso"
            image = sources.resolve(
                source, fetch=None, stat=lambda _: (5, "2026-01-02"), fetch_raw=raw.__getitem__, verify=verify,
                compute=lambda url, algorithm, expected: HASH_B if expected == "c" * 128 else self.fail(expected),
            )
        self.assertEqual(seen, [(b"sums", b"sig")])
        self.assertEqual(image["sha256"], HASH_B)


class Read(unittest.TestCase):
    SOURCE = {"id": "r", "family": "f", "name": "F", "channel": "stable", "arch": "x86_64", "homepage": "https://h/"}

    def resolve(self, extra, fetch, **kwargs):
        return sources.resolve({**self.SOURCE, **extra}, fetch=fetch, stat=lambda _: (5, "2026-01-02"), **kwargs)

    def test_address_and_hash_from_a_json_answer(self):
        answer = f'{{"url":"https://h/a-7.iso","sha_sum":"{HASH_A}"}}'
        image = self.resolve(
            {"page": "https://h/j", "pattern": r'"url":"(?P<url>[^"]*a-(?P<version>\d+)\.iso)","sha_sum":"(?P<sha256>\w+)"'},
            lambda _: answer,
        )
        self.assertEqual((image["url"], image["version"], image["sha256"]), ("https://h/a-7.iso", "7", HASH_A))

    def test_newest_takes_the_highest_version_of_a_listing(self):
        listing = '<a href="a_9.2_x.img">a</a><a href="a_26.8.3_x.img">a</a><a href="a_26.2_x.img">a</a>'
        image = self.resolve(
            {"page": "https://h/l/", "pattern": r'href="(?P<url>a_(?P<version>[\d.]+)_x\.img)"', "url_prefix": "https://h/l/", "newest": True},
            lambda _: listing,
            compute=lambda *_: HASH_A,
        )
        self.assertEqual(image["url"], "https://h/l/a_26.8.3_x.img")

    def test_a_share_listing_gives_the_address_and_the_hash_file_by_number(self):
        listing = '{"data":[{"id":686,"name":"G-QUAKE.iso","type":"file"},{"id":687,"name":"G-QUAKE.iso.sha256sum"}]}'
        pages = {"https://k/559": listing, "https://k/687/download": f"{HASH_A}  G-QUAKE.iso"}
        image = self.resolve(
            {
                "page": "https://k/559",
                "pattern": r'"id":(?P<iso>\d+),"name":"(?P<file>G-(?P<version>[A-Z]+)\.iso)",.*?"id":(?P<sums>\d+),"name":"G-[A-Z]+\.iso\.sha256sum"',
                "url_template": "https://k/{iso}/download",
                "hash_template": "https://k/{sums}/download",
            },
            pages.__getitem__,
        )
        self.assertEqual((image["url"], image["version"], image["sha256"]), ("https://k/686/download", "QUAKE", HASH_A))
        self.assertEqual(image["file_name"], "G-QUAKE.iso")

    def test_an_address_ending_with_its_file_name_gets_no_file_name(self):
        listing = '<a href="a_9.2_x.img">a</a>'
        image = self.resolve(
            {"page": "https://h/l/", "pattern": r'href="(?P<url>(?P<file>a_(?P<version>[\d.]+)_x\.img))"', "url_prefix": "https://h/l/"},
            lambda _: listing,
            compute=lambda *_: HASH_A,
        )
        self.assertNotIn("file_name", image)

    def test_a_listed_name_that_is_not_a_plain_file_name_is_refused(self):
        listing = '{"data":[{"id":686,"name":"../G.iso"}]}'
        with self.assertRaises(reseau.SourceError):
            self.resolve(
                {"page": "https://k/559", "pattern": r'"id":(?P<iso>\d+),"name":"(?P<file>[^"]+)"', "url_template": "https://k/{iso}/download", "version": "1"},
                lambda _: listing,
                compute=lambda *_: HASH_A,
            )

    def test_a_published_hash_that_is_not_sha256_is_refused(self):
        with self.assertRaises(reseau.SourceError):
            self.resolve(
                {"page": "https://h/j", "pattern": r'"url":"(?P<url>[^"]*a-(?P<version>\d+)\.iso)","sha_sum":"(?P<sha256>\w+)"'},
                lambda _: '{"url":"https://h/a-7.iso","sha_sum":"abc"}',
            )

    def test_a_check_file_naming_no_algorithm_is_refused(self):
        with self.assertRaises(reseau.SourceError):
            self.resolve(
                {"page": "https://h/p", "pattern": r"(?P<url>//h/a-(?P<version>\d+)\.iso)", "verify_suffix": ".sum"},
                lambda _: "//h/a-7.iso",
            )

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

    def test_hash_file_named_without_the_image_extension(self):
        pages = {"https://h/p": "https://h/iso/a-7.iso", "https://h/sums/a-7.sha256.txt": f"{HASH_B}  a-7.iso"}
        extra = {
            "page": "https://h/p", "pattern": r"https://h/iso/a-(?P<version>\d+)\.iso",
            "hash_replace": [r"/iso/(.+)\.iso$", r"/sums/\1"], "hash_suffix": ".sha256.txt",
        }
        image = self.resolve(extra, pages.__getitem__)
        self.assertEqual((image["url"], image["sha256"]), ("https://h/iso/a-7.iso", HASH_B))

    def test_a_listed_sha512_with_another_name_is_checked(self):
        expected = "c" * 128
        seen = []
        pages = {"https://h/p": "https://h/a.iso", "https://h/a-sha512.checksum": expected}
        extra = {
            "page": "https://h/p", "pattern": r"https://h/a\.iso", "version": "1",
            "hash_replace": [r"\.iso$", ""], "verify_suffix": "-sha512.checksum",
        }
        self.resolve(extra, pages.__getitem__, compute=lambda *args: seen.append(args) or HASH_A)
        self.assertEqual(seen, [("https://h/a.iso", "sha512", expected)])

    def test_the_line_naming_the_image_is_read_in_a_shared_list(self):
        listed = "\n".join((
            f"SHA512 (a.iso.torrent) = {'a' * 128}",
            f"SHA512 (b.iso) = {'b' * 128}",
            f"SHA512 (a.iso) = {'c' * 128}",
        ))
        seen = []
        pages = {"https://h/p": "https://h/d/a.iso", "https://h/d/SHA512": listed}
        extra = {
            "page": "https://h/p", "pattern": r"https://h/d/a\.iso", "version": "1",
            "hash_replace": [r"[^/]+$", ""], "verify_suffix": "SHA512",
        }
        self.resolve(extra, pages.__getitem__, compute=lambda *args: seen.append(args) or HASH_A)
        self.assertEqual(seen, [("https://h/d/a.iso", "sha512", "c" * 128)])

    def test_a_list_that_does_not_name_the_image_is_refused(self):
        pages = {"https://h/p": "https://h/a-7.iso", "https://h/a-7.iso.sha256": f"{HASH_A}  b.iso\n{HASH_B}  c.iso"}
        with self.assertRaises(reseau.SourceError):
            self.resolve({"page": "https://h/p", "pattern": r"https://h/a-(?P<version>\d+)\.iso", "hash_suffix": ".sha256"}, pages.__getitem__)

    def test_a_listed_md5_is_checked_during_the_computation(self):
        seen = []
        pages = {"https://h/p": "https://h/d/a.img", "https://h/d/md5.txt": f"MD5 (a.img) = {'d' * 32}"}
        extra = {
            "page": "https://h/p", "pattern": r"https://h/d/a\.img", "version": "1",
            "hash_replace": [r"[^/]+$", ""], "verify_suffix": "md5.txt",
        }
        self.resolve(extra, pages.__getitem__, compute=lambda *args: seen.append(args) or HASH_A)
        self.assertEqual(seen, [("https://h/d/a.img", "md5", "d" * 32)])

    @staticmethod
    def served(final_url):
        body = mock.MagicMock()
        body.__enter__.return_value = body
        body.geturl.return_value = final_url
        body.read.side_effect = [b"data", b""]
        return mock.patch.object(reseau.urllib.request, "urlopen", return_value=body)

    def test_a_file_is_hashed_and_checked_against_the_publisher_hash(self):
        sha1 = "a17c9aaa61e80a1bf71d0d850af4e5baa9800bbd"
        sha256 = "3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7"
        with self.served("https://h/a.iso"):
            self.assertEqual(empreintes.hash_url("https://h/a.iso", "sha1", sha1), sha256)

    def test_a_wrong_publisher_hash_refuses_the_file(self):
        with self.served("https://h/a.iso"):
            with self.assertRaisesRegex(reseau.SourceError, "does not match"):
                empreintes.hash_url("https://h/a.iso", "sha1", "0" * 40)

    def test_a_file_redirected_out_of_https_is_not_hashed(self):
        with self.served("http://miroir/a.iso"):
            with self.assertRaisesRegex(reseau.SourceError, "redirected out of https"):
                empreintes.hash_url("https://h/a.iso")

    def test_a_kept_hash_is_reused_only_while_address_and_size_hold(self):
        extra = {"page": "https://h/p", "pattern": r"https://h/a\.iso", "version": "1"}
        fetch = lambda _: "https://h/a.iso"
        kept = self.resolve(
            extra, fetch, previous={"url": "https://h/a.iso", "size_bytes": 5, "sha256": HASH_B},
            compute=lambda *_: self.fail("must not download again"),
        )
        changed = self.resolve(
            extra, fetch, previous={"url": "https://h/a.iso", "size_bytes": 4, "sha256": HASH_B},
            compute=lambda *_: HASH_A,
        )
        self.assertEqual((kept["sha256"], changed["sha256"]), (HASH_B, HASH_A))

    def test_the_latest_redirect_names_the_file(self):
        answer = mock.MagicMock()
        answer.__enter__.return_value = answer
        answer.geturl.return_value = "https://h/d/a-7.img.xz"
        extra = {"latest": "https://h/latest", "pattern": r"https://h/d/a-(?P<version>\d+)\.img\.xz", "hash_suffix": ".sha256"}
        with mock.patch.object(sources, "request", return_value=answer):
            image = self.resolve(extra, lambda _: f"{HASH_A}  a-7.img.xz")
            self.assertEqual((image["url"], image["version"], image["sha256"]), ("https://h/d/a-7.img.xz", "7", HASH_A))
            with self.assertRaisesRegex(reseau.SourceError, "no address matches"):
                self.resolve({**extra, "pattern": r"autre"}, lambda _: "")

    def test_prefix_size_and_fixed_version_from_the_source(self):
        extra = {
            "page": "https://h/j", "url_prefix": "https://h/d/", "version": "stable",
            "pattern": r'"(?P<url>a\.img)","size":(?P<size>\d+),"sha":"(?P<sha256>\w+)"',
        }
        image = self.resolve(extra, lambda _: f'"a.img","size":9,"sha":"{HASH_A}"')
        self.assertEqual((image["url"], image["size_bytes"], image["version"]), ("https://h/d/a.img", 9, "stable"))

    def test_plain_http_is_refused(self):
        with self.assertRaises(reseau.SourceError):
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
        with mock.patch.object(sources, "request", return_value=self.response(url)):
            first = sources.resolve(self.SOURCE, compute=lambda _: HASH_A)
            kept = sources.resolve(
                self.SOURCE,
                previous={"url": url, "size_bytes": 5, "sha256": HASH_B},
                compute=lambda _: self.fail("must not download again"),
            )
        self.assertEqual((first["sha256"], kept["sha256"]), (HASH_A, HASH_B))
        self.assertEqual(first["note"], "evaluation")

    def test_a_changed_size_computes_the_hash_again(self):
        url = "https://software-static.download.prss.microsoft.com/x/w.iso"
        with mock.patch.object(sources, "request", return_value=self.response(url)):
            image = sources.resolve(
                self.SOURCE, previous={"url": url, "size_bytes": 4, "sha256": HASH_B}, compute=lambda _: HASH_A
            )
        self.assertEqual(image["sha256"], HASH_A)

    def test_a_direct_address_is_hashed_and_dated(self):
        source = {key: value for key, value in self.SOURCE.items() if key != "fwlink"} | {"direct": "https://t/T.ISO"}
        with mock.patch.object(sources, "request", return_value=self.response("https://t/T.ISO")):
            image = sources.resolve(source, compute=lambda _: HASH_A)
        self.assertEqual(
            (image["url"], image["sha256"], image["size_bytes"], image["released"]),
            ("https://t/T.ISO", HASH_A, 5, "2026-09-17"),
        )

    def test_a_direct_address_leaving_https_or_iso_is_refused(self):
        source = {key: value for key, value in self.SOURCE.items() if key != "fwlink"} | {"direct": "https://t/T.ISO"}
        for final in ("http://t/T.ISO", "https://t/T.zip"):
            with mock.patch.object(sources, "request", return_value=self.response(final)):
                with self.assertRaisesRegex(reseau.SourceError, "leads to"):
                    sources.resolve(source, compute=lambda _: HASH_A)

    def test_a_file_without_size_or_date_is_refused(self):
        answer = self.response("https://software-static.download.prss.microsoft.com/x/w.iso")
        answer.headers = {"Content-Length": "5"}
        with mock.patch.object(sources, "request", return_value=answer):
            with self.assertRaisesRegex(reseau.SourceError, "no size or date"):
                sources.resolve(self.SOURCE, compute=lambda _: HASH_A)

    def test_a_link_leaving_microsoft_is_refused(self):
        with mock.patch.object(sources, "request", return_value=self.response("https://evil.example/w.iso")):
            with self.assertRaises(reseau.SourceError):
                sources.resolve(self.SOURCE, compute=lambda _: HASH_A)


if __name__ == "__main__":
    unittest.main()
