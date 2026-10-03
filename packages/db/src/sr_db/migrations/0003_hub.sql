-- Hub: the central referral platform (DESIGN.md sections 3.1 and 3.3).
-- Value lists match sr_db.domain; ids carry the table prefix from sr_db.domain.ID_PREFIX.
-- Timestamps have no defaults: the simulator sets every time from its anchor time.
CREATE SCHEMA hub;

-- Routing mode comes from has_active_account: without an active account, referrals are
-- routed as MANUAL and never delivered electronically (F06).
CREATE TABLE hub.clinic (
    id                  text PRIMARY KEY CHECK (id ~ '^cln_[0-9]+$'),
    name                text NOT NULL,
    specialty_group     text NOT NULL,
    status              text NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
    has_active_account  boolean NOT NULL,
    created_at          timestamptz NOT NULL
);

CREATE TABLE hub.specialist (
    id         text PRIMARY KEY CHECK (id ~ '^spc_[0-9]+$'),
    clinic_id  text NOT NULL REFERENCES hub.clinic (id),
    name       text NOT NULL,
    status     text NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE'))
);
CREATE INDEX ON hub.specialist (clinic_id);

-- A clinic's subscription to receive referrals in its own PMS.
CREATE TABLE hub.clinic_integration (
    id                text PRIMARY KEY CHECK (id ~ '^ci_[0-9]+$'),
    clinic_id         text NOT NULL REFERENCES hub.clinic (id),
    vendor            text NOT NULL CHECK (vendor IN ('KAURI_PMS', 'TASMAN_CLINIC', 'RIDGELINE')),
    auth_status       text NOT NULL CHECK (auth_status IN ('VALID', 'EXPIRED', 'REVOKED')),
    token_expires_at  timestamptz,
    endpoint_status   text NOT NULL CHECK (endpoint_status IN ('HEALTHY', 'DEGRADED', 'DOWN')),
    updated_at        timestamptz NOT NULL,
    UNIQUE (clinic_id, vendor)
);

-- source_ref points to composer.referral by id only (not a foreign key; see 0002).
-- accepted_at stays NULL until ingestion succeeds; routing_mode is NULL until routed.
CREATE TABLE hub.hub_referral (
    id              text PRIMARY KEY CHECK (id ~ '^HR-[0-9]+$'),
    source_ref      text NOT NULL,
    correlation_id  text NOT NULL,
    clinic_id       text NOT NULL REFERENCES hub.clinic (id),
    specialist_id   text REFERENCES hub.specialist (id),
    routing_mode    text CHECK (routing_mode IN ('ELECTRONIC', 'MANUAL')),
    status          text NOT NULL CHECK (status IN ('NEW', 'VIEWED', 'ACKNOWLEDGED', 'DECLINED', 'CANCELLED')),
    created_at      timestamptz NOT NULL,
    accepted_at     timestamptz
);
CREATE INDEX ON hub.hub_referral (clinic_id, created_at);
CREATE INDEX ON hub.hub_referral (specialist_id);
CREATE INDEX ON hub.hub_referral (source_ref);
CREATE INDEX ON hub.hub_referral (correlation_id);

-- The PDF and attachment bundle the Hub fetches from Composer during ingestion.
CREATE TABLE hub.hub_file (
    id               text PRIMARY KEY CHECK (id ~ '^hf_[0-9]+$'),
    hub_referral_id  text NOT NULL REFERENCES hub.hub_referral (id),
    kind             text NOT NULL CHECK (kind IN ('PDF', 'ATTACHMENTS')),
    fetch_status     text NOT NULL CHECK (fetch_status IN ('PENDING', 'FETCHED', 'FAILED')),
    size_bytes       bigint CHECK (size_bytes >= 0),
    fetch_error      text,
    updated_at       timestamptz NOT NULL
);
CREATE INDEX ON hub.hub_file (hub_referral_id);

-- One delivery per channel and target. target_ref is a clinic_integration id for CLINIC_PMS,
-- and a plain label for EMAIL and PORTAL.
CREATE TABLE hub.delivery (
    id               text PRIMARY KEY CHECK (id ~ '^dl_[0-9]+$'),
    hub_referral_id  text NOT NULL REFERENCES hub.hub_referral (id),
    channel          text NOT NULL CHECK (channel IN ('EMAIL', 'PORTAL', 'CLINIC_PMS')),
    target_ref       text NOT NULL,
    status           text NOT NULL CHECK (status IN ('PENDING', 'SUCCEEDED', 'FAILED')),
    last_error_code  text,
    updated_at       timestamptz NOT NULL,
    UNIQUE (hub_referral_id, channel, target_ref)
);

-- Each try of a delivery. A delivery can fail with no attempts at all, when it fails
-- before the request is sent (for example F07 authentication).
CREATE TABLE hub.delivery_attempt (
    id             text PRIMARY KEY CHECK (id ~ '^da_[0-9]+$'),
    delivery_id    text NOT NULL REFERENCES hub.delivery (id),
    attempt_no     integer NOT NULL CHECK (attempt_no > 0),
    http_status    integer,
    error_code     text,
    error_message  text,
    latency_ms     integer CHECK (latency_ms >= 0),
    attempted_at   timestamptz NOT NULL,
    UNIQUE (delivery_id, attempt_no)
);

CREATE TABLE hub.recipient_action (
    id               text PRIMARY KEY CHECK (id ~ '^ra_[0-9]+$'),
    hub_referral_id  text NOT NULL REFERENCES hub.hub_referral (id),
    action           text NOT NULL CHECK (action IN ('VIEWED', 'DOWNLOADED', 'ACKNOWLEDGED', 'DECLINED', 'MESSAGE')),
    actor_ref        text,
    occurred_at      timestamptz NOT NULL
);
CREATE INDEX ON hub.recipient_action (hub_referral_id, occurred_at);

-- Hub-to-Composer callbacks that carry recipient actions back to the GP side.
CREATE TABLE hub.return_callback (
    id               text PRIMARY KEY CHECK (id ~ '^rc_[0-9]+$'),
    hub_referral_id  text NOT NULL REFERENCES hub.hub_referral (id),
    action_id        text NOT NULL REFERENCES hub.recipient_action (id),
    attempt_no       integer NOT NULL CHECK (attempt_no > 0),
    http_status      integer,
    status           text NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED')),
    attempted_at     timestamptz NOT NULL,
    UNIQUE (action_id, attempt_no)
);
CREATE INDEX ON hub.return_callback (hub_referral_id);
