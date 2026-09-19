import time
import pandas as pd

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.dialects.mssql import DATETIMEOFFSET, NVARCHAR
from urllib.parse import quote_plus

from src.config import (
    AZURE_SQL_SERVER,
    AZURE_SQL_DATABASE,
    AZURE_SQL_USERNAME,
    AZURE_SQL_PASSWORD,
)


def get_engine():
    connection_string = quote_plus(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER=tcp:{AZURE_SQL_SERVER},1433;"
        f"DATABASE={AZURE_SQL_DATABASE};"
        f"UID={AZURE_SQL_USERNAME};"
        f"PWD={AZURE_SQL_PASSWORD};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "LongAsMax=Yes;"
    )

    return create_engine(
        f"mssql+pyodbc:///?odbc_connect={connection_string}",
        # Use normal parameter binding for variable-length NVARCHAR(MAX) JSON.
        # pyodbc's fast array binding can allocate undersized string buffers.
        fast_executemany=False,
        pool_pre_ping=True,
        connect_args={
            "timeout": 120
        }
    )


def test_connection(
    engine=None,
    max_attempts=4
):
    engine = engine or get_engine()

    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text("""
                        SELECT
                            DB_NAME() AS database_name,
                            @@SERVERNAME AS server_name
                    """)
                ).fetchone()

            return {
                "database": result.database_name,
                "server": result.server_name
            }

        except OperationalError:
            if attempt == max_attempts:
                raise

            wait_seconds = 10 * attempt

            print(
                f"Azure SQL unavailable "
                f"(attempt {attempt}/{max_attempts}). "
                f"Retrying in {wait_seconds}s..."
            )

            time.sleep(wait_seconds)


def ensure_schema(
    schema,
    engine=None
):
    engine = engine or get_engine()

    sql = f"""
        IF NOT EXISTS (
            SELECT 1
            FROM sys.schemas
            WHERE name = '{schema}'
        )
        EXEC('CREATE SCHEMA {schema}')
    """

    with engine.begin() as conn:
        conn.execute(text(sql))


def write_dataframe(
    df,
    table,
    schema,
    if_exists="append",
    engine=None,
    dtype=None
):
    engine = engine or get_engine()

    # Keep raw ingestion types consistent even when a notebook's dtype dict
    # predates the explicit mapping. SQL Server TIMESTAMP is a rowversion,
    # not an extraction date/time.
    if schema == "bronze" and table in {
        "qbo_accounts_raw", "qbo_customers_raw", "qbo_journal_entries_raw"
    }:
        dtype = dict(dtype or {})
        raw_types = {
            "entity_id": NVARCHAR(100),
            "batch_id": NVARCHAR(36),
            "extracted_at": DATETIMEOFFSET(),
            "payload_json": NVARCHAR(None),
        }
        dtype.update({name: sql_type for name, sql_type in raw_types.items()
                      if name in df.columns})

    ensure_schema(schema, engine)

    df.to_sql(
        table,
        engine,
        schema=schema,
        if_exists=if_exists,
        index=False,
        dtype=dtype
    )


def read_sql(
    query,
    engine=None
):
    engine = engine or get_engine()

    return pd.read_sql(
        query,
        engine
    )
