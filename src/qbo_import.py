"""CSV validation and create-only imports for the QuickBooks import notebook."""

import csv
import json
import time
import uuid
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from src import config
from src.quickbooks import get_access_token


DETAIL_TYPES = {
    "Checking": "Checking",
    "Service/Fee Income": "ServiceFeeIncome",
    "Discounts/Refunds Given": "DiscountsRefundsGiven",
    "Other Costs of Services - COS": "OtherCostsOfServiceCos",
    "Cost of labor - COS": "CostOfLaborCos",
    "Payroll Wage Expenses": "PayrollWageExpenses",
    "Payroll Tax Expenses": "PayrollTaxExpenses",
    "Commissions and fees": "CommissionsAndFees",
    "Advertising/Promotional": "AdvertisingPromotional",
    "Dues & subscriptions": "DuesSubscriptions",
    "Cost of Labor": "CostOfLabor",
    "Other business expenses": "OtherBusinessExpenses",
    "Legal & Professional Fees": "LegalProfessionalFees",
    "Insurance": "Insurance",
    "Rent or Lease of Buildings": "RentOrLeaseOfBuildings",
    "Taxes Paid": "TaxesPaid",
    "Other Miscellaneous Income": "OtherMiscellaneousIncome",
    "Interest Paid": "InterestPaid",
}


def read_csv(path, required):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing columns {sorted(missing)}")
        rows = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path}:{reader.line_num}: malformed CSV row")
            rows.append({key: value.strip() for key, value in row.items()})
    if not rows:
        raise ValueError(f"{path}: empty input")
    return rows


def validate_unique(records, field):
    seen = set()
    for record in records:
        key = record[field].casefold()
        if not key or key in seen:
            raise ValueError(f"Missing or duplicate {field}: {record[field]!r}")
        seen.add(key)


def money(value):
    try:
        amount = Decimal(value or "0")
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
            raise ValueError(f"Invalid amount: {value!r}")
        return amount
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {value!r}") from exc


