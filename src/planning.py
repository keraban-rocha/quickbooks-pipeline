"""Import versioned FP&A plans and expose monthly comparisons with QBO actuals."""

import hashlib
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from sqlalchemy import MetaData, Table, text

INCOME = {"Revenue", "Other Income"}
EXPENSE = {"COGS", "Opex", "Other Expense", "Tax"}
KEY = ["version_key", "month_start", "account_id", "department"]


def amount(value):
    try:
        result = Decimal(value)
        if not result.is_finite() or result != result.quantize(Decimal("0.01")) or abs(result) >= Decimal("10000000000000000"):
            raise ValueError(f"Invalid USD amount: {value!r}")
        return result
    except InvalidOperation as exc:
        raise ValueError(f"Invalid USD amount: {value!r}") from exc


def load_plans(data_dir, *, budget_label="Original Budget", fiscal_start_month=9,
               forecast_as_of=None):
    """Read CSVs without API/database calls. Forecast dates must be explicit."""
    if not 1 <= fiscal_start_month <= 12 or not budget_label.strip():
        raise ValueError("Provide a budget label and fiscal start month from 1 to 12")
    forecast_as_of = forecast_as_of or {}
    rows = []
    batch = str(uuid.uuid4())
    loaded = datetime.now(timezone.utc)
    for scenario, filename, prefix in (
        ("Budget", "budget_detail.csv", "Budget"),
        ("Forecast", "forecast_detail.csv", "Forecast"),
    ):
        path = Path(data_dir) / filename
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        required = {"Month", "Account_Number", "Account_Name", "PL_Section", "Subcategory",
                    "Department", f"{prefix}_Amount_USD", f"{prefix}_Reporting_Amount_USD"}
        required |= {"Forecast_Version", "Basis"} if scenario == "Forecast" else {"Primary_Driver"}
        missing = required - set(frame.columns)
        if missing or frame.empty:
            raise ValueError(f"{filename}: missing columns {sorted(missing)} or empty file")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        for index, raw in frame.iterrows():
            r = {key: value.strip() for key, value in raw.items()}
            if any(not r[col] for col in required):
                raise ValueError(f"{filename} row {index + 2}: blank required field")
            month = date.fromisoformat(r["Month"])
            if month.day != 1:
                raise ValueError(f"{filename}: Month must be the first day of the month")
            fy = month.year + int(fiscal_start_month != 1 and month.month >= fiscal_start_month)
            as_of = None
            if scenario == "Budget":
                version = f"FY{fy} {budget_label.strip()}"
                basis = "Budget"
            else:
                version = r["Forecast_Version"]
                if version not in forecast_as_of:
                    raise ValueError(f"Supply forecast_as_of for version {version!r}")
                as_of = date.fromisoformat(forecast_as_of[version])
                basis = r["Basis"]
                if basis not in {"Actualized", "Forecast"}:
                    raise ValueError(f"Unexpected forecast Basis: {basis}")
                if (basis == "Actualized") != (month <= as_of.replace(day=1)):
                    raise ValueError(f"Forecast Basis disagrees with as-of month: {version}, {month}")
            native = amount(r[f"{prefix}_Amount_USD"])
            reporting = amount(r[f"{prefix}_Reporting_Amount_USD"])
            section = r["PL_Section"]
            if section not in INCOME | EXPENSE:
                raise ValueError(f"Unknown PL_Section: {section}")
            sign = 1 if section in INCOME and r["Subcategory"] != "Contra Revenue" else -1
            if reporting != native * sign:
                raise ValueError(f"Inconsistent reporting sign: {filename} row {index + 2}")
            rows.append({"version_key": str(uuid.uuid5(uuid.NAMESPACE_URL, scenario + ":" + version)),
                         "scenario": scenario, "version_name": version, "forecast_as_of": as_of,
                         "fiscal_year": fy, "fiscal_start_month": fiscal_start_month,
                         "month_start": month, "account_number": r["Account_Number"],
                         "account_name": r["Account_Name"], "pl_section": section,
                         "subcategory": r["Subcategory"], "department": r["Department"],
                         "primary_driver": r.get("Primary_Driver") or None, "basis": basis,
                         "amount_usd": native, "reporting_amount_usd": reporting,
                         "source_file": filename, "source_sha256": digest,
                         "load_batch_id": batch, "loaded_at": loaded})
    result = pd.DataFrame(rows)
    if result.duplicated(["version_key", "month_start", "account_number", "department"]).any():
        raise ValueError("Duplicate plan grain: version/month/account/department")
    validate_metadata(result)
    # A scenario/version/month must not mix Actualized and Forecast rows.
    if result.groupby(["version_key", "month_start"]).basis.nunique().gt(1).any():
        raise ValueError("Mixed Basis within a version/month")
    limits = {"version_name": 150, "account_number": 100, "account_name": 255,
              "department": 100, "subcategory": 150, "primary_driver": 200}
    for col, limit in limits.items():
        if result[col].fillna("").str.len().gt(limit).any():
            raise ValueError(f"{col} exceeds {limit} characters")
    return result


