# Databricks notebook source
import re

dbutils.widgets.text("catalog", "serverless_stable_vfpvf8_catalog")
dbutils.widgets.text("schema", "object_layer")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

for label, value in (("catalog", catalog), ("schema", schema)):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Unsafe {label}: {value!r}")

namespace = f"`{catalog}`.`{schema}`"

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {namespace}")

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {namespace}.employee_base (
      employee_id STRING NOT NULL,
      first_name  STRING,
      department  STRING,
      hire_date   DATE,
      salary      DECIMAL(12,2),
      status      STRING,
      _etl_run_id STRING,
      _etl_ts     TIMESTAMP
    ) USING DELTA
    TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
    """
)

spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {namespace}.employee_app_changes (
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
    TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')
    """
)

# A plain view is deliberate for the POC: it is always current and avoids a
# separate materialized-view refresh pipeline while preserving GOLD semantics.
spark.sql(
    f"""
    CREATE OR REPLACE VIEW {namespace}.employee_gold AS
    SELECT
      COALESCE(a.employee_id, b.employee_id) AS employee_id,
      COALESCE(a.first_name,  b.first_name)  AS first_name,
      COALESCE(a.department,  b.department)  AS department,
      COALESCE(a.hire_date,   b.hire_date)   AS hire_date,
      COALESCE(a.salary,      b.salary)      AS salary,
      COALESCE(a.status,      b.status)      AS status,
      (COALESCE(a.salary, b.salary) > 200000) AS is_high_risk
    FROM {namespace}.employee_base b
    FULL OUTER JOIN {namespace}.employee_app_changes a
      ON b.employee_id = a.employee_id
    WHERE COALESCE(a.is_deleted, false) = false
    """
)

for table_name in ("employee_base", "employee_app_changes"):
    properties = spark.sql(f"SHOW TBLPROPERTIES {namespace}.{table_name}").collect()
    cdf = {row.key: row.value for row in properties}.get("delta.enableChangeDataFeed")
    if cdf != "true":
        raise RuntimeError(f"CDF is not enabled on {namespace}.{table_name}")

display(spark.sql(f"SHOW TABLES IN {namespace}"))
