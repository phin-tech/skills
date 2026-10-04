#!/bin/sh
# Build the sample pages that are published to GitHub Pages.   sh examples/build-site.sh <out-dir>
# The inputs sit next to this script, so the pages can be rebuilt at any time.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
out=${1:?usage: build-site.sh <out-dir>}
sv="python3 $here/../schemaviz.py"
mkdir -p "$out"
$sv diff "$here/recipe-v1.dbml" "$here/recipe-v2.dbml" --rename users=accounts --rename accounts.created_at=joined_at \
  --old-label "example v1" --new-label "example v2" --title "Recipe app migration" --out "$out/recipe-diff.html"
$sv render "$here/jaffle_shop.dbml" --title "Jaffle Shop (dbt)" --out "$out/jaffle-shop.html"
cp "$here/index.html" "$out/index.html"
touch "$out/.nojekyll"
