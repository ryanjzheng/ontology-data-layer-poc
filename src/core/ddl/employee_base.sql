CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.employee_base (
  employee_id STRING NOT NULL,
  first_name  STRING,
  department  STRING,
  hire_date   DATE,
  salary      DECIMAL(12,2),
  status      STRING,
  _etl_run_id STRING,
  _etl_ts     TIMESTAMP
) USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');
