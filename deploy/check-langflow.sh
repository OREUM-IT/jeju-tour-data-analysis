#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
compose=(docker compose -f compose.yaml -f compose.catalog.yaml -f compose.langflow.yaml)
"${compose[@]}" exec -T langflow python /opt/oreumit/check-ssrf.py
"${compose[@]}" exec -T langflow python /opt/oreumit/bootstrap.py
"${compose[@]}" exec -T langflow-db sh -eu -c 'PGPASSWORD="$LANGFLOW_DB_PASSWORD" psql -h 127.0.0.1 -U langflow_app -d langflow -v ON_ERROR_STOP=1' <<'SQL'
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication)) THEN
    RAISE EXCEPTION 'Langflow application role has unexpected privileges';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='flow') THEN
    RAISE EXCEPTION 'Langflow migrations or flow table are missing';
  END IF;
END $$;
SQL
echo 'PASS: private Langflow authentication, dedicated restricted DB role, MCP tools and imported flow checked. Model execution, Gateway, Tunnel and Vercel remain separate.'
