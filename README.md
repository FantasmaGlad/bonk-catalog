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
  <img src="docs/icons/gitlab.svg" width="16" height="16" valign="middle" alt="GitLab">&nbsp;Backup copy: <a href="https://gitlab.com/FantasmaGlad/bonk-catalog">gitlab.com/FantasmaGlad/bonk-catalog</a>
</p>

<p align="center">
  <a href="https://github.com/FantasmaGlad/bonk-catalog/actions/workflows/publier.yml"><img src="https://github.com/FantasmaGlad/bonk-catalog/actions/workflows/publier.yml/badge.svg?branch=main" alt="Publish"></a>
</p>

## Files

| File | Content |
| :--- | :--- |
| [`v2/catalog.json`](https://catalog.bonk-os.app/v2/catalog.json) | systems, versions, links, SHA-256 hashes |
| [`v2/catalog.json.sig`](https://catalog.bonk-os.app/v2/catalog.json.sig) | Ed25519 signature, checked by Bonk with its built-in keys |
| [`grub.json`](https://catalog.bonk-os.app/grub.json) | GRUB of the vault: UEFI and BIOS images, GNU source and signature |

## Sources

No image is hosted. Every day, links and hashes are read from each publisher; a publisher's signature is checked against its key pinned in `keys/editeurs/`. An image that can no longer be read is kept 30 days, then removed; the catalog expires 14 days after publication. Each week, representative images are started under QEMU and the proof is published with them.

GRUB is built from the signed GNU archive named in [`sources/grub.json`](sources/grub.json), under the GPL-3.0-or-later, its source published next to it.

## License

View-only, all rights reserved: [LICENSE](LICENSE). Logos are trademarks of their owners, used to identify their systems.

Report a vulnerability as described in [SECURITY.md](SECURITY.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

## Related repositories

| Repository | Content |
| :--- | :--- |
| <img src="docs/icons/bonk.png" width="20" height="20" valign="middle" alt="Bonk">&nbsp;**[Bonk](https://github.com/FantasmaGlad/Bonk)** | application |
| <img src="docs/icons/bonk.png" width="20" height="20" valign="middle" alt="BonkWeb">&nbsp;**[BonkWeb](https://github.com/FantasmaGlad/BonkWeb)** | website |
| <img src="docs/icons/linux.svg" width="20" height="20" valign="middle" alt="bonk-packages">&nbsp;**[bonk-packages](https://github.com/FantasmaGlad/bonk-packages)** | package repositories |
| <img src="docs/icons/homebrew.svg" width="20" height="20" valign="middle" alt="homebrew-bonk">&nbsp;**[homebrew-bonk](https://github.com/FantasmaGlad/homebrew-bonk)** | Homebrew tap |
| <img src="docs/icons/bonk.png" width="20" height="20" valign="middle" alt="bonk-security">&nbsp;**[bonk-security](https://github.com/FantasmaGlad/bonk-security)** | security site |
