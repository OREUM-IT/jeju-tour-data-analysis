#!/usr/bin/env bash
set -euo pipefail
cd /work/app/prototype
npm ci --ignore-scripts --no-audit --no-fund --fetch-retries=1 --fetch-timeout=30000
./node_modules/.bin/esbuild src/server.ts --bundle --platform=node --format=cjs --packages=external --target=node22 --outfile=dist/server.js
node scripts/copy-migrations.cjs
node scripts/copy-public-service.cjs
