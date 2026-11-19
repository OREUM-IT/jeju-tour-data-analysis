#!/usr/bin/env bash
# One-use builder; only the generated JSON leaves the disposable container.
set -euo pipefail
umask 077
readonly commit=b0602a175c1d08959b7187da2b29ac450b52e0ba
readonly work="$(mktemp -d /tmp/jeju-catalog.XXXXXXXX)"
mkdir -p "$work/.local/app"
if [[ -d /source/.git ]]; then
  source_repo=/source
else
  source_repo="$work/.local/source"
  mkdir "$source_repo"
  git -C "$source_repo" init --quiet
  git -C "$source_repo" remote add origin https://github.com/OREUM-IT/jeju-tour.git
  GIT_TERMINAL_PROMPT=0 git -C "$source_repo" fetch --quiet --depth 1 origin "$commit"
  git -C "$source_repo" checkout --quiet --detach "$commit"
fi
git -C "$source_repo" archive "$commit" prototype | tar -x -C "$work/.local/app"
cd "$work/.local/app/prototype"
npm ci --ignore-scripts --no-audit --no-fund --cache "$work/npm-cache"
./node_modules/.bin/esbuild src/server.ts --bundle --platform=node --format=cjs --packages=external --target=node22 --outfile=dist/server.js
node scripts/copy-migrations.cjs
node scripts/copy-public-service.cjs
cd /builder/services/jeju-mcp
node scripts/dump-app-places.mjs --app-dir "$work/.local/app/prototype" --output "$work/.local/app-places.json"
node scripts/export-agent-catalog.mjs \
  --places "$work/.local/app-places.json" \
  --raw "$source_repo/prototype/data/jeju_tour_data.json" \
  --commit "$commit" \
  --output /output/.local/agent-catalog-b0602a1.json
