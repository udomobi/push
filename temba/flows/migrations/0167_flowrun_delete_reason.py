# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-06-06 18:28
from __future__ import unicode_literals

from django.db import migrations, models

SQL_update_triggers = """
----------------------------------------------------------------------
-- Handles changes relating to a flow run's path
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_flowrun_path_change() RETURNS TRIGGER AS $$
DECLARE
  p INT;
  _old_is_active BOOL;
  _old_path TEXT;
  _new_path TEXT;
  _old_path_json JSONB;
  _new_path_json JSONB;
  _old_path_len INT;
  _new_path_len INT;
BEGIN
  -- Handles one of the following changes to a flow run:
  --  1. flow path unchanged and is_active becomes false (run interrupted or expired)
  --  2. flow path added to and is_active becomes false (run completed)
  --  3. flow path added to and is_active remains true (run continues)
  --  4. deletion
  --

  -- restrict changes to runs
  IF TG_OP = 'UPDATE' THEN
    IF NEW.is_active AND NOT OLD.is_active THEN RAISE EXCEPTION 'Cannot re-activate an inactive flow run'; END IF;

    -- TODO re-enable after migration to populate run events
    -- IF NOT OLD.is_active AND NEW.path != OLD.path THEN RAISE EXCEPTION 'Cannot modify path on an inactive flow run'; END IF;
  END IF;

  IF TG_OP = 'UPDATE' OR TG_OP = 'DELETE' THEN
    _old_is_active := OLD.is_active;
    _old_path := OLD.path;
  END IF;

  IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
    -- ignore test contacts
    IF temba_contact_is_test(NEW.contact_id) THEN RETURN NULL; END IF;

    _new_path := NEW.path;

    -- don't differentiate between empty array and NULL
    IF _old_path IS NULL THEN _old_path := '[]'; END IF;
    IF _new_path IS NULL THEN _new_path := '[]'; END IF;

    _old_path_json := _old_path::jsonb;
    _new_path_json := _new_path::jsonb;
    _old_path_len := jsonb_array_length(_old_path_json);
    _new_path_len := jsonb_array_length(_new_path_json);

    -- if there are no changes that effect path/node counts, bail
    IF TG_OP = 'UPDATE'
        AND _old_path_len = _new_path_len
        AND COALESCE(_old_path_json->(_old_path_len-1)->>'exit_uuid', '') = COALESCE(_new_path_json->(_new_path_len-1)->>'exit_uuid', '')
        AND NEW.is_active = OLD.is_active
    THEN
      RETURN NULL;
    END IF;

    -- we don't support rewinding run paths, so the new path must be longer than the old
    IF _new_path_len < _old_path_len THEN RAISE EXCEPTION 'Cannot rewind a flow run path'; END IF;

    -- update the node counts
    IF _old_path_len > 0 AND _old_is_active THEN
      PERFORM temba_insert_flownodecount(NEW.flow_id, UUID(_old_path_json->(_old_path_len-1)->>'node_uuid'), -1);
    END IF;

    IF _new_path_len > 0 AND NEW.is_active THEN
      PERFORM temba_insert_flownodecount(NEW.flow_id, UUID(_new_path_json->(_new_path_len-1)->>'node_uuid'), 1);
    END IF;

    -- if we have old steps, we start at the end of the old path
    IF _old_path_len > 0 THEN p := _old_path_len; ELSE p := 1; END IF;

    LOOP
      EXIT WHEN p >= _new_path_len;
      PERFORM temba_insert_flowpathcount(
          NEW.flow_id,
          UUID(_new_path_json->(p-1)->>'exit_uuid'),
          UUID(_new_path_json->p->>'node_uuid'),
          timestamptz(_new_path_json->p->>'arrived_on'),
          1
      );
      PERFORM temba_insert_flowpathrecentrun(
        UUID(_new_path_json->(p-1)->>'exit_uuid'),
        UUID(_new_path_json->(p-1)->>'uuid'),
        UUID(_new_path_json->p->>'node_uuid'),
        UUID(_new_path_json->p->>'uuid'),
        NEW.id,
        timestamptz(_new_path_json->p->>'arrived_on')
      );
      p := p + 1;
    END LOOP;

  ELSIF TG_OP = 'DELETE' THEN
    IF OLD.delete_reason = 'A' OR temba_contact_is_test(OLD.contact_id) THEN
      RETURN NULL;
    END IF;

    -- do nothing if path was empty
    IF _old_path IS NULL OR _old_path = '[]' THEN RETURN NULL; END IF;

    -- parse path as JSON
    _old_path_json := _old_path::json;
    _old_path_len := jsonb_array_length(_old_path_json);

    -- decrement node count at last node in this path if this was an active run
    IF _old_is_active THEN
      PERFORM temba_insert_flownodecount(OLD.flow_id, UUID(_old_path_json->(_old_path_len-1)->>'node_uuid'), -1);
    END IF;

    -- decrement all path counts
    p := 1;
    LOOP
      EXIT WHEN p >= _old_path_len;

      -- it's possible that steps from old flows don't have exit_uuid
      IF (_old_path_json->(p-1)->'exit_uuid') IS NOT NULL THEN
        PERFORM temba_insert_flowpathcount(
          OLD.flow_id,
          UUID(_old_path_json->(p-1)->>'exit_uuid'),
          UUID(_old_path_json->p->>'node_uuid'),
          timestamptz(_old_path_json->p->>'arrived_on'),
          -1
        );
      END IF;

      p := p + 1;
    END LOOP;
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS temba_flowrun_path_change on flows_flowrun;
CREATE TRIGGER temba_flowrun_path_change
  AFTER INSERT OR DELETE OR UPDATE OF path, is_active ON flows_flowrun
  FOR EACH ROW EXECUTE PROCEDURE temba_flowrun_path_change();

CREATE OR REPLACE FUNCTION temba_update_flowcategorycount() RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.delete_reason = 'A' OR temba_contact_is_test(OLD.contact_id) THEN
      RETURN NULL;
    END IF;

    EXECUTE temba_update_category_counts(OLD.flow_id, NULL, OLD.results::json);

    RETURN NULL;
  END IF;

  IF temba_contact_is_test(NEW.contact_id) THEN
    RETURN NULL;
  END IF;

  IF TG_OP = 'INSERT' THEN
    EXECUTE temba_update_category_counts(NEW.flow_id, NEW.results::json, NULL);

  ELSIF TG_OP = 'UPDATE' THEN
    -- use string comparison to check for no-change case
    IF NEW.results = OLD.results THEN RETURN NULL; END IF;

    EXECUTE temba_update_category_counts(NEW.flow_id, NEW.results::json, OLD.results::json);
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS temba_flowrun_update_flowcategorycount on flows_flowrun;
CREATE TRIGGER temba_flowrun_update_flowcategorycount
   AFTER INSERT OR DELETE OR UPDATE OF results
   ON flows_flowrun
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_flowcategorycount();

----------------------------------------------------------------------
-- Increments or decrements our counts for each exit type
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_update_flowruncount() RETURNS TRIGGER AS $$
BEGIN
  -- FlowRun being added
  IF TG_OP = 'INSERT' THEN
     -- Is this a test contact, ignore
     IF temba_contact_is_test(NEW.contact_id) THEN
       RETURN NULL;
     END IF;

    -- Increment appropriate type
    PERFORM temba_insert_flowruncount(NEW.flow_id, NEW.exit_type, 1);

  -- FlowRun being removed
  ELSIF TG_OP = 'DELETE' THEN
     -- Is this a test contact, ignore
     IF OLD.delete_reason = 'A' OR temba_contact_is_test(OLD.contact_id) THEN
       RETURN NULL;
     END IF;

    PERFORM temba_insert_flowruncount(OLD.flow_id, OLD.exit_type, -1);

  -- Updating exit type
  ELSIF TG_OP = 'UPDATE' THEN
     -- Is this a test contact, ignore
     IF temba_contact_is_test(NEW.contact_id) THEN
       RETURN NULL;
     END IF;

    PERFORM temba_insert_flowruncount(OLD.flow_id, OLD.exit_type, -1);
    PERFORM temba_insert_flowruncount(NEW.flow_id, NEW.exit_type, 1);
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS temba_flowrun_update_flowruncount on flows_flowrun;
CREATE TRIGGER temba_flowrun_update_flowruncount
   AFTER INSERT OR DELETE OR UPDATE OF exit_type
   ON flows_flowrun
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_flowruncount();

----------------------------------------------------------------------
-- Increments or decrements our start counts for each exit type
----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION temba_update_flowstartcount() RETURNS TRIGGER AS $$
BEGIN
  -- FlowRun being added
  IF TG_OP = 'INSERT' THEN
    PERFORM temba_insert_flowstartcount(NEW.start_id, 1);

  -- FlowRun being removed
  ELSIF TG_OP = 'DELETE' THEN
    IF OLD.delete_reason = 'A' THEN
      RETURN NULL;
    END IF;

    PERFORM temba_insert_flowstartcount(OLD.start_id, -1);

  -- Updating exit type
  ELSIF TG_OP = 'UPDATE' THEN
    PERFORM temba_insert_flowstartcount(OLD.start_id, -1);
    PERFORM temba_insert_flowstartcount(NEW.start_id, 1);
  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS temba_flowrun_update_flowstartcount on flows_flowrun;
CREATE TRIGGER temba_flowrun_update_flowstartcount
   AFTER INSERT OR DELETE OR UPDATE OF start_id
   ON flows_flowrun
   FOR EACH ROW
   EXECUTE PROCEDURE temba_update_flowstartcount();
"""


class Migration(migrations.Migration):

    dependencies = [("flows", "0166_auto_20180605_1546")]

    operations = [
        migrations.AddField(
            model_name="flowrun",
            name="delete_reason",
            field=models.CharField(
                choices=[(("A", "Archive delete"), ("U", "User delete"))],
                help_text="Why the run is being deleted",
                max_length=1,
                null=True,
            ),
        ),
        migrations.RunSQL(SQL_update_triggers),
    ]