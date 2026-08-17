from pathlib import Path

import pytest

from src.action.apply_action import EDITABLE_PROPERTIES, EmployeeActionStore

ROOT = Path(__file__).resolve().parents[1]


def test_app_created_ids_are_namespaced() -> None:
    EmployeeActionStore._require_app_id("app-123")
    with pytest.raises(ValueError, match="must start"):
        EmployeeActionStore._require_app_id("emp-00001")


def test_action_layer_rejects_source_owned_properties_before_connecting() -> None:
    store = object.__new__(EmployeeActionStore)
    with pytest.raises(ValueError, match="Only"):
        store.edit_property("emp-00001", editor="test", first_name="Not allowed")


def test_action_layer_requires_at_least_one_edit() -> None:
    store = object.__new__(EmployeeActionStore)
    with pytest.raises(ValueError, match="At least one"):
        store.edit_property("emp-00001", editor="test")


def test_only_salary_and_status_are_editable() -> None:
    assert EDITABLE_PROPERTIES == {"salary", "status"}


def test_overlay_contract_is_full_outer_and_tombstone_aware() -> None:
    ddl = (ROOT / "src/lakebase/employee_overlay.sql").read_text().upper()
    assert "FULL OUTER JOIN" in ddl
    assert "IS_DELETED" in ddl
    assert "OBJECT_LAYER.EMPLOYEE_SYNC" in ddl


def test_gold_contract_reconciles_without_mutating_base() -> None:
    ddl = (ROOT / "src/ddl/employee_gold.sql").read_text().upper()
    bridge = (ROOT / "src/notebooks/apply_bridge.py").read_text().upper()
    assert "FULL OUTER JOIN" in ddl
    assert "IS_HIGH_RISK" in ddl
    assert "MERGE INTO {TARGET}" in bridge
    assert "EMPLOYEE_BASE T" not in bridge
