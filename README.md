# bonk-catalog

The signed list of bootable images offered by [Bonk](https://bonk-os.app), published at `https://catalog.bonk-os.app/v1/`.

| File | Content |
|---|---|
| `v1/catalog.json` | systems, versions, official links, SHA-256 hashes |
| `v1/catalog.json.sig` | raw Ed25519 signature of the exact bytes of `catalog.json` |
| `v1/icons/` | system logos, covered by their hash in the catalog |

No image is hosted here. Links and hashes are read every day from each publisher's own published hash file (`sources/images.json`), never typed by hand. Bonk checks the signature with public keys built into it, then downloads directly from the publisher and checks the hash before writing.

A publisher that cannot be read keeps its last published entry, and an issue reports it. The catalog expires after 14 days: if publishing stops, clients refuse it rather than trust stale links.

Adding an image: add a source to `sources/images.json`; `python3 -m unittest` in `scripts/` runs the tests, and `scripts/generer.py --output /tmp/site/v1` reads the real sources.

Logos are trademarks of their respective owners, used to identify their systems. Code under the MIT license.
