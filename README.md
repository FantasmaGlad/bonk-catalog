<p align="center">
  <img src="docs/icons/bonk.png" width="96" height="96" alt="Bonk">
</p>

<h1 align="center">bonk-catalog</h1>

<p align="center">
  <b>The signed list of bootable images offered by Bonk.</b>
</p>

<p align="center">
  <a href="https://catalog.bonk-os.app"><b>catalog.bonk-os.app</b></a> &nbsp;·&nbsp;
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
| [`index.html`](https://catalog.bonk-os.app) | the same list, readable in a browser |

## How it works

- No image is hosted here. Links and hashes are read every day from each publisher's own hash file.
- Bonk checks the signature with keys built into it, downloads from the publisher, then checks the hash before writing.
- Each image carries `status` and `checked`: `online` when its link answered on that date, `stale` when its publisher could not be read and the last entry was kept. An issue lists the stale ones.
- Newer releases found at a publisher but not yet listed are reported in `v1/updates.json` and in an issue.
- The catalog expires after 14 days: if publishing stops, Bonk refuses stale links.

## GRUB for the vault

| File | Content |
| :--- | :--- |
| [`grub.json`](https://catalog.bonk-os.app/grub.json) | status of the published GRUB: version, links, hashes, signature check |
| `v1/grub/<version>/` | `BOOTX64.EFI`, the GNU source archive and its signature |

- **Source**: the GNU release archive named in [`sources/grub.json`](sources/grub.json), with its SHA-256 and the fingerprint of its signer.
- **Signature**: checked with `gpgv` against [`keys/grub-signataire.asc`](keys/grub-signataire.asc) before anything is built, and again at each publication.
- **Build**: [`scripts/grub-build.sh`](scripts/grub-build.sh) builds one standalone `BOOTX64.EFI` for x86_64. It is rebuilt only when the version or the build files change; otherwise the published bytes are reused and checked.
- **Publication**: the catalog entry `grub` names each file and its hash, covered by the catalog signature. After deployment, every file is downloaded from the public address and checked again.
- **Updates**: every Monday a workflow looks for a newer signed GRUB, builds it and opens a pull request. Nothing is published before it is merged.

GRUB is free software under the GPL-3.0-or-later; its source is published next to each build.

Logos are trademarks of their respective owners, used to identify their systems.

---

License: view-only, all rights reserved ([LICENSE](LICENSE)) · Security: [SECURITY.md](SECURITY.md) · Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)

<sub>Related repositories: [Bonk](https://github.com/FantasmaGlad/Bonk) (application) · [BonkWeb](https://github.com/FantasmaGlad/BonkWeb) (website) · [bonk-packages](https://github.com/FantasmaGlad/bonk-packages) (package repositories) · [homebrew-bonk](https://github.com/FantasmaGlad/homebrew-bonk) (Homebrew tap) · [bonk-security](https://github.com/FantasmaGlad/bonk-security) (security site) · Security policy: [security.bonk-os.app](https://security.bonk-os.app), [`/.well-known/security.txt`](https://bonk-os.app/.well-known/security.txt)</sub>
