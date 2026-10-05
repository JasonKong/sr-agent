-- Audit of every diagnosis, and the CS team's feedback on it (DESIGN.md section 7.4, T5.3).
--
-- Written by the agent through the sr_auditor role, which can only insert into and read
-- these tables. The MCP server's sr_reader role has no access. Questions are stored after
-- the agent has masked patient identifiers in them, and diagnoses after their own masking.
CREATE SCHEMA audit;

CREATE TABLE audit.diagnosis (
    id                    text PRIMARY KEY CHECK (id ~ '^dx_[0-9a-f]{12}$'),
    user_ref              text NOT NULL,
    question              text NOT NULL,
    model                 text NOT NULL,
    served_by             jsonb NOT NULL,
    prompt_version        text NOT NULL,
    outcome               text NOT NULL CHECK (outcome IN (
                              'answered', 'tool_limit', 'time_limit', 'token_limit',
                              'max_tokens', 'refused', 'invalid_output', 'unexpected_stop')),
    diagnosis             jsonb,
    tool_calls            jsonb NOT NULL,
    turns                 integer NOT NULL,
    repairs               integer NOT NULL,
    dropped_evidence      jsonb NOT NULL,
    question_redactions   integer NOT NULL,
    output_redactions     integer NOT NULL,
    safety_flags          jsonb NOT NULL,
    input_tokens          integer NOT NULL,
    cache_write_tokens    integer NOT NULL,
    cache_read_tokens     integer NOT NULL,
    output_tokens         integer NOT NULL,
    turn_usage            jsonb NOT NULL,  -- per turn: the serving model and its tokens
    cost_usd              numeric(12, 6),
    latency_ms            numeric(12, 1) NOT NULL,
    created_at            timestamptz NOT NULL
);
CREATE INDEX ON audit.diagnosis (user_ref, created_at DESC);

CREATE TABLE audit.feedback (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    diagnosis_id   text NOT NULL REFERENCES audit.diagnosis (id),
    user_ref       text NOT NULL,
    rating         text NOT NULL CHECK (rating IN ('UP', 'DOWN')),
    correct_code   text CHECK (correct_code IN (
                       'F00_NO_FAULT', 'F01_MISSING_REQUIRED_FIELD', 'F02_PDF_TOO_LARGE',
                       'F03_SOURCE_DOC_SKIPPED', 'F04_HUB_UNAVAILABLE',
                       'F05_ATTACHMENT_FETCH_FAILED', 'F06_MANUAL_ROUTING',
                       'F07_CLINIC_PMS_AUTH_FAILED', 'F08_CLINIC_PMS_TIMEOUT',
                       'F09_CLINIC_IMPORT_REJECTED', 'F10_DELIVERED_NOT_ACTIONED',
                       'F11_RESPONSE_NOT_FILED')),
    comment        text,
    created_at     timestamptz NOT NULL
);
CREATE INDEX ON audit.feedback (diagnosis_id);

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sr_auditor') THEN
        CREATE ROLE sr_auditor NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA audit TO sr_auditor;
GRANT SELECT, INSERT ON audit.diagnosis, audit.feedback TO sr_auditor;
