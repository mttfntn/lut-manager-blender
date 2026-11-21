#!/usr/bin/env bash
# Download and unpack the newest Linux build of a Blender series, e.g. "5.3".
#
#   tools/get_blender.sh 5.3 [dest]     -> prints the path of the blender executable
#
# download.blender.org sometimes refuses CI machines, so every request is retried and
# then tried again on official mirrors (https://www.blender.org/download/mirrors/).
set -euo pipefail

SERIES="$1"
DEST="${2:-$HOME/blender}"
MIRRORS=(
  "https://download.blender.org/release"
  "https://mirrors.ocf.berkeley.edu/blender/release"
  "https://ftp.nluug.nl/pub/graphics/blender/release"
  "https://mirror.clarkson.edu/blender/release"
)
CURL=(curl --fail --silent --show-error --location --retry 4 --retry-all-errors --retry-delay 5 --connect-timeout 20)

file=""
for m in "${MIRRORS[@]}"; do
  if listing=$("${CURL[@]}" "$m/Blender$SERIES/"); then
    file=$(grep -oE "blender-$SERIES\.[0-9]+-linux-x64\.tar\.xz" <<<"$listing" | sort -t. -k3,3n -u | tail -1 || true)
    [ -n "$file" ] && break
  fi
  echo "no listing from $m, trying the next mirror" >&2
done
[ -n "$file" ] || { echo "could not find a Linux build of Blender $SERIES" >&2; exit 1; }

mkdir -p "$DEST"
archive="$DEST/$file"
if [ ! -s "$archive" ]; then
  for m in "${MIRRORS[@]}"; do
    echo "downloading $file from $m" >&2
    "${CURL[@]}" -o "$archive.part" "$m/Blender$SERIES/$file" && mv "$archive.part" "$archive" && break
    rm -f "$archive.part"
  done
fi
[ -s "$archive" ] || { echo "download of $file failed on every mirror" >&2; exit 1; }

dir="$DEST/${file%.tar.xz}"
[ -x "$dir/blender" ] || tar -xf "$archive" -C "$DEST"
echo "$dir/blender"
