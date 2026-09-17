import json
import uuid

import pandas as pd

from datetime import (
    datetime,
    timezone
)


# ---------------------------
# BRONZE
# ---------------------------

def build_raw_dataframe(
    data,
    batch_id=None,
    extracted_at=None
):

    batch_id = (
        batch_id
        or str(uuid.uuid4())
    )

    extracted_at = (
        extracted_at
        or datetime.now(timezone.utc)
    )

    return pd.DataFrame([
        {
            "entity_id": item.get("Id"),
            "batch_id": batch_id,
            "extracted_at": extracted_at,
            "payload_json": json.dumps(
                item,
                ensure_ascii=False
            )
        }
        for item in data
    ])


# ---------------------------
# SILVER - ACCOUNT
# ---------------------------

def transform_accounts(
    df_raw
):

    rows = []

    for payload in df_raw["payload_json"]:

        account = json.loads(payload)

        rows.append({
            "account_id":
                account.get("Id"),

            "account_name":
                account.get("Name"),

            "account_number":
                account.get("AcctNum"),

            "account_type":
                account.get("AccountType"),

            "account_subtype":
                account.get("AccountSubType"),

            "classification":
                account.get("Classification"),

            "fully_qualified_name":
                account.get(
                    "FullyQualifiedName"
                ),

            "active":
                account.get("Active")
        })

    return pd.DataFrame(rows)


# ---------------------------
# SILVER - CUSTOMERS
# ---------------------------

def transform_customers(
    df_raw
):

    rows = []

    for payload in df_raw["payload_json"]:

        customer = json.loads(payload)

        email = (
            customer
            .get("PrimaryEmailAddr", {})
            .get("Address")
        )

        phone = (
            customer
            .get("PrimaryPhone", {})
            .get("FreeFormNumber")
        )

        address = customer.get(
            "BillAddr",
            {}
        )

        rows.append({
            "customer_id":
                customer.get("Id"),

            "display_name":
                customer.get("DisplayName"),

            "company_name":
                customer.get("CompanyName"),

            "email":
                email,

            "phone":
                phone,

            "city":
                address.get("City"),

            "state":
                address.get(
                    "CountrySubDivisionCode"
                ),

            "postal_code":
                address.get("PostalCode"),

            "country":
                address.get("Country"),

            "active":
                customer.get("Active")
        })

    return pd.DataFrame(rows)


# ---------------------------
# SILVER - GENERAL LEDGER
# ---------------------------

def transform_journal_entries(
    df_raw,
    journal_min=202309,
    journal_max=202608
):

    rows = []

    for payload in df_raw["payload_json"]:

        journal = json.loads(payload)

        journal_no = str(
            journal.get(
                "DocNumber",
                ""
            )
        )

        # Filters only CloudFlow journals
        if not journal_no.isdigit():
            continue

        journal_number = int(
            journal_no
        )

        if not (
            journal_min
            <= journal_number
            <= journal_max
        ):
            continue

        for line in journal.get(
            "Line",
            []
        ):

            detail = line.get(
                "JournalEntryLineDetail",
                {}
            )

            account_ref = detail.get(
                "AccountRef",
                {}
            )

            posting_type = detail.get(
                "PostingType"
            )

            amount = float(
                line.get(
                    "Amount",
                    0
                )
            )

            signed_amount = (
                amount
                if posting_type == "Debit"
                else -amount
            )

            rows.append({
                "journal_id":
                    journal.get("Id"),

                "journal_no":
                    journal_no,

                "txn_date":
                    journal.get("TxnDate"),

                "line_id":
                    line.get("Id"),

                "description":
                    line.get("Description"),

                "account_id":
                    account_ref.get("value"),

                "account_name":
                    account_ref.get("name"),

                "posting_type":
                    posting_type,

                "amount":
                    amount,

                "signed_amount":
                    signed_amount
            })

    df = pd.DataFrame(rows)

    if not df.empty:

        df["txn_date"] = pd.to_datetime(
            df["txn_date"]
        )

        df["year"] = (
            df["txn_date"].dt.year
        )

        df["month"] = (
            df["txn_date"].dt.month
        )

        df["year_month"] = (
            df["txn_date"]
            .dt.to_period("M")
            .astype(str)
        )

    return df


# ---------------------------
# GOLD
# ---------------------------

def build_monthly_pnl(
    fact_gl,
    dim_account
):

    df = fact_gl.merge(
        dim_account[
            [
                "account_id",
                "account_type",
                "classification"
            ]
        ],
        on="account_id",
        how="left"
    )

    pnl = (
        df
        .groupby(
            [
                "year_month",
                "account_id",
                "account_name",
                "account_type",
                "classification"
            ],
            dropna=False
        )
        ["signed_amount"]
        .sum()
        .reset_index()
    )

    return pnl