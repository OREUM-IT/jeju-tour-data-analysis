# Upstream pgvector image, PG17; retrieved and smoke-tested 2026-11-19.
FROM pgvector/pgvector:pg17@sha256:b513c48566e4777cd3370451400000c78eda6a08131d68f4c31d4a76f27bd8fd
COPY --chmod=0644 init-db.sql /docker-entrypoint-initdb.d/010-vector.sql
