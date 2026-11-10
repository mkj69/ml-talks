#!/usr/bin/env bash
# Preview locally: assembles site/ + data/ into _site/ and serves it.
set -e
cd "$(dirname "$0")"
rm -rf _site && mkdir -p _site/data
cp -r site/* _site/
cp -r data/manifest.json data/papers data/abstracts _site/data/
cd _site && python3 -m http.server "${1:-8000}" --bind 0.0.0.0
