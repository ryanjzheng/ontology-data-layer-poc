from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient
from psycopg import sql
from psycopg.rows import dict_row

EDITABLE_PROPERTIES = frozenset({"salary", "status"})
SOURCE_PROPERTIES = frozenset({"first_name", "department", "hire_date"})


@dataclass(frozen=True)
class LakebaseConfig:
    endpoint: str = "projects/ontology-poc/branches/production/endpoints/primary"
    database: str = "ontology_poc"
    profile: str = "fevm-serverless"


class EmployeeActionStore:
    """Thin action surface that can write only to public.employee_write."""

    def __init__(self, config: LakebaseConfig | None = None) -> None:
        self.config = config or LakebaseConfig()
        self.workspace = WorkspaceClient(profile=self.config.profile)

    def _connect(self) -> psycopg.Connection[dict[str, Any]]:
        endpoint = self.workspace.postgres.get_endpoint(name=self.config.endpoint)
        credential = self.workspace.postgres.generate_database_credential(
            endpoint=self.config.endpoint
        )
        username = self.workspace.current_user.me().user_name
        return psycopg.connect(
            host=endpoint.status.hosts.host,
            dbname=self.config.database,
            user=username,
            password=credential.token,
            sslmode="require",
            connect_timeout=30,
            row_factory=dict_row,
        )

    @staticmethod
    def _require_app_id(employee_id: str) -> None:
        if not employee_id.startswith("app-"):
            raise ValueError("App-created employee IDs must start with 'app-'")

    @staticmethod
    def _exists(conn: psycopg.Connection[Any], employee_id: str) -> bool:
        row = conn.execute(
            """
            SELECT EXISTS (
              SELECT 1 FROM object_layer.employee_sync WHERE employee_id = %s
              UNION ALL
              SELECT 1 FROM public.employee_write WHERE employee_id = %s
            ) AS present
            """,
            (employee_id, employee_id),
        ).fetchone()
        return bool(row["present"])

    @staticmethod
    def _read_overlay(
        conn: psycopg.Connection[Any], employee_id: str
    ) -> dict[str, Any] | None:
        return conn.execute(
            "SELECT * FROM public.employee_overlay WHERE employee_id = %s",
            (employee_id,),
        ).fetchone()

    def create_object(
        self,
        *,
        first_name: str,
        department: str,
        hire_date: date,
        salary: Decimal | int | float | None = None,
        status: str | None = "active",
        editor: str,
        employee_id: str | None = None,
    ) -> dict[str, Any]:
        employee_id = employee_id or f"app-{uuid.uuid4()}"
        self._require_app_id(employee_id)
        with self._connect() as conn:
            if self._exists(conn, employee_id):
                raise ValueError(f"Employee {employee_id!r} already exists")
            conn.execute(
                """
                INSERT INTO public.employee_write (
                  employee_id, first_name, department, hire_date, salary, status,
                  is_new, is_deleted, editor, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, true, false, %s, now())
                """,
                (
                    employee_id,
                    first_name,
                    department,
                    hire_date,
                    salary,
                    status,
                    editor,
                ),
            )
            return self._read_overlay(conn, employee_id)

    def edit_property(
        self, employee_id: str, *, editor: str, **changes: Any
    ) -> dict[str, Any]:
        if not changes:
            raise ValueError("At least one editable property is required")
        unknown = set(changes) - EDITABLE_PROPERTIES
        if unknown:
            raise ValueError(
                f"Only {sorted(EDITABLE_PROPERTIES)} are editable; rejected {sorted(unknown)}"
            )

        columns = list(changes)
        insert_columns = [
            sql.Identifier("employee_id"),
            *map(sql.Identifier, columns),
            sql.Identifier("editor"),
        ]
        placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in insert_columns)
        assignments = sql.SQL(", ").join(
            sql.SQL("{column} = EXCLUDED.{column}").format(column=sql.Identifier(column))
            for column in columns
        )
        statement = sql.SQL(
            """
            INSERT INTO public.employee_write ({columns}, updated_at)
            VALUES ({placeholders}, now())
            ON CONFLICT (employee_id) DO UPDATE SET
              {assignments},
              editor = EXCLUDED.editor,
              updated_at = now()
            """
        ).format(
            columns=sql.SQL(", ").join(insert_columns),
            placeholders=placeholders,
            assignments=assignments,
        )
        values = [employee_id, *(changes[column] for column in columns), editor]

        with self._connect() as conn:
            if not self._exists(conn, employee_id):
                raise ValueError(f"Employee {employee_id!r} does not exist")
            conn.execute(statement, values)
            return self._read_overlay(conn, employee_id)

    def revert_property(
        self, employee_id: str, property_name: str, *, editor: str
    ) -> dict[str, Any]:
        if property_name not in EDITABLE_PROPERTIES:
            raise ValueError(f"{property_name!r} is not an editable property")
        with self._connect() as conn:
            write_row = conn.execute(
                "SELECT is_new FROM public.employee_write WHERE employee_id = %s",
                (employee_id,),
            ).fetchone()
            if write_row is None:
                raise ValueError(f"Employee {employee_id!r} has no edit to revert")
            if write_row["is_new"]:
                raise ValueError(
                    "App-created objects have no BASE value; delete the object instead"
                )
            conn.execute(
                sql.SQL(
                    "UPDATE public.employee_write "
                    "SET {property} = NULL, editor = %s, updated_at = now() "
                    "WHERE employee_id = %s"
                ).format(property=sql.Identifier(property_name)),
                (editor, employee_id),
            )
            return self._read_overlay(conn, employee_id)

    def delete_object(self, employee_id: str, *, editor: str) -> None:
        with self._connect() as conn:
            if not self._exists(conn, employee_id):
                raise ValueError(f"Employee {employee_id!r} does not exist")
            conn.execute(
                """
                INSERT INTO public.employee_write (
                  employee_id, is_new, is_deleted, editor, updated_at
                ) VALUES (%s, false, true, %s, now())
                ON CONFLICT (employee_id) DO UPDATE SET
                  is_deleted = true,
                  editor = EXCLUDED.editor,
                  updated_at = now()
                """,
                (employee_id, editor),
            )

    def undelete_object(
        self, employee_id: str, *, editor: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            result = conn.execute(
                """
                UPDATE public.employee_write
                SET is_deleted = false, editor = %s, updated_at = now()
                WHERE employee_id = %s
                """,
                (editor, employee_id),
            )
            if result.rowcount != 1:
                raise ValueError(f"Employee {employee_id!r} has no tombstone")
            return self._read_overlay(conn, employee_id)

    def get_object(self, employee_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._read_overlay(conn, employee_id)

    def list_objects(self, limit: int = 100) -> Iterator[dict[str, Any]]:
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM public.employee_overlay ORDER BY employee_id LIMIT %s",
                (limit,),
            ).fetchall()
        yield from rows


def _json_default(value: Any) -> str:
    if isinstance(value, (date, Decimal)):
        return str(value)
    raise TypeError(f"Cannot encode {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="fevm-serverless")
    parser.add_argument(
        "--endpoint",
        default="projects/ontology-poc/branches/production/endpoints/primary",
    )
    parser.add_argument("--database", default="ontology_poc")
    subparsers = parser.add_subparsers(dest="operation", required=True)

    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("employee_id")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--limit", type=int, default=100)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--employee-id")
    create_parser.add_argument("--first-name", required=True)
    create_parser.add_argument("--department", required=True)
    create_parser.add_argument("--hire-date", type=date.fromisoformat, required=True)
    create_parser.add_argument("--salary", type=Decimal)
    create_parser.add_argument("--status", default="active")
    create_parser.add_argument("--editor", required=True)

    edit_parser = subparsers.add_parser("edit")
    edit_parser.add_argument("employee_id")
    edit_parser.add_argument("--salary", type=Decimal)
    edit_parser.add_argument("--status")
    edit_parser.add_argument("--editor", required=True)

    revert_parser = subparsers.add_parser("revert")
    revert_parser.add_argument("employee_id")
    revert_parser.add_argument("property", choices=sorted(EDITABLE_PROPERTIES))
    revert_parser.add_argument("--editor", required=True)

    delete_parser = subparsers.add_parser("delete")
    delete_parser.add_argument("employee_id")
    delete_parser.add_argument("--editor", required=True)

    undelete_parser = subparsers.add_parser("undelete")
    undelete_parser.add_argument("employee_id")
    undelete_parser.add_argument("--editor", required=True)

    args = parser.parse_args()
    store = EmployeeActionStore(
        LakebaseConfig(args.endpoint, args.database, args.profile)
    )

    if args.operation == "get":
        result: Any = store.get_object(args.employee_id)
    elif args.operation == "list":
        result = list(store.list_objects(args.limit))
    elif args.operation == "create":
        result = store.create_object(
            employee_id=args.employee_id,
            first_name=args.first_name,
            department=args.department,
            hire_date=args.hire_date,
            salary=args.salary,
            status=args.status,
            editor=args.editor,
        )
    elif args.operation == "edit":
        changes = {
            key: value
            for key, value in {"salary": args.salary, "status": args.status}.items()
            if value is not None
        }
        result = store.edit_property(args.employee_id, editor=args.editor, **changes)
    elif args.operation == "revert":
        result = store.revert_property(
            args.employee_id, args.property, editor=args.editor
        )
    elif args.operation == "undelete":
        result = store.undelete_object(args.employee_id, editor=args.editor)
    else:
        store.delete_object(args.employee_id, editor=args.editor)
        result = {"deleted": args.employee_id}

    print(json.dumps(result, default=_json_default, indent=2))


if __name__ == "__main__":
    main()
