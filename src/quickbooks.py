import json
import requests

from src.config import (
    QBO_CLIENT_ID,
    QBO_CLIENT_SECRET,
    QBO_REALM_ID,
    QBO_TOKEN_URL,
    QBO_BASE_URL,
    TOKEN_FILE,
)


def load_refresh_token():

    with open(TOKEN_FILE, "r") as file:
        data = json.load(file)

    return data["refresh_token"]


def save_refresh_token(refresh_token):

    TOKEN_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(TOKEN_FILE, "w") as file:
        json.dump(
            {"refresh_token": refresh_token},
            file,
            indent=4
        )


def get_access_token():

    response = requests.post(
        QBO_TOKEN_URL,
        auth=(
            QBO_CLIENT_ID,
            QBO_CLIENT_SECRET
        ),
        headers={
            "Accept": "application/json",
            "Content-Type":
                "application/x-www-form-urlencoded"
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": load_refresh_token()
        },
        timeout=30
    )

    response.raise_for_status()

    tokens = response.json()

    # Intuit rotates refresh tokens
    save_refresh_token(
        tokens["refresh_token"]
    )

    return tokens["access_token"]


def get_headers():

    return {
        "Authorization":
            f"Bearer {get_access_token()}",
        "Accept": "application/json"
    }


def get_company_info():

    response = requests.get(
        f"{QBO_BASE_URL}/companyinfo/{QBO_REALM_ID}",
        headers=get_headers(),
        timeout=30
    )

    response.raise_for_status()

    return response.json()["CompanyInfo"]


def qbo_query(query):

    response = requests.get(
        f"{QBO_BASE_URL}/query",
        headers=get_headers(),
        params={
            "query": query
        },
        timeout=60
    )

    response.raise_for_status()

    return response.json()["QueryResponse"]


def get_accounts():

    return qbo_query(
        "SELECT * FROM Account MAXRESULTS 1000"
    ).get("Account", [])


def get_customers():

    return qbo_query(
        "SELECT * FROM Customer MAXRESULTS 1000"
    ).get("Customer", [])


def get_journal_entries(
    start_date="2023-09-01",
    end_date="2026-08-31"
):

    query = f"""
        SELECT * FROM JournalEntry
        WHERE TxnDate >= '{start_date}'
        AND TxnDate <= '{end_date}'
        MAXRESULTS 1000
    """

    return qbo_query(query).get(
        "JournalEntry",
        []
    )