\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS vector;
-- Bootstrap administrator only. Create separate least-privilege app roles and
-- a separate Langflow database before connecting either application.
