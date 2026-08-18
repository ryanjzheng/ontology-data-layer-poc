-- Dedicated app SP: ontology-poc-app
-- Application ID: f381b3a3-d440-424a-b18d-806fb4496d1a
GRANT USE CATALOG ON CATALOG serverless_stable_vfpvf8_catalog
  TO `f381b3a3-d440-424a-b18d-806fb4496d1a`;
GRANT USE SCHEMA ON SCHEMA serverless_stable_vfpvf8_catalog.object_layer
  TO `f381b3a3-d440-424a-b18d-806fb4496d1a`;
GRANT SELECT ON TABLE serverless_stable_vfpvf8_catalog.object_layer.employee_gold
  TO `f381b3a3-d440-424a-b18d-806fb4496d1a`;
GRANT SELECT ON TABLE serverless_stable_vfpvf8_catalog.object_layer.employee_sync
  TO `f381b3a3-d440-424a-b18d-806fb4496d1a`;

-- Deliberately absent: SELECT or MODIFY on employee_base.
