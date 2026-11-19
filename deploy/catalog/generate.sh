#!/usr/bin/env bash
# One-use builder; only the generated JSON leaves the disposable container.
set -euo pipefail
umask 077
readonly commit=b0602a175c1d08959b7187da2b29ac450b52e0ba
readonly work="$(mktemp -d /tmp/jeju-catalog.XXXXXXXX)"
mkdir -p "$work/.local/source" "$work/.local/app"
git -C "$work/.local/source" init --quiet
git -C "$work/.local/source" remote add origin https://github.com/OREUM-IT/jeju-tour.git
git -C "$work/.local/source" fetch --quiet --depth 1 origin "$commit"
git -C "$work/.local/source" checkout --quiet --detach "$commit"
git -C "$work/.local/source" archive "$commit" prototype | tar -x -C "$work/.local/app"
cd "$work/.local/app/prototype"
npm ci --ignore-scripts --no-audit --no-fund --cache "$work/npm-cache"
./node_modules/.bin/esbuild src/server.ts --bundle --platform=node --format=cjs --packages=external --target=node22 --outfile=dist/server.js
node scripts/copy-migrations.cjs
node scripts/copy-public-service.cjs
cd /builder/services/jeju-mcp
node scripts/dump-app-places.mjs --app-dir "$work/.local/app/prototype" --output "$work/.local/app-places.json"
node scripts/export-agent-catalog.mjs \
  --places "$work/.local/app-places.json" \
  --raw "$work/.local/source/prototype/data/jeju_tour_data.json" \
  --commit "$commit" \
  --output /output/.local/agent-catalog-b0602a1.json
