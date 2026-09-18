import copy
import unittest

from src.qbo_import import import_records, money, QBOClient


class FakeClient:
    def __init__(self):
        self.records = {name: [] for name in ("Account", "Customer", "JournalEntry")}
        self.calls = []

    def query_all(self, entity):
        return copy.deepcopy(self.records[entity])

    def create(self, entity, payload):
        record = copy.deepcopy(payload)
        record["Id"] = str(len(self.calls) + 1)
        if entity == "Account":
            record["FullyQualifiedName"] = record["Name"]
        self.records[entity].append(record)
        self.calls.append((entity, payload))
        return record


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [{"Name": "Checking", "AcctNum": "1000", "AccountType": "Bank", "AccountSubType": "Checking"}]
        self.customers = [{"DisplayName": "C001", "Notes": "Customer_ID=C001"}]
        self.journal = {"DocNumber": "202309", "TxnDate": "2023-09-30", "PrivateNote": "", "Line": [
            {"Amount": 10.0, "DetailType": "JournalEntryLineDetail", "Description": "",
             "JournalEntryLineDetail": {"PostingType": side, "AccountRef": {"value": "Checking"}}}
            for side in ("Debit", "Credit")]}

    def test_order_references_and_rerun(self):
        client = FakeClient()
        import_records(self.accounts, self.customers, [self.journal], client)
        self.assertEqual([entity for entity, _ in client.calls], ["Account", "Customer", "JournalEntry"])
        self.assertEqual(client.calls[-1][1]["Line"][0]["JournalEntryLineDetail"]["AccountRef"]["value"], "1")
        result = import_records(self.accounts, self.customers, [self.journal], client)
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(all(row["status"] == "reused" for row in result))

    def test_conflicting_journal_blocks_all_writes(self):
        client = FakeClient()
        client.records["JournalEntry"] = [{"DocNumber": "202309", "TxnDate": "2024-01-01"}]
        with self.assertRaisesRegex(ValueError, "conflicts"):
            import_records(self.accounts, self.customers, [self.journal], client)
        self.assertEqual(client.calls, [])

    def test_partial_import_resumes(self):
        client = FakeClient()
        import_records(self.accounts, [], [], client)
        import_records(self.accounts, self.customers, [self.journal], client)
        self.assertEqual(len(client.records["Account"]), 1)
        self.assertEqual(len(client.records["JournalEntry"]), 1)

    def test_money_rejects_invalid_values(self):
        for value in ("NaN", "Infinity", "-1", "0.001", "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money(value)

    def test_pagination_includes_inactive_records(self):
        client = QBOClient.__new__(QBOClient)
        queries = []
        def request(method, endpoint, **kwargs):
            queries.append(kwargs["params"]["query"])
            return {"QueryResponse": {"Customer": [{}] * (1000 if len(queries) == 1 else 1)}}
        client.request = request
        self.assertEqual(len(client.query_all("Customer")), 1001)
        self.assertIn("STARTPOSITION 1001", queries[1])
        self.assertIn("Active IN (true, false)", queries[0])


if __name__ == "__main__":
    unittest.main()
