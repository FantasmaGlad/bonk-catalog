<p align="center">
  <img src="https://raw.githubusercontent.com/FantasmaGlad/Bonk/main/crates/bonk/assets/icons/128x128/bonk.png" width="96" height="96" alt="Bonk">
</p>

<h1 align="center">bonk-catalog</h1>

<p align="center">
  <b>The signed list of bootable images offered by Bonk.</b>
</p>

<p align="center">
  <a href="https://catalog.bonk-os.app/v1/catalog.json"><b>catalog.json</b></a> &nbsp;·&nbsp;
  <a href="https://bonk-os.app/images/"><b>Systems</b></a>
</p>

<p align="center">
  <a href="https://github.com/FantasmaGlad/bonk-catalog/actions/workflows/publier.yml"><img src="https://github.com/FantasmaGlad/bonk-catalog/actions/workflows/publier.yml/badge.svg?branch=main" alt="Publish"></a>
</p>

---

## Files

| File | Content |
| :--- | :--- |
| [`v1/catalog.json`](https://catalog.bonk-os.app/v1/catalog.json) | systems, versions, official links, SHA-256 hashes |
| [`v1/catalog.json.sig`](https://catalog.bonk-os.app/v1/catalog.json.sig) | Ed25519 signature of the exact bytes of `catalog.json` |
| `v1/icons/` | system logos, covered by their hash in the catalog |

## How it works

- No image is hosted here. Links and hashes are read every day from each publisher's own hash file.
- Bonk checks the signature with keys built into it, downloads from the publisher, then checks the hash before writing.
- A publisher that cannot be read keeps its last entry, and an issue reports it.
- The catalog expires after 14 days: if publishing stops, Bonk refuses stale links.

Logos are trademarks of their respective owners, used to identify their systems.

---

License: view-only, all rights reserved ([LICENSE](LICENSE)) · Security: [SECURITY.md](SECURITY.md) · Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

<sub>Related repositories: [Bonk](https://github.com/FantasmaGlad/Bonk) (application) · [BonkWeb](https://github.com/FantasmaGlad/BonkWeb) (website) · [bonk-packages](https://github.com/FantasmaGlad/bonk-packages) (package repositories) · [homebrew-bonk](https://github.com/FantasmaGlad/homebrew-bonk) (Homebrew tap) · [bonk-security](https://github.com/FantasmaGlad/bonk-security) (security site) · Security policy: [security.bonk-os.app](https://security.bonk-os.app), [`/.well-known/security.txt`](https://bonk-os.app/.well-known/security.txt)</sub>
