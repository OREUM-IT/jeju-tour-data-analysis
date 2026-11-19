# Reuse the reviewed PG17 base, in a separate instance and volume.
# No tourism/vector data or bootstrap credentials are shared with Langflow.
FROM pgvector/pgvector:pg17@sha256:b513c48566e4777cd3370451400000c78eda6a08131d68f4c31d4a76f27bd8fd
COPY --chmod=0644 init-langflow.sql /docker-entrypoint-initdb.d/010-langflow.sql
