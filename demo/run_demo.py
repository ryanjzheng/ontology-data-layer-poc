from __future__ import annotations

import argparse
import json
import time
from datetime import date
from decimal import Decimal
from typing import Any

from databricks.sdk import WorkspaceClient

from src.action.apply_action import EmployeeActionStore, LakebaseConfig

CATALOG = "serverless_stable_vfpvf8_catalog"
SCHEMA = "object_layer"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class AcceptanceDemo:
    def __init__(
        self,
        *,
        profile: str,
        warehouse_id: str,
        etl_job_id: int,
        bridge_job_id: int,
    ) -> None:
        self.workspace = WorkspaceClient(profile=profile)
        self.warehouse_id = warehouse_id
        self.etl_job_id = etl_job_id
        self.bridge_job_id = bridge_job_id
        self.actions = EmployeeActionStore(LakebaseConfig(profile=profile))

    def query(self, statement: str) -> list[list[str]]:
        response = self.workspace.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=self.warehouse_id,
            wait_timeout="50s",
        )
        if response.status and response.status.error:
            raise RuntimeError(response.status.error.message)
        return (response.result.data_array if response.result else None) or []

    def scalar(self, statement: str) -> str | None:
        rows = self.query(statement)
        return rows[0][0] if rows else None

    def run_bridge(self) -> None:
        self.workspace.jobs.run_now_and_wait(job_id=self.bridge_job_id)

    def run_etl(self, bump_ids: list[str], bump_amount: int) -> None:
        self.workspace.jobs.run_now_and_wait(
            job_id=self.etl_job_id,
            job_parameters={
                "catalog": CATALOG,
                "schema": SCHEMA,
                "row_count": "100",
                "bump_ids": json.dumps(bump_ids),
                "bump_amount": str(bump_amount),
            },
        )

    def wait_for_overlay(self, employee_id: str, expected_salary: Decimal) -> None:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            row = self.actions.get_object(employee_id)
            if row and row["salary"] == expected_salary:
                return
            time.sleep(5)
        raise TimeoutError(f"Lakebase sync did not expose {employee_id} at {expected_salary}")

    def uc_salary(self, table: str, employee_id: str) -> Decimal | None:
        value = self.scalar(
            f"SELECT salary FROM {CATALOG}.{SCHEMA}.{table} "
            f"WHERE employee_id = {_quote(employee_id)}"
        )
        return Decimal(value) if value is not None else None

    def count(self, table: str, employee_id: str) -> int:
        value = self.scalar(
            f"SELECT COUNT(*) FROM {CATALOG}.{SCHEMA}.{table} "
            f"WHERE employee_id = {_quote(employee_id)}"
        )
        return int(value or 0)

    def run(self) -> dict[str, Any]:
        editor = "ontology-poc-demo"
        edited_id = "emp-00001"
        source_delete_id = "emp-00002"
        edited_salary = Decimal("250000.00")

        base_before = self.uc_salary("employee_base", edited_id)
        if base_before is None:
            raise AssertionError("Run the ETL simulator before the acceptance demo")
        self.wait_for_overlay(edited_id, base_before)

        immediate = self.actions.edit_property(
            edited_id, salary=edited_salary, editor=editor
        )
        assert immediate["salary"] == edited_salary
        self.run_bridge()
        assert self.uc_salary("employee_app_changes", edited_id) == edited_salary
        assert self.uc_salary("employee_gold", edited_id) == edited_salary

        created = self.actions.create_object(
            first_name="Ada",
            department="Network",
            hire_date=date(2026, 8, 17),
            salary=Decimal("225000.00"),
            status="active",
            editor=editor,
        )
        created_id = created["employee_id"]
        assert self.count("employee_base", created_id) == 0
        self.run_bridge()
        assert self.count("employee_gold", created_id) == 1

        self.run_etl([edited_id, source_delete_id], 12_345)
        refreshed_edited_base = self.uc_salary("employee_base", edited_id)
        refreshed_unedited_base = self.uc_salary("employee_base", source_delete_id)
        assert refreshed_edited_base != base_before
        assert self.uc_salary("employee_gold", edited_id) == edited_salary
        assert self.uc_salary("employee_gold", source_delete_id) == refreshed_unedited_base

        self.actions.revert_property(edited_id, "salary", editor=editor)
        self.run_bridge()
        assert self.uc_salary("employee_gold", edited_id) == refreshed_edited_base

        self.actions.delete_object(source_delete_id, editor=editor)
        self.actions.delete_object(created_id, editor=editor)
        assert self.actions.get_object(source_delete_id) is None
        assert self.actions.get_object(created_id) is None
        self.run_bridge()
        assert self.count("employee_gold", source_delete_id) == 0
        assert self.count("employee_gold", created_id) == 0
        assert self.count("employee_base", source_delete_id) == 1
        assert self.count("employee_base", created_id) == 0

        return {
            "edit_propagation": "passed",
            "create_without_base_write": "passed",
            "refresh_resilience": "passed",
            "non_edited_refresh": "passed",
            "revert_to_base": "passed",
            "delete_tombstones": "passed",
            "created_employee_id": created_id,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ontology POC acceptance behaviors")
    parser.add_argument("--profile", default="fevm-serverless")
    parser.add_argument("--warehouse-id", default="50878c28d521fb53")
    parser.add_argument("--etl-job-id", type=int, required=True)
    parser.add_argument("--bridge-job-id", type=int, required=True)
    args = parser.parse_args()

    demo = AcceptanceDemo(
        profile=args.profile,
        warehouse_id=args.warehouse_id,
        etl_job_id=args.etl_job_id,
        bridge_job_id=args.bridge_job_id,
    )
    print(json.dumps(demo.run(), indent=2))


if __name__ == "__main__":
    main()
