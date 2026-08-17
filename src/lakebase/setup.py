from __future__ import annotations

import argparse
from pathlib import Path

import psycopg
from databricks.sdk import WorkspaceClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the Lakebase write store and overlay")
    parser.add_argument("--profile", default="fevm-serverless")
    parser.add_argument(
        "--endpoint",
        default="projects/ontology-poc/branches/production/endpoints/primary",
    )
    parser.add_argument("--database", default="ontology_poc")
    args = parser.parse_args()

    workspace = WorkspaceClient(profile=args.profile)
    endpoint = workspace.postgres.get_endpoint(name=args.endpoint)
    credential = workspace.postgres.generate_database_credential(endpoint=args.endpoint)
    username = workspace.current_user.me().user_name

    ddl_dir = Path(__file__).resolve().parent
    statements = [
        (ddl_dir / "employee_write.sql").read_text(),
        (ddl_dir / "employee_overlay.sql").read_text(),
        (ddl_dir / "grants.sql").read_text(),
    ]
    with psycopg.connect(
        host=endpoint.status.hosts.host,
        dbname=args.database,
        user=username,
        password=credential.token,
        sslmode="require",
        connect_timeout=30,
    ) as conn:
        for statement in statements:
            conn.execute(statement)

        result = conn.execute(
            """
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE (table_schema = 'object_layer' AND table_name = 'employee_sync')
               OR (table_schema = 'public' AND table_name IN ('employee_write', 'employee_overlay'))
            ORDER BY table_schema, table_name
            """
        ).fetchall()
        for table_schema, table_name, table_type in result:
            print(f"{table_schema}.{table_name}: {table_type}")


if __name__ == "__main__":
    main()
