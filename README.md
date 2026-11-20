<p align="center">
  <img src="docs/icons/bonk.png" width="96" height="96" alt="Bonk">
</p>

<h1 align="center">bonk-catalog</h1>

<p align="center">
  The signed list of bootable images offered by Bonk.
</p>

<p align="center">
  <a href="https://catalog.bonk-os.app">catalog.bonk-os.app</a>,
  <a href="https://catalog.bonk-os.app/v2/catalog.json">catalog.json</a>,
  <a href="https://bonk-os.app/images/">Systems</a>
</p>

<p align="center">
  <a href="https://github.com/FantasmaGlad/bonk-catalog/actions/workflows/publier.yml"><img src="https://github.com/FantasmaGlad/bonk-catalog/actions/workflows/publier.yml/badge.svg?branch=main" alt="Publish"></a>
</p>

## Files

| File | Content |
| :--- | :--- |
| [`v2/catalog.json`](https://catalog.bonk-os.app/v2/catalog.json) | systems, versions, links, SHA-256 hashes, usages and bases |
| [`v2/catalog.json.sig`](https://catalog.bonk-os.app/v2/catalog.json.sig) | Ed25519 signature of `catalog.json` |
| `v2/icons/` | system logos, each covered by its hash in the catalog |
| [`index.html`](https://catalog.bonk-os.app) | the same list, for a browser |

## Sources

No image is hosted here. Every day, links and hashes are read from each publisher: its hash file, or the image itself, downloaded and hashed once per address when no hash is published.

A system sold, behind an account or shared by torrent is listed with its official page only, without a link or a hash.

When the publisher signs its hash file (Ubuntu, Debian, Fedora, openSUSE, Kicksecure, FreeBSD), the signature is checked against its key, pinned in `keys/editeurs/`, before any hash is read.

Usages and bases, with their English and French labels, live in `sources/taxonomy.json`. Adding one needs no Bonk release.

## Freshness

Each image carries `status` and `checked`. It is `online` when its publisher was read that day, `stale` when the read failed and the last entry was kept. After 30 days without a good read, the entry is removed. An issue lists the failed sources.

Newer releases found at a publisher but not yet listed are reported in `v2/updates.json` and in an issue.

The catalog expires 14 days after publication. If publishing stops, Bonk refuses the outdated links.

Bonk checks the signature with the keys built into it, downloads from the publisher, and checks the hash before writing.

## GRUB for the vault

| File | Content |
| :--- | :--- |
| [`grub.json`](https://catalog.bonk-os.app/grub.json) | version, links, hashes and signature check of the published GRUB |
| `v2/grub/<version>/` | `BOOTX64.EFI`, `BOOTAA64.EFI`, the GNU source archive and its signature |

The source is the GNU release archive named in [`sources/grub.json`](sources/grub.json), with its SHA-256 and the fingerprint of its signer. Its signature is checked by `gpgv` against [`keys/grub-signataire.asc`](keys/grub-signataire.asc) before the build and at each publication.

[`scripts/grub-build.sh`](scripts/grub-build.sh) builds one standalone image per architecture, `BOOTX64.EFI` and `BOOTAA64.EFI`. It runs again only when the version or the build files change.

After deployment, every file is downloaded from its public address and checked again. Every Monday, a workflow looks for a newer signed GRUB and opens a pull request; nothing is published before it is merged.

GRUB is free software under the GPL-3.0-or-later; its source is published next to each build.

## Logos

Logos are trademarks of their owners, used to identify their systems.

## License

View-only, all rights reserved: [LICENSE](LICENSE).

Report a vulnerability as described in [SECURITY.md](SECURITY.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

## Related repositories

| Repository | Content |
| :--- | :--- |
| <img src="docs/icons/bonk.png" width="20" height="20" valign="middle" alt="Bonk">&nbsp;**[Bonk](https://github.com/FantasmaGlad/Bonk)** | application |
| <img src="docs/icons/bonk.png" width="20" height="20" valign="middle" alt="BonkWeb">&nbsp;**[BonkWeb](https://github.com/FantasmaGlad/BonkWeb)** | website |
| <img src="docs/icons/linux.svg" width="20" height="20" valign="middle" alt="bonk-packages">&nbsp;**[bonk-packages](https://github.com/FantasmaGlad/bonk-packages)** | package repositories |
| <img src="docs/icons/homebrew.svg" width="20" height="20" valign="middle" alt="homebrew-bonk">&nbsp;**[homebrew-bonk](https://github.com/FantasmaGlad/homebrew-bonk)** | Homebrew tap |
| <img src="docs/icons/bonk.png" width="20" height="20" valign="middle" alt="bonk-security">&nbsp;**[bonk-security](https://github.com/FantasmaGlad/bonk-security)** | security site |
