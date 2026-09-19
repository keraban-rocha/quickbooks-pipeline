import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.planning import load_plans, map_accounts


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        base = {"Month": "2025-09-01", "Account_Number": "4200", "Account_Name": "Discounts",
                "PL_Section": "Revenue", "Subcategory": "Contra Revenue", "Department": "Sales"}
        self.budget = {**base, "Primary_Driver": "Discount Rate", "Budget_Amount_USD": "10.10",
                       "Budget_Reporting_Amount_USD": "-10.10"}
        self.forecast = {**base, "Forecast_Version": "April snapshot", "Basis": "Actualized",
                         "Forecast_Amount_USD": "12.30", "Forecast_Reporting_Amount_USD": "-12.30"}

    def load(self, budget_rows=None):
        pd.DataFrame(budget_rows or [self.budget]).to_csv(self.path / "budget_detail.csv", index=False)
        pd.DataFrame([self.forecast]).to_csv(self.path / "forecast_detail.csv", index=False)
        return load_plans(self.path, forecast_as_of={"April snapshot": "2026-04-30"})

    def test_fiscal_version_and_contra_sign(self):
        result = self.load()
        self.assertEqual(result.iloc[0].version_name, "FY2026 Original Budget")
        self.assertEqual(str(result.iloc[0].reporting_amount_usd), "-10.10")
        self.assertEqual(result.iloc[1].basis, "Actualized")

    def test_duplicate_grain_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate plan grain"):
            self.load([self.budget, self.budget])

    def test_bad_sign_rejected(self):
        self.budget["Budget_Reporting_Amount_USD"] = "10.10"
        with self.assertRaisesRegex(ValueError, "reporting sign"):
            self.load()

    def test_cutoff_rejected(self):
        self.forecast["Basis"] = "Forecast"
        with self.assertRaisesRegex(ValueError, "as-of month"):
            self.load()

    def test_missing_account_rejected(self):
        accounts = pd.DataFrame([{"account_id": "1", "account_number": "4000", "classification": "Revenue"}])
        with self.assertRaisesRegex(ValueError, "Accounts missing"):
            map_accounts(self.load(), accounts)

    def test_mapping_and_stable_version_keys(self):
        plans = self.load()
        accounts = pd.DataFrame([{"account_id": "QBO-42", "account_number": "4200", "classification": "Revenue"}])
        mapped = map_accounts(plans, accounts)
        self.assertEqual(set(mapped.account_id), {"QBO-42"})
        self.assertEqual(list(plans.version_key), list(self.load().version_key))
