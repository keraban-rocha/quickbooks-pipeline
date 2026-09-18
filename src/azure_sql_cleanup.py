"""Preview and transactionally drop user tables and custom Azure SQL schemas."""

from sqlalchemy import text


def quote_identifier(name):
    return "[" + name.replace("]", "]]") + "]"


def qualified(schema, name):
    return f"{quote_identifier(schema)}.{quote_identifier(name)}"


def inspect_cleanup(connection):
    """Read metadata only. Require full visibility to avoid an incomplete plan."""
    identity = dict(connection.execute(text("""
        SELECT DB_NAME() AS database_name,
               CAST(SERVERPROPERTY('ServerName') AS nvarchar(256)) AS server_name,
               HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONTROL') AS can_control
    """)).mappings().one())
    if identity["database_name"].lower() in {"master", "model", "msdb", "tempdb"}:
        raise ValueError("Cleanup is restricted to a user database")
    if identity.pop("can_control") != 1:
        raise PermissionError("Cleanup requires CONTROL on this database for full metadata visibility and DDL")

    def rows(sql):
        return [dict(row) for row in connection.execute(text(sql)).mappings()]

    tables = rows("""
        SELECT t.object_id, s.name AS schema_name, t.name,
               t.temporal_type, t.is_memory_optimized, t.is_filetable,
               t.is_external, t.ledger_type
        FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
        WHERE t.is_ms_shipped = 0 ORDER BY s.name, t.name
    """)
    schemas = rows("""
        SELECT schema_id, name FROM sys.schemas
        WHERE schema_id > 4 AND schema_id < 16384 ORDER BY name
    """)
    foreign_keys = rows("""
        SELECT f.object_id, s.name AS schema_name, t.name AS table_name, f.name
        FROM sys.foreign_keys f
        JOIN sys.tables t ON t.object_id = f.parent_object_id
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        WHERE t.is_ms_shipped = 0 ORDER BY s.name, t.name, f.name
    """)
    # Other schema objects are deliberately reported, never silently deleted.
    blockers = rows("""
        SELECT s.name AS schema_name, o.name, o.type_desc AS reason
        FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
        WHERE o.is_ms_shipped = 0 AND o.parent_object_id = 0
          AND o.type NOT IN ('U', 'IT')
        UNION ALL
        SELECT SCHEMA_NAME(schema_id), name, 'USER_DEFINED_TYPE'
        FROM sys.types WHERE is_user_defined = 1
        UNION ALL
        SELECT SCHEMA_NAME(schema_id), name, 'XML_SCHEMA_COLLECTION'
        FROM sys.xml_schema_collections WHERE xml_collection_id > 1
    """)
    for table in tables:
        if any(table[key] for key in ("is_memory_optimized", "is_filetable", "is_external", "ledger_type")):
            blockers.append({"schema_name": table["schema_name"], "name": table["name"],
                             "reason": "Special table type requires a dedicated cleanup procedure"})
    statements = []
    for table in tables:
        if table["temporal_type"] == 2:
            statements.append(f"ALTER TABLE {qualified(table['schema_name'], table['name'])} SET (SYSTEM_VERSIONING = OFF);")
    for fk in foreign_keys:
        statements.append(f"ALTER TABLE {qualified(fk['schema_name'], fk['table_name'])} DROP CONSTRAINT {quote_identifier(fk['name'])};")
    for table in tables:
        statements.append(f"DROP TABLE {qualified(table['schema_name'], table['name'])};")
    for schema in schemas:
        statements.append(f"DROP SCHEMA {quote_identifier(schema['name'])};")
    return {"identity": identity, "tables": tables, "schemas": schemas,
            "foreign_keys": foreign_keys, "blockers": blockers, "statements": statements}


def execute_cleanup(engine, reviewed_plan, confirmed_database):
    """Recheck the preview, execute in one transaction, and verify before commit."""
    if confirmed_database != reviewed_plan["identity"]["database_name"]:
        raise ValueError("CONFIRM_DATABASE must exactly match the database shown in the preview")
    if reviewed_plan["blockers"]:
        raise ValueError("Resolve the preview's blockers before cleanup; no objects were dropped")
    with engine.begin() as connection:
        connection.execute(text("SET XACT_ABORT ON; SET LOCK_TIMEOUT 15000;"))
        current = inspect_cleanup(connection)
        if current != reviewed_plan:
            raise ValueError("Database objects or target changed; rerun and review the preview")
        for statement in current["statements"]:
            connection.execute(text(statement))
        remaining = inspect_cleanup(connection)
        if remaining["tables"] or remaining["schemas"]:
            raise RuntimeError("Cleanup verification failed; rolling back")
    return {"tables_dropped": len(current["tables"]),
            "schemas_dropped": len(current["schemas"])}