def load_import_data(data_dir, *, account_file="qbo_chart_of_accounts.csv",
                     journal_files=("qbo_journal_import_part1.csv", "qbo_journal_import_part2.csv"),
                     customer_file=None):
    """Validate selected CSVs; customer import is optional and disabled by default."""
    data_dir = Path(data_dir)
    accounts = []
    for row in read_csv(data_dir / account_file,
                        ["Account Name", "Account Number", "Type", "Detail Type"]):
        subtype = DETAIL_TYPES.get(row["Detail Type"])
        if not subtype:
            raise ValueError(f"Unsupported detail type: {row['Detail Type']}")
        if row["Type"] == "Other Expense":
            subtype = "OtherMiscellaneousExpense"
        accounts.append({"Name": row["Account Name"], "AcctNum": row["Account Number"],
                         "AccountType": row["Type"], "AccountSubType": subtype})
    validate_unique(accounts, "Name")
    validate_unique(accounts, "AcctNum")
    customers = [
        {"DisplayName": row["Customer_ID"],
         "Notes": "; ".join(f"{key}={value}" for key, value in row.items() if value)}
        for row in (read_csv(data_dir / customer_file, ["Customer_ID"]) if customer_file else [])
    ]
    validate_unique(customers, "DisplayName")
    account_names = {item["Name"] for item in accounts}
    customer_names = {item["DisplayName"] for item in customers}
    journals, owners = {}, {}
    paths = [data_dir / name for name in journal_files]
    if not paths:
        raise ValueError("Select at least one journal CSV file")
    if len(set(path.resolve() for path in paths)) != len(paths):
        raise ValueError("A journal CSV file was selected more than once")
    for path in paths:
        for row in read_csv(path, ["*JournalNo", "*JournalDate", "*AccountName", "Debits", "Credits"]):
            number = row["*JournalNo"]
            if not number or len(number) > 21:
                raise ValueError(f"Invalid journal number: {number!r}")
            if number in owners and owners[number] != path:
                raise ValueError(f"Journal {number} appears in multiple files")
            owners[number] = path
            date = datetime.strptime(row["*JournalDate"], "%m/%d/%Y").date().isoformat()
            for field in ("TaxCode", "Location", "Class"):
                if row.get(field):
                    raise ValueError(f"Journal {number}: {field} is not supported yet")
            if row["*AccountName"] not in account_names:
                raise ValueError(f"Journal {number}: unknown account {row['*AccountName']}")
            debit, credit = money(row["Debits"]), money(row["Credits"])
            if (debit > 0) == (credit > 0):
                raise ValueError(f"Journal {number}: each line needs one positive debit or credit")
            journal = journals.setdefault(number, {"DocNumber": number, "TxnDate": date,
                                                   "PrivateNote": row.get("Memo", ""), "Line": []})
            if journal["TxnDate"] != date or journal["PrivateNote"] != row.get("Memo", ""):
                raise ValueError(f"Journal {number}: inconsistent date or memo")
            detail = {"PostingType": "Debit" if debit else "Credit",
                      "AccountRef": {"value": row["*AccountName"]}}
            if row.get("Name"):
                if not customer_file:
                    raise ValueError(f"Journal {number}: Name requires an optional customer file; "
                                     "the selected account/journal-only import expects Name to be blank")
                if row["Name"] not in customer_names:
                    raise ValueError(f"Journal {number}: unknown customer {row['Name']}")
                detail["Entity"] = {"Type": "Customer", "EntityRef": {"value": row["Name"]}}
            journal["Line"].append({"Amount": float(debit or credit),
                                    "Description": row.get("Description", ""),
                                    "DetailType": "JournalEntryLineDetail",
                                    "JournalEntryLineDetail": detail})
    for journal in journals.values():
        balance = sum(Decimal(str(line["Amount"])) *
                      (1 if line["JournalEntryLineDetail"]["PostingType"] == "Debit" else -1)
                      for line in journal["Line"])
        if balance or len(journal["Line"]) < 2:
            raise ValueError(f"Journal {journal['DocNumber']} is unbalanced: {balance}")
    return accounts, customers, list(journals.values())


class QBOClient:
    def __init__(self):
        if not all((config.QBO_CLIENT_ID, config.QBO_CLIENT_SECRET, config.QBO_REALM_ID)):
            raise ValueError("Set QBO_CLIENT_ID, QBO_CLIENT_SECRET and QBO_REALM_ID in .env")
        if not config.QBO_BASE_URL.startswith("https://sandbox-quickbooks.api.intuit.com/"):
            raise ValueError("This importer currently supports the project sandbox only")
        self.session = requests.Session()
        self.refresh()

    def refresh(self):
        self.session.headers.update({"Authorization": f"Bearer {get_access_token()}",
                                     "Accept": "application/json", "Content-Type": "application/json"})

    def request(self, method, endpoint, **kwargs):
        for attempt in range(4):
            try:
                response = self.session.request(method, f"{config.QBO_BASE_URL}/{endpoint}",
                                                timeout=60, **kwargs)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 401 and attempt < 3:
                self.refresh()
                continue
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 3:
                try:
                    delay = float(response.headers.get("Retry-After", "2"))
                except ValueError:
                    delay = 2
                time.sleep(min(60, max(2 ** attempt, delay)))
                continue
            if not response.ok:
                raise RuntimeError(f"QBO {endpoint}: HTTP {response.status_code}; "
                                   f"intuit_tid={response.headers.get('intuit_tid')}; {response.text}")
            result = response.json()
            if "Fault" in result:
                raise RuntimeError(f"QBO {endpoint}: {result['Fault']}")
            return result
        raise RuntimeError("QBO retries exhausted")

    def query_all(self, entity):
        records = []
        while True:
            active = " WHERE Active IN (true, false)" if entity in ("Account", "Customer") else ""
            query = f"SELECT * FROM {entity}{active} STARTPOSITION {len(records) + 1} MAXRESULTS 1000"
            page = self.request("GET", "query", params={"query": query}).get("QueryResponse", {}).get(entity, [])
            records.extend(page)
            if len(page) < 1000:
                return records

    def create(self, entity, payload):
        identity = config.QBO_BASE_URL + entity + json.dumps(payload, sort_keys=True)
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
        result = self.request("POST", entity.lower(), params={"requestid": request_id}, json=payload)
        record = result[entity]
        if not record.get("Id"):
            raise RuntimeError(f"QBO returned no Id for {entity}")
        return record


