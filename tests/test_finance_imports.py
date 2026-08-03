import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from finance_imports import deduplicate_transactions, parse_csv_file


class FinanceImportParserTests(unittest.TestCase):
    def test_registered_account_identity_deduplicates_renamed_cba_snapshots(self):
        metadata = {
            "id": 7,
            "name": "CBA Eden",
            "ownership": "business",
            "account_type": "cash",
        }
        with TemporaryDirectory() as temp_dir:
            first_path = Path(temp_dir) / "cba-eden-july.csv"
            second_path = Path(temp_dir) / "business-cash-export.csv"
            first_path.write_text(
                '01/07/2026,-20.00,"CAFE",100.00\n'
                '02/07/2026,100.00,"CLIENT PAYMENT",200.00\n',
                encoding="utf-8",
            )
            second_path.write_text(
                '02/07/2026,100.00,"CLIENT PAYMENT",200.00\n'
                '03/07/2026,-40.00,"SUPPLIES",160.00\n',
                encoding="utf-8",
            )

            rows_a = parse_csv_file(first_path, metadata)
            rows_b = parse_csv_file(second_path, metadata)

        combined = deduplicate_transactions(rows_a + rows_b)

        self.assertTrue(all(row["account_key"] == "registered:7" for row in combined))
        self.assertTrue(all(row["ownership"] == "business" for row in combined))
        self.assertEqual(len(combined), 3)

    def test_same_transaction_remains_separate_for_different_registered_accounts(self):
        with TemporaryDirectory() as temp_dir:
            first_path = Path(temp_dir) / "personal.csv"
            second_path = Path(temp_dir) / "business.csv"
            statement = '01/07/2026,-20.00,"CAFE",100.00\n'
            first_path.write_text(statement, encoding="utf-8")
            second_path.write_text(statement, encoding="utf-8")

            personal_rows = parse_csv_file(
                first_path,
                {
                    "id": 1,
                    "name": "Personal cash",
                    "ownership": "personal",
                    "account_type": "cash",
                },
            )
            business_rows = parse_csv_file(
                second_path,
                {
                    "id": 2,
                    "name": "Business cash",
                    "ownership": "business",
                    "account_type": "cash",
                },
            )

        combined = deduplicate_transactions(personal_rows + business_rows)

        self.assertEqual(len(combined), 2)
        self.assertEqual(
            {row["account_key"] for row in combined},
            {"registered:1", "registered:2"},
        )

