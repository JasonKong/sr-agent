-- Knowledge: past support tickets and runbooks, the sources for RAG (DESIGN.md section 7.3).
-- knowledge_chunk (with embeddings) arrives in T4.1, once the embedding model is chosen.
CREATE SCHEMA knowledge;

-- referral_ref and clinic_ref may name a Composer or Hub id, or nothing; tickets are free text.
CREATE TABLE knowledge.support_ticket (
    id               text PRIMARY KEY CHECK (id ~ '^tkt_[0-9]+$'),
    referral_ref     text,
    clinic_ref       text,
    title            text NOT NULL,
    description      text NOT NULL,
    resolution       text,
    root_cause_code  text CHECK (root_cause_code IN (
                         'F00_NO_FAULT', 'F01_MISSING_REQUIRED_FIELD', 'F02_PDF_TOO_LARGE',
                         'F03_SOURCE_DOC_SKIPPED', 'F04_HUB_UNAVAILABLE',
                         'F05_ATTACHMENT_FETCH_FAILED', 'F06_MANUAL_ROUTING',
                         'F07_CLINIC_PMS_AUTH_FAILED', 'F08_CLINIC_PMS_TIMEOUT',
                         'F09_CLINIC_IMPORT_REJECTED', 'F10_DELIVERED_NOT_ACTIONED',
                         'F11_RESPONSE_NOT_FILED')),
    created_at       timestamptz NOT NULL,
    resolved_at      timestamptz
);
CREATE INDEX ON knowledge.support_ticket (root_cause_code);

-- One runbook per failure code, plus general guides (error_code NULL).
CREATE TABLE knowledge.runbook (
    id             text PRIMARY KEY CHECK (id ~ '^rb_[0-9]+$'),
    slug           text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    error_code     text UNIQUE CHECK (error_code IN (
                       'F00_NO_FAULT', 'F01_MISSING_REQUIRED_FIELD', 'F02_PDF_TOO_LARGE',
                       'F03_SOURCE_DOC_SKIPPED', 'F04_HUB_UNAVAILABLE',
                       'F05_ATTACHMENT_FETCH_FAILED', 'F06_MANUAL_ROUTING',
                       'F07_CLINIC_PMS_AUTH_FAILED', 'F08_CLINIC_PMS_TIMEOUT',
                       'F09_CLINIC_IMPORT_REJECTED', 'F10_DELIVERED_NOT_ACTIONED',
                       'F11_RESPONSE_NOT_FILED')),
    title          text NOT NULL,
    body_markdown  text NOT NULL,
    updated_at     timestamptz NOT NULL
);
