#!/usr/bin/env bash
# Usage: bonk-publie.sh <tag>; prints the path of the Linux x86_64 bonk of that release.
set -euo pipefail
version=$1
archive="bonk-${version#v}-linux-x86_64.tar.gz"
dossier="$RUNNER_TEMP/bonk"
gh release download "$version" --repo FantasmaGlad/Bonk --pattern "$archive" --pattern SHA256SUMS --dir "$dossier" >&2
(cd "$dossier" && sha256sum --check --ignore-missing SHA256SUMS >&2)
gh attestation verify "$dossier/$archive" --repo FantasmaGlad/Bonk >&2
tar -xzf "$dossier/$archive" -C "$dossier"
bonk=$(find "$dossier" -type f -name bonk -perm -u+x -print -quit)
test -n "$bonk"
echo "$bonk"
