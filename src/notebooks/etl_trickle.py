# Databricks notebook source
import re
import time
import uuid

from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType

dbutils.widgets.text("catalog", "serverless_stable_vfpvf8_catalog")
dbutils.widgets.text("schema", "object_layer")
dbutils.widgets.text("interval_seconds", "5")
dbutils.widgets.text("batch_count", "4")
dbutils.widgets.text("rows_per_batch", "2")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
interval_seconds = int(dbutils.widgets.get("interval_seconds"))
batch_count = int(dbutils.widgets.get("batch_count"))
rows_per_batch = int(dbutils.widgets.get("rows_per_batch"))

for label, value in (("catalog", catalog), ("schema", schema)):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Unsafe {label}: {value!r}")
if not 1 <= interval_seconds <= 60:
    raise ValueError("interval_seconds must be between 1 and 60")
if not 1 <= batch_count <= 20:
    raise ValueError("batch_count must be between 1 and 20")
if not 1 <= rows_per_batch <= 100:
    raise ValueError("rows_per_batch must be between 1 and 100")

run_id = f"trickle-{uuid.uuid4()}"
run_tag = run_id.removeprefix("trickle-")[:8]
target = f"{catalog}.{schema}.employee_base"

for batch_index in range(batch_count):
    time.sleep(interval_seconds)

    row_number = F.col("id") + 1
    global_number = (F.lit(batch_index * rows_per_batch) + row_number).cast("long")
    employees = spark.range(rows_per_batch).select(
        F.concat(
            F.lit(f"live-{run_tag}-{batch_index + 1:02d}-"),
            F.lpad(row_number.cast("string"), 3, "0"),
        ).alias("employee_id"),
        F.concat(
            F.lit("Live Employee "),
            F.lpad(global_number.cast("string"), 3, "0"),
        ).alias("first_name"),
        F.element_at(
            F.array(
                F.lit("Network"),
                F.lit("Security"),
                F.lit("Operations"),
                F.lit("Finance"),
            ),
            ((global_number - 1) % 4 + 1).cast("int"),
        ).alias("department"),
        F.current_date().alias("hire_date"),
        (F.lit(72_000) + (global_number * 3_250))
        .cast(DecimalType(12, 2))
        .alias("salary"),
        F.lit("active").alias("status"),
        F.lit(run_id).alias("_etl_run_id"),
        F.current_timestamp().alias("_etl_ts"),
    )
    employees.write.mode("append").saveAsTable(target)
    print(
        f"Batch {batch_index + 1}/{batch_count}: appended "
        f"{rows_per_batch} employees to {target}"
    )

display(
    spark.table(target)
    .where(F.col("_etl_run_id") == run_id)
    .orderBy("employee_id")
)
