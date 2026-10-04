#!/bin/sh
# Install the schemaviz binary: sh install.sh   (or: curl -fsSL <raw url of this file> | sh)
# Environment: INSTALL_DIR (default ~/.local/bin), VERSION (default: latest schemaviz-v* release),
#              SCHEMAVIZ_BASE_URL (override where assets are downloaded from; used by the tests)
set -eu

REPO="phin-tech/skills"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"

os=$(uname -s | tr '[:upper:]' '[:lower:]')
arch=$(uname -m)
case "$os" in darwin | linux) ;; *) echo "schemaviz: no binary for $os. Use: uv tool install git+https://github.com/$REPO#subdirectory=skills/schemaviz" >&2; exit 1 ;; esac
case "$arch" in x86_64 | amd64) arch=x64 ;; arm64 | aarch64) arch=arm64 ;; *) echo "schemaviz: no binary for $arch" >&2; exit 1 ;; esac
asset="schemaviz-$os-$arch"

if [ -z "${VERSION:-}" ] && [ -z "${SCHEMAVIZ_BASE_URL:-}" ]; then
  VERSION=$(curl -fsSL "https://api.github.com/repos/$REPO/releases?per_page=100" |
    sed -n 's/.*"tag_name": *"schemaviz-v\([^"]*\)".*/\1/p' | head -n 1)
  [ -n "$VERSION" ] || { echo "schemaviz: no release found" >&2; exit 1; }
fi
base="${SCHEMAVIZ_BASE_URL:-https://github.com/$REPO/releases/download/schemaviz-v$VERSION}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
echo "schemaviz: downloading $asset ${VERSION:+v$VERSION}"
curl -fsSL "$base/$asset" -o "$tmp/$asset"
curl -fsSL "$base/SHA256SUMS" -o "$tmp/SHA256SUMS"

want=$(awk -v f="$asset" '$2 == f || $2 == "*" f {print $1}' "$tmp/SHA256SUMS")
[ -n "$want" ] || { echo "schemaviz: $asset is not listed in SHA256SUMS" >&2; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then got=$(sha256sum "$tmp/$asset" | awk '{print $1}'); else got=$(shasum -a 256 "$tmp/$asset" | awk '{print $1}'); fi
[ "$got" = "$want" ] || { echo "schemaviz: checksum mismatch, not installing" >&2; exit 1; }

mkdir -p "$INSTALL_DIR"
install -m 755 "$tmp/$asset" "$INSTALL_DIR/schemaviz"
echo "schemaviz: installed $("$INSTALL_DIR/schemaviz" --version) to $INSTALL_DIR/schemaviz"
case ":$PATH:" in *":$INSTALL_DIR:"*) ;; *) echo "schemaviz: add $INSTALL_DIR to your PATH" ;; esac