def validate_metadata(frame):
    for col in ("account_name", "pl_section", "subcategory"):
        if frame.groupby("account_number")[col].nunique().gt(1).any():
            raise ValueError(f"Inconsistent account metadata: {col}")


def map_accounts(plans, accounts):
    """Use account numbers as source keys; resolve the actual QBO account ID."""
    accounts = accounts.copy()
    accounts["account_number"] = accounts.account_number.fillna("").astype(str).str.strip()
    candidates = accounts[accounts.account_number.isin(plans.account_number)]
    if candidates.account_number.duplicated().any() or candidates.account_id.duplicated().any():
        raise ValueError("QBO account number/ID mapping is ambiguous")
    merged = plans.merge(candidates[["account_id", "account_number", "classification"]],
                         on="account_number", how="left", validate="many_to_one")
    missing = merged.loc[merged.account_id.isna(), "account_number"].unique()
    if len(missing):
        raise ValueError(f"Accounts missing from silver.dim_account: {list(missing)}")
    expected = merged.pl_section.map(lambda section: "Revenue" if section in INCOME else "Expense")
    if not expected.eq(merged.classification).all():
        raise ValueError("Planning P&L sections disagree with QBO account classifications")
    return merged.drop(columns="classification")


DDL = """
IF SCHEMA_ID('silver') IS NULL EXEC('CREATE SCHEMA silver');
IF SCHEMA_ID('gold') IS NULL EXEC('CREATE SCHEMA gold');
IF OBJECT_ID('silver.fact_plan', 'U') IS NULL
CREATE TABLE silver.fact_plan (
 version_key nvarchar(36) NOT NULL, scenario nvarchar(10) NOT NULL,
 version_name nvarchar(150) NOT NULL, forecast_as_of date NULL,
 fiscal_year int NOT NULL, fiscal_start_month int NOT NULL,
 month_start date NOT NULL, account_id nvarchar(100) NOT NULL,
 account_number nvarchar(100) NOT NULL, account_name nvarchar(255) NOT NULL,
 pl_section nvarchar(30) NOT NULL, subcategory nvarchar(150) NOT NULL,
 department nvarchar(100) NOT NULL, primary_driver nvarchar(200) NULL,
 basis nvarchar(20) NOT NULL, amount_usd decimal(18,2) NOT NULL,
 reporting_amount_usd decimal(18,2) NOT NULL, source_file nvarchar(100) NOT NULL,
 source_sha256 nvarchar(64) NOT NULL, load_batch_id nvarchar(36) NOT NULL,
 loaded_at datetimeoffset(7) NOT NULL,
 CONSTRAINT PK_fact_plan PRIMARY KEY (version_key, month_start, account_id, department)
);
"""

VIEWS = ["""
CREATE OR ALTER VIEW gold.fpa_monthly AS
SELECT DATEFROMPARTS(a.[year], a.[month], 1) AS month_start,
       CAST(a.account_id AS nvarchar(100)) AS account_id,
       CAST('Actual' AS nvarchar(10)) AS scenario,
       CAST('Actual' AS nvarchar(36)) AS version_key,
       CAST('Actual' AS nvarchar(150)) AS version_name,
       CAST(NULL AS date) AS forecast_as_of, CAST('Actual' AS nvarchar(20)) AS basis,
       CAST(SUM(a.actual_amount) AS decimal(18,2)) AS amount_usd,
       CAST(SUM(CASE WHEN a.classification = 'Expense' THEN -a.actual_amount
                     ELSE a.actual_amount END) AS decimal(18,2)) AS reporting_amount_usd
FROM gold.pnl_monthly_actual a
GROUP BY a.[year], a.[month], a.account_id
UNION ALL
SELECT month_start, account_id, scenario, version_key, version_name, forecast_as_of, basis,
       SUM(amount_usd), SUM(reporting_amount_usd)
FROM silver.fact_plan
GROUP BY month_start, account_id, scenario, version_key, version_name, forecast_as_of, basis
""", """
CREATE OR ALTER VIEW gold.fpa_account AS
SELECT a.account_id, a.account_number, a.account_name, a.account_type,
       a.classification, COALESCE(p.pl_section, 'Unmapped') AS pl_section,
       COALESCE(p.subcategory, 'Unmapped') AS subcategory
FROM silver.dim_account a
LEFT JOIN (SELECT DISTINCT account_id, pl_section, subcategory FROM silver.fact_plan) p
ON a.account_id = p.account_id
""", """
CREATE OR ALTER VIEW gold.fpa_version AS
SELECT DISTINCT version_key, scenario, version_name, forecast_as_of
FROM gold.fpa_monthly
"""]


