GRANT CONNECT ON DATABASE ontology_poc
  TO "f381b3a3-d440-424a-b18d-806fb4496d1a";
GRANT USAGE ON SCHEMA object_layer, public
  TO "f381b3a3-d440-424a-b18d-806fb4496d1a";
GRANT SELECT ON TABLE object_layer.employee_sync
  TO "f381b3a3-d440-424a-b18d-806fb4496d1a";
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.employee_write
  TO "f381b3a3-d440-424a-b18d-806fb4496d1a";
GRANT SELECT ON TABLE public.employee_overlay
  TO "f381b3a3-d440-424a-b18d-806fb4496d1a";
