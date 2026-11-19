#!/usr/bin/env bash
# Run with Docker access: sudo bash deploy/prepare-catalog.sh
set -euo pipefail
umask 077
repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"
base="$repo/deploy/.local"
test -d "$base" && test ! -L "$base" || { echo 'Run python3 deploy/prepare.py first.'; exit 1; }
image='node:24.22.1-bookworm@sha256:d862bb5d082c5ae0210ac07e8c0344cb8cf7bef6d78e9b8c50c179da923108ea'
commit=b0602a175c1d08959b7187da2b29ac450b52e0ba

# Mount only public tool source and metadata, never .env or the whole checkout.
mounts=(--mount "type=bind,src=$repo/services/jeju-mcp/src,dst=/tool/services/jeju-mcp/src,readonly"
  --mount "type=bind,src=$repo/services/jeju-mcp/scripts,dst=/tool/services/jeju-mcp/scripts,readonly"
  --mount "type=bind,src=$repo/deploy/catalog,dst=/tool/deploy/catalog,readonly"
  --mount "type=bind,src=$repo/data/theme-category-map.json,dst=/tool/data/theme-category-map.json,readonly"
  --mount "type=bind,src=$repo/data/persona-prototypes.json,dst=/tool/data/persona-prototypes.json,readonly")
runtime=(--rm --read-only --cap-drop ALL --security-opt no-new-privileges
  --user "$(id -u):$(id -g)" --memory 3g --cpus 2 --pids-limit 256
  --tmpfs /tmp:rw,size=512m,mode=1777)

if test -e "$base/catalog" || test -L "$base/catalog"; then
  test -d "$base/catalog" && test ! -L "$base/catalog" || { echo 'Unsafe catalog path.'; exit 1; }
  docker run "${runtime[@]}" --network none "${mounts[@]}" \
    --mount "type=bind,src=$base/catalog,dst=/result,readonly" "$image" \
    node /tool/deploy/catalog/verify.mjs /result/agent-catalog.json
  echo 'Existing catalog preserved; no overwrite or credential rotation.'
  exit 0
fi

# A failed attempt is preserved for diagnosis; retries use a fresh private folder.
work=$(mktemp -d "$base/catalog-build.XXXXXXXX")
echo 'Fetching pinned reference source into a private scratch copy.'
git init -q "$work/source"
git -C "$work/source" remote add origin https://github.com/OREUM-IT/jeju-tour.git
git -C "$work/source" fetch --depth 1 origin "$commit"
git -C "$work/source" checkout --detach -q FETCH_HEAD
test "$(git -C "$work/source" rev-parse HEAD)" = "$commit"
mkdir "$work/app" "$work/output"
git -C "$work/source" archive "$commit" prototype | tar -x -C "$work/app"

# Preserve Docker's configured container proxy; the host's proxy hostname may
# not resolve inside a container. Do not replace those Docker-supplied values.
network_env=()
# Optional managed-environment CA, kept out of images and source files.
if test -n "${NODE_EXTRA_CA_CERTS:-}"; then
  test -f "$NODE_EXTRA_CA_CERTS"
  network_env+=(--mount "type=bind,src=$NODE_EXTRA_CA_CERTS,dst=/run/build-ca.pem,readonly"
    -e NODE_EXTRA_CA_CERTS=/run/build-ca.pem)
fi
docker run "${runtime[@]}" "${network_env[@]}" -e npm_config_cache=/tmp/npm-cache \
  --mount "type=bind,src=$work,dst=/work" "${mounts[@]}" "$image" \
  bash /tool/deploy/catalog/build-app.sh

echo 'Exporting through loopback with outbound network disabled; no model credentials supplied.'
docker run "${runtime[@]}" --network none --mount "type=bind,src=$work,dst=/work" \
  "${mounts[@]}" "$image" bash -eu -c '
  # Both scripts require a .local output path; keep that boundary in the container too.
  mkdir /work/output/.local
  node /tool/services/jeju-mcp/scripts/dump-app-places.mjs --app-dir /work/app/prototype --output /work/output/.local/app-places.json
  node /tool/services/jeju-mcp/scripts/export-agent-catalog.mjs --places /work/output/.local/app-places.json --raw /work/source/prototype/data/jeju_tour_data.json --commit b0602a175c1d08959b7187da2b29ac450b52e0ba --output /work/output/.local/agent-catalog.json --report /work/output/.local/coverage.json
  node /tool/deploy/catalog/verify.mjs /work/output/.local/agent-catalog.json
  '

# Parent .local stays 0700. Only this non-secret file is readable by container UID1000.
chmod 0644 "$work/output/.local/agent-catalog.json"
test ! -e "$base/catalog" && test ! -L "$base/catalog"
mv -T -n "$work/output/.local" "$base/catalog"
echo 'PASS: real catalog prepared in deploy/.local/catalog. No server or database was changed.'
