import unittest
from unittest.mock import MagicMock, patch

from src.azure_sql_cleanup import execute_cleanup, quote_identifier


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.engine = MagicMock()
        self.plan = {"identity": {"database_name": "test_db"}, "blockers": [],
                     "tables": [{"name": "example"}], "schemas": [],
                     "statements": ["DROP TABLE [dbo].[example];"]}

    def test_identifier_escaping(self):
        self.assertEqual(quote_identifier("a]b"), "[a]]b]")

    def test_wrong_confirmation_never_connects(self):
        with self.assertRaises(ValueError):
            execute_cleanup(self.engine, self.plan, "other_db")
        self.engine.begin.assert_not_called()

    def test_blockers_never_connect(self):
        self.plan["blockers"] = [{"name": "view"}]
        with self.assertRaises(ValueError):
            execute_cleanup(self.engine, self.plan, "test_db")
        self.engine.begin.assert_not_called()

    @patch("src.azure_sql_cleanup.inspect_cleanup")
    def test_changed_plan_aborts_transaction(self, inspect):
        inspect.return_value = {**self.plan, "schemas": [{"name": "new"}]}
        with self.assertRaises(ValueError):
            execute_cleanup(self.engine, self.plan, "test_db")
        connection = self.engine.begin.return_value.__enter__.return_value
        self.assertEqual(connection.execute.call_count, 1)  # Settings only, no DROP.
        self.assertIs(self.engine.begin.return_value.__exit__.call_args.args[0], ValueError)

    @patch("src.azure_sql_cleanup.inspect_cleanup")
    def test_verified_cleanup_commits(self, inspect):
        inspect.side_effect = [self.plan, {"tables": [], "schemas": []}]
        result = execute_cleanup(self.engine, self.plan, "test_db")
        self.assertEqual(result, {"tables_dropped": 1, "schemas_dropped": 0})
        self.engine.begin.return_value.__exit__.assert_called_once_with(None, None, None)

    @patch("src.azure_sql_cleanup.inspect_cleanup")
    def test_verification_failure_rolls_back(self, inspect):
        inspect.side_effect = [self.plan, self.plan]
        with self.assertRaises(RuntimeError):
            execute_cleanup(self.engine, self.plan, "test_db")
        self.assertIs(self.engine.begin.return_value.__exit__.call_args.args[0], RuntimeError)
