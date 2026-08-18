from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION_ROUTES = (
    ROOT
    / "app/ontology-object-demo/server/routes/lakebase/employee-routes.ts"
).read_text()


def test_app_is_the_only_action_surface() -> None:
    assert not (ROOT / "src/action/apply_action.py").exists()
    assert not (ROOT / "src/action/__init__.py").exists()
    assert not (ROOT / "demo/run_demo.py").exists()


def test_app_actions_write_only_to_employee_write() -> None:
    assert "INSERT INTO public.employee_write" in ACTION_ROUTES
    assert "UPDATE public.employee_write" in ACTION_ROUTES
    assert "employee_base" not in ACTION_ROUTES


def test_app_created_ids_are_namespaced() -> None:
    assert "const employeeId = `app-${randomUUID()}`" in ACTION_ROUTES


def test_app_edit_and_revert_contract_is_limited_to_editable_properties() -> None:
    edit_schema = ACTION_ROUTES.split("const EditEmployeeBody", 1)[1].split(
        "const RevertBody", 1
    )[0]
    assert "Provide salary or status" in edit_schema
    assert "firstName" not in edit_schema
    assert "z.enum(['salary', 'status'])" in ACTION_ROUTES


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


def test_upstream_trickle_is_bounded_and_writes_only_base() -> None:
    notebook = (ROOT / "src/notebooks/etl_trickle.py").read_text()
    job = (ROOT / "resources/etl_trickle.job.yml").read_text()
    assert 'dbutils.widgets.text("interval_seconds", "5")' in notebook
    assert 'dbutils.widgets.text("batch_count", "4")' in notebook
    assert 'dbutils.widgets.text("rows_per_batch", "2")' in notebook
    assert 'saveAsTable(target)' in notebook
    assert "employee_write" not in notebook
    assert 'default: "5"' in job
    assert 'default: "4"' in job
    assert 'default: "2"' in job
