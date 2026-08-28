"""Bring an existing database up to date with db/models.py.

SQLAlchemy's create_all() only creates missing *tables* — it never adds a
column to a table that already exists, so a deployed database silently keeps
the old shape and every query touching a new column fails. This adds what's
missing, in place, without touching existing rows.

    python scripts/migrate.py

Safe to run repeatedly: it only adds what isn't there. It does not drop or
rename anything, so a column removed from the model simply stays behind
unused.
"""
from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

from sqlalchemy import inspect, text  # noqa: E402

from db.models import Base, engine, init_db  # noqa: E402

# SQLite's ALTER TABLE ADD COLUMN cannot add a NOT NULL column without a
# default, so every added column is nullable or carries one.
_SQLITE_TYPES = {
    "BOOLEAN": "BOOLEAN",
    "VARCHAR": "VARCHAR",
    "TEXT": "TEXT",
    "INTEGER": "INTEGER",
    "BIGINT": "BIGINT",
    "FLOAT": "FLOAT",
    "DATETIME": "DATETIME",
}


def _column_sql(column) -> str:
    type_name = column.type.compile(engine.dialect)
    sql = f"{column.name} {type_name}"
    default = column.default
    if default is not None and getattr(default, "is_scalar", False):
        value = default.arg
        if isinstance(value, bool):
            sql += f" DEFAULT {1 if value else 0}"
        elif isinstance(value, (int, float)):
            sql += f" DEFAULT {value}"
        elif isinstance(value, str):
            sql += f" DEFAULT '{value}'"
    return sql


def migrate() -> None:
    init_db()  # creates any table that doesn't exist yet
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    added = 0
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_columns:
                continue
            ddl = f"ALTER TABLE {table.name} ADD COLUMN {_column_sql(column)}"
            print(f"  + {table.name}.{column.name}")
            with engine.begin() as conn:
                conn.execute(text(ddl))
            added += 1

    print(f"\nDone. Columns added: {added}." if added else "\nDone. Schema already up to date.")


if __name__ == "__main__":
    print(f"Migrating {engine.url}\n")
    migrate()
