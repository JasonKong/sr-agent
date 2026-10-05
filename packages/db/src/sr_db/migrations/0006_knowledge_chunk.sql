-- Chunks of past tickets and runbooks for retrieval (DESIGN.md section 7.3, T4.1).
--
-- Written by `sr-ingest`, which masks patient identifiers before anything is embedded or
-- stored. The vector size is fixed by the embedding model: 384 for the default local model
-- (bge-small-en-v1.5). `content_hash` covers the text and the embedder, so re-ingesting
-- embeds only what changed. Chunk ids are `<source id>#<chunk number>`, such as tkt_42#0.
CREATE TABLE knowledge.knowledge_chunk (
    id            text PRIMARY KEY CHECK (id ~ '^(tkt|rb)_[0-9]+#[0-9]+$'),
    source_type   text NOT NULL CHECK (source_type IN ('TICKET', 'RUNBOOK')),
    source_id     text NOT NULL,
    chunk_no      integer NOT NULL CHECK (chunk_no >= 0),
    error_code    text CHECK (error_code IN (
                      'F00_NO_FAULT', 'F01_MISSING_REQUIRED_FIELD', 'F02_PDF_TOO_LARGE',
                      'F03_SOURCE_DOC_SKIPPED', 'F04_HUB_UNAVAILABLE',
                      'F05_ATTACHMENT_FETCH_FAILED', 'F06_MANUAL_ROUTING',
                      'F07_CLINIC_PMS_AUTH_FAILED', 'F08_CLINIC_PMS_TIMEOUT',
                      'F09_CLINIC_IMPORT_REJECTED', 'F10_DELIVERED_NOT_ACTIONED',
                      'F11_RESPONSE_NOT_FILED')),
    title         text NOT NULL,
    text          text NOT NULL,
    metadata      jsonb NOT NULL,
    content_hash  text NOT NULL,
    embedder      text NOT NULL,
    embedding     vector(384) NOT NULL,
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || text)) STORED,
    ingested_at   timestamptz NOT NULL,
    UNIQUE (source_id, chunk_no)
);
CREATE INDEX ON knowledge.knowledge_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON knowledge.knowledge_chunk USING gin (tsv);
CREATE INDEX ON knowledge.knowledge_chunk (error_code);

-- The MCP server searches chunks; the text is already masked.
GRANT SELECT ON knowledge.knowledge_chunk TO sr_reader;
