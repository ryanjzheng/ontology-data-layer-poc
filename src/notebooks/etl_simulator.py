# Databricks notebook source
import json
import re
import uuid

from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType

dbutils.widgets.text("catalog", "serverless_stable_vfpvf8_catalog")
dbutils.widgets.text("schema", "object_layer")
dbutils.widgets.text("row_count", "100")
dbutils.widgets.text("bump_ids", "[]")
dbutils.widgets.text("bump_amount", "0")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
row_count = int(dbutils.widgets.get("row_count"))
bump_ids = json.loads(dbutils.widgets.get("bump_ids"))
bump_amount = float(dbutils.widgets.get("bump_amount"))

for label, value in (("catalog", catalog), ("schema", schema)):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Unsafe {label}: {value!r}")
if row_count < 1 or row_count > 100_000:
    raise ValueError("row_count must be between 1 and 100000")
if not isinstance(bump_ids, list) or not all(isinstance(item, str) for item in bump_ids):
    raise ValueError("bump_ids must be a JSON array of employee ID strings")

run_id = str(uuid.uuid4())
employee_id = F.format_string("emp-%05d", F.col("id") + 1)
base_salary = F.lit(65_000) + ((F.col("id") * 7_919) % 155_000)
salary = F.when(employee_id.isin(bump_ids), base_salary + F.lit(bump_amount)).otherwise(base_salary)

source = (
    spark.range(row_count)
    .select(
        employee_id.alias("employee_id"),
        F.concat(
            F.lit("Employee "),
            F.lpad((F.col("id") + 1).cast("string"), 5, "0"),
        ).alias("first_name"),
        F.element_at(
            F.array(
                F.lit("Network"),
                F.lit("Security"),
                F.lit("Operations"),
                F.lit("Finance"),
                F.lit("Engineering"),
            ),
            (F.col("id") % 5 + 1).cast("int"),
        ).alias("department"),
        F.date_sub(
            F.current_date(), ((F.col("id") * 37) % 3650).cast("int")
        ).alias("hire_date"),
        salary.cast(DecimalType(12, 2)).alias("salary"),
        F.when(F.col("id") % 17 == 0, F.lit("on_leave"))
        .otherwise(F.lit("active"))
        .alias("status"),
        F.lit(run_id).alias("_etl_run_id"),
        F.current_timestamp().alias("_etl_ts"),
    )
)
source.createOrReplaceTempView("_employee_refresh")

target = f"`{catalog}`.`{schema}`.employee_base"
spark.sql(
    f"""
    MERGE INTO {target} t
    USING _employee_refresh s
      ON t.employee_id = s.employee_id
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
    """
)

summary = (
    spark.table(f"{catalog}.{schema}.employee_base")
    .where(F.col("_etl_run_id") == run_id)
    .agg(
        F.first("_etl_run_id").alias("_etl_run_id"),
        F.count("*").alias("row_count"),
        F.sum(F.when(F.col("employee_id").isin(bump_ids), 1).otherwise(0)).alias(
            "bumped_rows"
        ),
        F.min("_etl_ts").alias("refreshed_at"),
    )
)
display(summary)
