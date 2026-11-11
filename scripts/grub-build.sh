#!/bin/sh
# Usage: grub-build.sh <verified archive> <early config> <x86_64 | aarch64> <output EFI image>
# Needs gcc, make, gawk, bison, flex and python3; aarch64 also needs gcc-aarch64-linux-gnu.
set -eu
archive=$(realpath "$1")
early=$(realpath "$2")
arch=$3
output=$(realpath -m "$4")
case "$arch" in
  x86_64) target=x86_64 format=x86_64-efi ;;
  aarch64) target=aarch64-linux-gnu format=arm64-efi ;;
  *) echo "unknown architecture: $arch" >&2; exit 2 ;;
esac
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
tar -C "$work" -xf "$archive"
source_dir=$(find "$work" -mindepth 1 -maxdepth 1 -type d)
(
  cd "$source_dir"
  ./configure --target="$target" --with-platform=efi --disable-werror --prefix="$work/prefix" > "$work/configure.log" 2>&1 \
    || { tail -n 40 "$work/configure.log"; exit 1; }
  make -j"$(nproc)" > "$work/make.log" 2>&1 || { tail -n 40 "$work/make.log"; exit 1; }
  make install > "$work/install.log" 2>&1 || { tail -n 40 "$work/install.log"; exit 1; }
)
# Everything the vault menu needs, so the drive works without any other GRUB file.
"$work/prefix/bin/grub-mkstandalone" -O "$format" -o "$output" \
  --modules="part_msdos part_gpt fat exfat search search_fs_file configfile normal" \
  --install-modules="part_msdos part_gpt fat exfat iso9660 udf loopback linux normal configfile search search_fs_file search_label echo test regexp ls cat all_video gfxterm font efi_gop chain boot sleep halt reboot probe" \
  --locales= --fonts= --themes= \
  "boot/grub/grub.cfg=$early"
