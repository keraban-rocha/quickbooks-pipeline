import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


# QuickBooks
QBO_CLIENT_ID = os.getenv("QBO_CLIENT_ID")
QBO_CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")
QBO_REALM_ID = os.getenv("QBO_REALM_ID")

QBO_TOKEN_URL = (
    "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
)

QBO_BASE_URL = (
    f"https://sandbox-quickbooks.api.intuit.com"
    f"/v3/company/{QBO_REALM_ID}"
)

TOKEN_FILE = PROJECT_ROOT / "tokens" / "qbo_tokens.json"


# Azure SQL
AZURE_SQL_SERVER = os.getenv("AZURE_SQL_SERVER")
AZURE_SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE")
AZURE_SQL_USERNAME = os.getenv("AZURE_SQL_USERNAME")
AZURE_SQL_PASSWORD = os.getenv("AZURE_SQL_PASSWORD")


def validate_config():

    required = {
        "QBO_CLIENT_ID": QBO_CLIENT_ID,
        "QBO_CLIENT_SECRET": QBO_CLIENT_SECRET,
        "QBO_REALM_ID": QBO_REALM_ID,
        "AZURE_SQL_SERVER": AZURE_SQL_SERVER,
        "AZURE_SQL_DATABASE": AZURE_SQL_DATABASE,
        "AZURE_SQL_USERNAME": AZURE_SQL_USERNAME,
        "AZURE_SQL_PASSWORD": AZURE_SQL_PASSWORD,
    }

    missing = [
        key
        for key, value in required.items()
        if not value
    ]

    if missing:
        raise ValueError(
            f"Missing environment variables: {missing}"
        )

    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"QuickBooks token file not found: {TOKEN_FILE}"
        )