def import_plans(connection, plans):
    """Caller owns transaction. Reload complete supplied versions, retain others."""
    connection.execute(text("SET XACT_ABORT ON;"))
    # Serialize plan imports so that version replacement cannot interleave.
    lock = connection.execute(text("""
        DECLARE @result int;
        EXEC @result = sp_getapplock @Resource='fpa_plan_import',
             @LockMode='Exclusive', @LockOwner='Transaction', @LockTimeout=15000;
        SELECT @result;
    """)).scalar_one()
    if lock < 0:
        raise RuntimeError("Another planning import is running")
    accounts = pd.read_sql(text("SELECT account_id, account_number, classification FROM silver.dim_account"), connection)
    if accounts.account_id.duplicated().any():
        raise ValueError("silver.dim_account contains duplicate account IDs")
    mapped = map_accounts(plans, accounts)
    # Verify prerequisites before writing; no account-name joins or guessed IDs.
    connection.execute(text("SELECT TOP 0 [year], [month], account_id, classification, actual_amount FROM gold.pnl_monthly_actual"))
    connection.execute(text(DDL))
    keys = mapped.version_key.unique().tolist()
    existing = pd.read_sql(text("SELECT * FROM silver.fact_plan"), connection)
    retained = existing[~existing.version_key.isin(keys)]
    combined = pd.concat([retained, mapped], ignore_index=True)
    validate_metadata(combined)
    if combined.fiscal_start_month.nunique() != 1:
        raise ValueError("Fiscal start month differs from retained planning versions")
    for version_key in keys:
        connection.execute(text("DELETE FROM silver.fact_plan WHERE version_key=:key"), {"key": version_key})
    records = mapped.astype(object).where(pd.notnull(mapped), None).to_dict("records")
    # A typed SQLAlchemy Insert enables bounded multi-row inserts and explicit
    # DECIMAL/DATE/DATETIMEOFFSET bindings, without pyodbc fast array binding.
    target = Table("fact_plan", MetaData(), schema="silver", autoload_with=connection)
    for start in range(0, len(records), 500):
        connection.execute(target.insert(), records[start:start + 500])
    for sql_view in VIEWS:
        connection.execute(text(sql_view))
    start_month = int(mapped.fiscal_start_month.iloc[0])
    connection.execute(text(f"""
        CREATE OR ALTER VIEW gold.fpa_month AS
        SELECT DISTINCT month_start, YEAR(month_start) AS calendar_year,
               MONTH(month_start) AS calendar_month,
               CONVERT(char(7), month_start, 126) AS year_month,
               YEAR(month_start) + CASE WHEN {start_month} <> 1 AND MONTH(month_start) >= {start_month}
                                       THEN 1 ELSE 0 END AS fiscal_year,
               ((MONTH(month_start) - {start_month} + 12) % 12) + 1 AS fiscal_month
        FROM gold.fpa_monthly
    """))
    for key in keys:
        expected = mapped[mapped.version_key == key]
        actual = connection.execute(text("""
            SELECT COUNT_BIG(*) AS row_count, SUM(reporting_amount_usd) AS total
            FROM silver.fact_plan WHERE version_key=:key
        """), {"key": key}).mappings().one()
        if actual["row_count"] != len(expected) or actual["total"] != sum(expected.reporting_amount_usd):
            raise RuntimeError("Plan load reconciliation failed; transaction must be rolled back")
    return mapped.groupby(["scenario", "version_name"]).agg(
        rows=("account_id", "size"), reporting_total_usd=("reporting_amount_usd", "sum")
    ).reset_index()
