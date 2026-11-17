#!/usr/bin/env python3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from catalogue import construction, reseau
from donnees_essai import SOURCE

# A failed read is tried again at once in tests, not after the real pause.
construction.RETRY_PAUSE = 0


class Building(unittest.TestCase):
    def test_failed_source_keeps_its_last_entry_marked_stale(self):
        old = {"id": SOURCE["id"], "url": "https://old/", "status": "online", "checked": "1970-01-01"}
        previous = {"images": [old]}
        with mock.patch.object(construction, "resolve", side_effect=reseau.SourceError("down")):
            catalog, failures = construction.build([SOURCE], [], {}, previous, 100)
        self.assertEqual(catalog["images"], [dict(old, status="stale")])
        self.assertEqual(failures, [{"id": SOURCE["id"], "error": "down"}])
        self.assertEqual(catalog["sequence"], 100)
        self.assertEqual(catalog["expires_unix"], 100 + construction.LIFETIME_SECONDS)

    def test_a_resolved_link_is_online_and_dated(self):
        with mock.patch.object(construction, "resolve", return_value={"id": SOURCE["id"], "url": "https://new/"}):
            catalog, _ = construction.build([SOURCE], [], {}, {}, 86400 * 2)
        self.assertEqual(catalog["images"][0]["status"], "online")
        self.assertEqual(catalog["images"][0]["checked"], "1970-01-03")

    def test_sources_keep_their_order_when_read_side_by_side(self):
        sources = [dict(SOURCE, id=f"s{n}") for n in range(20)]

        def resolve(source, **_):
            time.sleep(0.001 * (20 - int(source["id"][1:])))
            return {"id": source["id"]}

        with mock.patch.object(construction, "resolve", side_effect=resolve):
            catalog, _ = construction.build(sources, [], {}, {}, 100)
        self.assertEqual([image["id"] for image in catalog["images"]], [source["id"] for source in sources])

    def test_a_network_error_is_tried_once_more(self):
        calls = []

        def resolve(source, **_):
            calls.append(source["id"])
            if len(calls) == 1:
                raise OSError("dropped")
            return {"id": source["id"]}

        with mock.patch.object(construction, "resolve", side_effect=resolve):
            catalog, failures = construction.build([SOURCE], [], {}, {}, 100)
        self.assertEqual((len(calls), failures), (2, []))
        self.assertEqual(catalog["images"][0]["status"], "online")

    def test_a_source_stale_for_more_than_a_month_leaves_the_catalog(self):
        day = 86400
        fresh = {"id": "a", "url": "https://a/", "checked": "1970-01-30"}
        old = {"id": "b", "url": "https://b/", "checked": "1970-01-01"}
        with mock.patch.object(construction, "resolve", side_effect=reseau.SourceError("gone")):
            catalog, failures = construction.build(
                [dict(SOURCE, id="a"), dict(SOURCE, id="b")], [], {}, {"images": [fresh, old]}, 31 * day
            )
        self.assertEqual([image["id"] for image in catalog["images"]], ["a"])
        self.assertEqual(failures[1], {"id": "b", "error": "gone; removed after 31 days without a good read"})

    def test_failed_source_without_history_is_left_out(self):
        with mock.patch.object(construction, "resolve", side_effect=OSError("down")):
            catalog, failures = construction.build([SOURCE], [], {}, {}, 100)
        self.assertEqual(catalog["images"], [])
        self.assertEqual(len(failures), 1)


class Families(unittest.TestCase):
    def test_a_flavour_names_its_parent_and_its_logo_source(self):
        family = {"id": "kubuntu", "name": "Kubuntu", "category": "ubuntu", "icon": "kubuntu.png",
                  "parent": "ubuntu", "icon_source": "https://kubuntu.org/"}
        entry = construction.family_entry(family, {"kubuntu.png": "c" * 64})
        self.assertEqual(entry["parent"], "ubuntu")
        self.assertEqual(entry["icon"], "icons/kubuntu.png")
        self.assertNotIn("homepage", entry)

    def test_the_first_previous_catalog_that_answers_is_read(self):
        answers = {"https://c/v2/": OSError("404"), "https://c/v1/": b'{"images": []}', "https://c/v1/.sig": b"s"}

        def fetch(url):
            answer = answers[url]
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch.object(construction, "fetch_bytes", side_effect=fetch), mock.patch.object(
            construction, "signed", return_value=True
        ):
            self.assertEqual(construction.previous_catalog(["https://c/v2/", "https://c/v1/"]), {"images": []})
            self.assertEqual(construction.previous_catalog(None), {})

    def test_only_a_signed_previous_catalog_is_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            key, public = Path(folder, "k.pem"), Path(folder, "p.pem")
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", key], check=True)
            subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-out", public], check=True)
            manifest = Path(folder, "m")
            manifest.write_bytes(b'{"images": [{"id": "a"}]}')
            subprocess.run(
                ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", key, "-in", manifest, "-out", Path(folder, "s")],
                check=True,
            )
            signature = Path(folder, "s").read_bytes()
            pages = {"https://c/": manifest.read_bytes(), "https://c/.sig": signature}
            with mock.patch.object(construction, "fetch_bytes", side_effect=pages.__getitem__):
                self.assertEqual(construction.previous_catalog(["https://c/"], [public]), {"images": [{"id": "a"}]})
            pages["https://c/"] = b'{"images": [{"id": "b"}]}'
            with mock.patch.object(construction, "fetch_bytes", side_effect=pages.__getitem__):
                self.assertEqual(construction.previous_catalog(["https://c/"], [public]), {})


if __name__ == "__main__":
    unittest.main()
