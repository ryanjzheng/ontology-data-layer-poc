CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.employee_app_changes (
  employee_id    STRING NOT NULL,
  first_name     STRING,
  department     STRING,
  hire_date      DATE,
  salary         DECIMAL(12,2),
  status         STRING,
  is_new         BOOLEAN NOT NULL,
  is_deleted     BOOLEAN NOT NULL,
  editor         STRING,
  src_updated_at TIMESTAMP,
  synced_at      TIMESTAMP NOT NULL
) USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
