# Databricks notebook source
import re

import psycopg
from databricks.sdk import WorkspaceClient
from pyspark.sql.types import (
    BooleanType,
    DateType,
    DecimalType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

dbutils.widgets.text("catalog", "serverless_stable_vfpvf8_catalog")
dbutils.widgets.text("schema", "object_layer")
dbutils.widgets.text(
    "lakebase_endpoint",
    "projects/ontology-poc/branches/production/endpoints/primary",
)
dbutils.widgets.text("lakebase_database", "ontology_poc")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
endpoint_name = dbutils.widgets.get("lakebase_endpoint")
database = dbutils.widgets.get("lakebase_database")

for label, value in (("catalog", catalog), ("schema", schema), ("database", database)):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Unsafe {label}: {value!r}")
if not re.fullmatch(r"projects/[a-z0-9-]+/branches/[a-z0-9-]+/endpoints/[a-z0-9-]+", endpoint_name):
    raise ValueError(f"Unsafe Lakebase endpoint resource name: {endpoint_name!r}")

w = WorkspaceClient()
endpoint = w.postgres.get_endpoint(name=endpoint_name)
credential = w.postgres.generate_database_credential(endpoint=endpoint_name)
username = w.current_user.me().user_name

with psycopg.connect(
    host=endpoint.status.hosts.host,
    dbname=database,
    user=username,
    password=credential.token,
    sslmode="require",
    connect_timeout=30,
) as conn:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT employee_id, first_name, department, hire_date, salary, status,
                   is_new, is_deleted, editor, updated_at
            FROM public.employee_write
            """
        )
        rows = cursor.fetchall()

if not rows:
    dbutils.notebook.exit("No Lakebase writes to apply")

write_schema = StructType(
    [
        StructField("employee_id", StringType(), False),
        StructField("first_name", StringType(), True),
        StructField("department", StringType(), True),
        StructField("hire_date", DateType(), True),
        StructField("salary", DecimalType(12, 2), True),
        StructField("status", StringType(), True),
        StructField("is_new", BooleanType(), False),
        StructField("is_deleted", BooleanType(), False),
        StructField("editor", StringType(), True),
        StructField("updated_at", TimestampType(), False),
    ]
)
spark.createDataFrame(rows, schema=write_schema).createOrReplaceTempView("_write_src")

target = f"`{catalog}`.`{schema}`.employee_app_changes"
spark.sql(
    f"""
    MERGE INTO {target} t
    USING _write_src s
      ON t.employee_id = s.employee_id
    WHEN MATCHED AND (t.src_updated_at IS NULL OR s.updated_at > t.src_updated_at) THEN
      UPDATE SET
        first_name = s.first_name,
        department = s.department,
        hire_date = s.hire_date,
        salary = s.salary,
        status = s.status,
        is_new = s.is_new,
        is_deleted = s.is_deleted,
        editor = s.editor,
        src_updated_at = s.updated_at,
        synced_at = current_timestamp()
    WHEN NOT MATCHED THEN INSERT (
      employee_id, first_name, department, hire_date, salary, status,
      is_new, is_deleted, editor, src_updated_at, synced_at
    ) VALUES (
      s.employee_id, s.first_name, s.department, s.hire_date, s.salary, s.status,
      s.is_new, s.is_deleted, s.editor, s.updated_at, current_timestamp()
    )
    """
)

display(
    spark.sql(
        f"""
        SELECT COUNT(*) AS write_rows,
               MAX(src_updated_at) AS latest_source_update,
               MAX(synced_at) AS latest_bridge_sync
        FROM {target}
        """
    )
)
