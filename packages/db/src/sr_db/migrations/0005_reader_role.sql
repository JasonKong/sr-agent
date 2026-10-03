-- Read-only access for the MCP server (DESIGN.md sections 7.2 and 8).
--
-- sr_reader is a group role: it cannot log in. `sr-db create-reader` creates the login role
-- the MCP server uses, as a member of sr_reader. Privacy is enforced here, by the database:
-- the role cannot read patient names, NHIs or dates of birth, only whether they are present.
-- Every new table needs an explicit grant; a test fails if one is missing.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sr_reader') THEN
        CREATE ROLE sr_reader NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA composer, hub, knowledge TO sr_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA hub, knowledge TO sr_reader;
GRANT SELECT ON
    composer.gp_practice,
    composer.gp_user,
    composer.pms_connection,
    composer.referral,
    composer.referral_event,
    composer.source_document,
    composer.hub_send,
    composer.inbound_response
TO sr_reader;
GRANT SELECT (id, practice_id) ON composer.patient_snapshot TO sr_reader;

-- Whether each required patient field is present, empty or invalid, without the value.
-- SECURITY DEFINER runs it with the owner's rights; the fixed search_path stops a caller
-- from substituting their own functions.
CREATE FUNCTION composer.patient_field_status(patient text)
RETURNS TABLE (synthetic_nhi text, date_of_birth text)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT
        CASE
            WHEN p.synthetic_nhi IS NULL OR p.synthetic_nhi = '' THEN 'empty'
            WHEN p.synthetic_nhi ~ '^[A-Z]{3}[0-9]{4}$' THEN 'present'
            ELSE 'invalid'
        END,
        CASE
            WHEN p.date_of_birth IS NULL OR p.date_of_birth = '' THEN 'empty'
            WHEN p.date_of_birth ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4}$' THEN 'present'
            ELSE 'invalid'
        END
    FROM composer.patient_snapshot p
    WHERE p.id = patient
$$;

REVOKE ALL ON FUNCTION composer.patient_field_status(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION composer.patient_field_status(text) TO sr_reader;