def matches(expected, actual):
    """Compare supplied fields only; ignore server-generated fields."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(matches(value, actual.get(key, "")) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(matches(a, b) for a, b in zip(expected, actual))
    return expected == actual


def find_existing(records, field, key):
    found = [record for record in records if str(record.get(field, "")).casefold() == key.casefold()]
    if len(found) > 1:
        raise ValueError(f"Ambiguous existing {field}: {key}")
    return found[0] if found else None


def import_records(accounts, customers, journals, client):
    """Preflight remote records, then create missing entities in dependency order."""
    entities = ["Account", "JournalEntry"] + (["Customer"] if customers else [])
    existing = {entity: client.query_all(entity) for entity in entities}
    mappings = {"Account": {}, "Customer": {}}
    planned, results = [], []
    for entity, records, field in (("Account", accounts, "Name"), ("Customer", customers, "DisplayName")):
        for payload in records:
            key = payload[field]
            match = find_existing(existing[entity], "FullyQualifiedName" if entity == "Account" else field, key)
            if entity == "Account":
                numbered = find_existing(existing[entity], "AcctNum", payload["AcctNum"])
                if numbered and (not match or numbered["Id"] != match["Id"]):
                    raise ValueError(f"Account number collision: {payload['AcctNum']}")
            if match:
                check = dict(payload)
                if entity == "Account" and not match.get("AcctNum"):
                    check.pop("AcctNum")
                if not match.get("Active", True) or not matches(check, match):
                    raise ValueError(f"Existing {entity} conflicts with source: {key}")
                mappings[entity][key] = match["Id"]
                results.append({"entity": entity, "key": key, "id": match["Id"], "status": "reused"})
            else:
                mappings[entity][key] = f"pending:{key}"
                planned.append((entity, key, payload))

    def resolve(journal):
        payload = deepcopy(journal)
        for line in payload["Line"]:
            detail = line["JournalEntryLineDetail"]
            refs = [("Account", detail["AccountRef"])]
            if "Entity" in detail:
                refs.append(("Customer", detail["Entity"]["EntityRef"]))
            for entity, ref in refs:
                name = ref["value"]
                if name not in mappings[entity]:
                    raise ValueError(f"Journal {journal['DocNumber']}: unknown {entity} {name}")
                ref["value"] = mappings[entity][name]
        return payload

    pending_journals = []
    for journal in journals:
        payload = resolve(journal)
        match = find_existing(existing["JournalEntry"], "DocNumber", journal["DocNumber"])
        if match and not matches(payload, match):
            raise ValueError(f"Existing journal conflicts with source: {journal['DocNumber']}")
        if match:
            results.append({"entity": "JournalEntry", "key": journal["DocNumber"], "id": match["Id"], "status": "reused"})
        else:
            pending_journals.append(journal)
    print(f"To create: {len(planned)} master records, {len(pending_journals)} journals.", flush=True)
    for entity, key, payload in planned:
        record = client.create(entity, payload)
        mappings[entity][key] = record["Id"]
        results.append({"entity": entity, "key": key, "id": record["Id"], "status": "created"})
        print(f"Created {entity} {key}: Id={record['Id']}", flush=True)
    for journal in pending_journals:
        record = client.create("JournalEntry", resolve(journal))
        results.append({"entity": "JournalEntry", "key": journal["DocNumber"], "id": record["Id"], "status": "created"})
        print(f"Created JournalEntry {journal['DocNumber']}: Id={record['Id']}", flush=True)
    return results
