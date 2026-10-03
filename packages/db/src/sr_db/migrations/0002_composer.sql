-- Composer: the GP-side system (DESIGN.md sections 3.1 and 3.3).
-- Value lists match sr_db.domain; ids carry the table prefix from sr_db.domain.ID_PREFIX.
-- Timestamps have no defaults: the simulator sets every time from its anchor time.
CREATE SCHEMA composer;

CREATE TABLE composer.gp_practice (
    id          text PRIMARY KEY CHECK (id ~ '^prc_[0-9]+$'),
    name        text NOT NULL,
    pms_vendor  text NOT NULL CHECK (pms_vendor IN ('KAURI_PMS', 'HARBOUR_PMS', 'SUMMIT_PMS')),
    status      text NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
    created_at  timestamptz NOT NULL
);

CREATE TABLE composer.gp_user (
    id           text PRIMARY KEY CHECK (id ~ '^gpu_[0-9]+$'),
    practice_id  text NOT NULL REFERENCES composer.gp_practice (id),
    name         text NOT NULL,
    role         text NOT NULL CHECK (role IN ('GP', 'NURSE_PRACTITIONER'))
);
CREATE INDEX ON composer.gp_user (practice_id);

-- The practice's connection to its own PMS, used to fetch documents and file responses.
-- The vendor is the practice's pms_vendor.
CREATE TABLE composer.pms_connection (
    id                text PRIMARY KEY CHECK (id ~ '^pmc_[0-9]+$'),
    practice_id       text NOT NULL UNIQUE REFERENCES composer.gp_practice (id),
    auth_status       text NOT NULL CHECK (auth_status IN ('VALID', 'EXPIRED', 'REVOKED')),
    token_expires_at  timestamptz,
    updated_at        timestamptz NOT NULL
);

-- Patient details as received from the GP PMS for one referral. Synthetic only, and never
-- returned unmasked by the MCP server. date_of_birth is the raw text the PMS sent, so it
-- can be empty or malformed (F01).
CREATE TABLE composer.patient_snapshot (
    id             text PRIMARY KEY CHECK (id ~ '^pt_[0-9]+$'),
    practice_id    text NOT NULL REFERENCES composer.gp_practice (id),
    synthetic_nhi  text,
    given_name     text NOT NULL,
    family_name    text NOT NULL,
    date_of_birth  text
);
CREATE INDEX ON composer.patient_snapshot (practice_id);

-- recipient_*_ref and hub_referral_id point into the Hub by id only. They are not foreign
-- keys: the two systems are separate, and the link can be missing (for example F04).
CREATE TABLE composer.referral (
    id                        text PRIMARY KEY CHECK (id ~ '^GR-[0-9]+$'),
    practice_id               text NOT NULL REFERENCES composer.gp_practice (id),
    gp_user_id                text NOT NULL REFERENCES composer.gp_user (id),
    patient_id                text NOT NULL REFERENCES composer.patient_snapshot (id),
    recipient_clinic_ref      text NOT NULL,
    recipient_specialist_ref  text,
    correlation_id            text NOT NULL,
    status                    text NOT NULL CHECK (status IN (
                                  'DRAFT', 'SUBMITTED', 'GENERATED', 'SENT', 'RECEIVED',
                                  'ACKNOWLEDGED', 'DECLINED', 'ERROR')),
    error_level               text NOT NULL CHECK (error_level IN ('NONE', 'NON_CRITICAL', 'CRITICAL')),
    hub_referral_id           text,
    pdf_size_bytes            bigint CHECK (pdf_size_bytes >= 0),
    created_at                timestamptz NOT NULL,
    submitted_at              timestamptz,
    sent_at                   timestamptz
);
CREATE INDEX ON composer.referral (practice_id);
CREATE INDEX ON composer.referral (gp_user_id);
CREATE INDEX ON composer.referral (patient_id);
CREATE INDEX ON composer.referral (correlation_id);
CREATE INDEX ON composer.referral (hub_referral_id);
CREATE INDEX ON composer.referral (recipient_clinic_ref, created_at);
CREATE INDEX ON composer.referral (status, created_at);

-- Timeline of everything Composer did with a referral. `event` is an open vocabulary
-- (scenarios add their own), so only its format is checked.
CREATE TABLE composer.referral_event (
    id           text PRIMARY KEY CHECK (id ~ '^rev_[0-9]+$'),
    referral_id  text NOT NULL REFERENCES composer.referral (id),
    event        text NOT NULL CHECK (event ~ '^[A-Z][A-Z0-9_]*$'),
    sub_type     text,
    level        text NOT NULL CHECK (level IN ('INFO', 'WARN', 'ERROR')),
    detail       text,
    occurred_at  timestamptz NOT NULL
);
CREATE INDEX ON composer.referral_event (referral_id, occurred_at);

-- Documents pulled from the GP PMS into the referral PDF.
CREATE TABLE composer.source_document (
    id            text PRIMARY KEY CHECK (id ~ '^doc_[0-9]+$'),
    referral_id   text NOT NULL REFERENCES composer.referral (id),
    title         text NOT NULL,
    size_bytes    bigint NOT NULL CHECK (size_bytes >= 0),
    fetch_status  text NOT NULL CHECK (fetch_status IN ('PENDING', 'FETCHED', 'FAILED')),
    fetch_error   text
);
CREATE INDEX ON composer.source_document (referral_id);

-- Hand-off attempts to the Hub. A NULL http_status means no response (for example a timeout).
CREATE TABLE composer.hub_send (
    id             text PRIMARY KEY CHECK (id ~ '^hs_[0-9]+$'),
    referral_id    text NOT NULL REFERENCES composer.referral (id),
    attempt_no     integer NOT NULL CHECK (attempt_no > 0),
    http_status    integer,
    error_message  text,
    attempted_at   timestamptz NOT NULL,
    UNIQUE (referral_id, attempt_no)
);

-- Specialist responses received from the Hub, and whether they reached the GP PMS inbox.
-- hub_action_ref points to hub.recipient_action by id only.
CREATE TABLE composer.inbound_response (
    id                   text PRIMARY KEY CHECK (id ~ '^ir_[0-9]+$'),
    referral_id          text NOT NULL REFERENCES composer.referral (id),
    hub_action_ref       text,
    type                 text NOT NULL CHECK (type IN ('VIEWED', 'ACKNOWLEDGED', 'DECLINED', 'MESSAGE')),
    received_at          timestamptz NOT NULL,
    inbox_filing_status  text NOT NULL CHECK (inbox_filing_status IN ('PENDING', 'FILED', 'FAILED', 'SUPPRESSED')),
    filing_error         text
);
CREATE INDEX ON composer.inbound_response (referral_id);
