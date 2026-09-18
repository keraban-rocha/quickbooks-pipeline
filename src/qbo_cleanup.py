import time
import requests

from src.config import QBO_BASE_URL
from src.quickbooks import (
    get_access_token,
    get_company_info,
)


TRANSACTION_ENTITIES = [
    "Payment",
    "BillPayment",
    "RefundReceipt",
    "CreditMemo",
    "SalesReceipt",
    "Deposit",
    "Purchase",
    "VendorCredit",
    "Invoice",
    "Bill",
    "Transfer",
    "JournalEntry",
    "Estimate",
    "PurchaseOrder",
    "TimeActivity",
    "InventoryAdjustment",
    "TaxPayment",
    "StatementCharge",
    "DelayedCharge",
]


def get_headers(access_token):
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def query_all(entity, access_token):

    response = requests.get(
        f"{QBO_BASE_URL}/query",
        headers=get_headers(access_token),
        params={
            "query":
                f"SELECT * FROM {entity} MAXRESULTS 1000"
        },
        timeout=60,
    )

    if not response.ok:
        print(
            f"\nQuery failed for {entity}"
            f"\nHTTP {response.status_code}"
            f"\n{response.text}\n"
        )
        return None

    return (
        response.json()
        .get("QueryResponse", {})
        .get(entity, [])
    )


def delete_entity(
    entity,
    entity_id,
    sync_token,
    access_token,
):

    endpoint = entity.lower()

    response = requests.post(
        f"{QBO_BASE_URL}/{endpoint}",
        params={
            "operation": "delete"
        },
        headers=get_headers(access_token),
        json={
            "Id": str(entity_id),
            "SyncToken": str(sync_token),
        },
        timeout=30,
    )

    return response


def wipe_transactions(max_passes=5):

    company = get_company_info()

    print(
        "Cleaning transactions from:",
        company["CompanyName"]
    )

    if "sandbox-quickbooks" not in QBO_BASE_URL:
        raise RuntimeError(
            "Cleanup blocked: not a sandbox."
        )

    access_token = get_access_token()

    for pass_number in range(
        1,
        max_passes + 1
    ):

        print(
            f"\n--- Pass {pass_number} ---"
        )

        deleted = 0

        for entity in TRANSACTION_ENTITIES:

            records = query_all(
                entity,
                access_token
            )

            if records is None:
                print(
                    f"{entity}: unsupported"
                )
                continue

            if not records:
                continue

            print(
                f"{entity}: {len(records)} found"
            )

            for item in records:

                response = delete_entity(
                    entity,
                    item["Id"],
                    item.get(
                        "SyncToken",
                        "0"
                    ),
                    access_token
                )

                if response.ok:

                    deleted += 1

                    print(
                        f"  Deleted "
                        f"{entity} "
                        f"{item['Id']}"
                    )

                else:
                    print(
                        f"  FAILED {entity} {item['Id']}"
                        f"\n  HTTP {response.status_code}"
                        f"\n  {response.text}\n"
                    )

        if deleted == 0:
            break

        time.sleep(2)

    print("\nTransaction cleanup finished.")