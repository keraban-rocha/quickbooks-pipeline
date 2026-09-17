import time
import pandas as pd

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
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
    )

    return create_engine(
        f"mssql+pyodbc:///?odbc_connect={connection_string}",
        fast_executemany=True,
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