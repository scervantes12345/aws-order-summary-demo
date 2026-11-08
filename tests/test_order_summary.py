import json
import subprocess
import sys
import unittest
from pathlib import Path

from lambda_function import lambda_handler
from order_summary import summarize_csv_text, summarize_orders

ROOT = Path(__file__).resolve().parent.parent
ORDERS_CSV = ROOT / 'orders.csv'
EXPECTED = json.loads((ROOT / 'expected_summary.json').read_text(encoding='utf-8'))
SAMPLE_EVENT = json.loads((ROOT / 'events' / 'sample_event.json').read_text(encoding='utf-8'))


class SummaryLogicTest(unittest.TestCase):
    def test_expected_fixture_is_five_orders_and_400(self):
        self.assertEqual(EXPECTED['order_count'], 5)
        self.assertEqual(EXPECTED['revenue_usd'], '400.00')

    def test_file_summary_matches_expected(self):
        self.assertEqual(summarize_orders(ORDERS_CSV), EXPECTED)

    def test_text_summary_matches_expected(self):
        self.assertEqual(summarize_csv_text(ORDERS_CSV.read_text(encoding='utf-8')), EXPECTED)

    def test_header_only_csv_is_zero(self):
        self.assertEqual(
            summarize_csv_text('order_id,region,amount_usd\n'),
            {'order_count': 0, 'revenue_usd': '0.00', 'revenue_by_region_usd': {}},
        )


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, 'order_summary.py', *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_cli_prints_expected_summary(self):
        result = self.run_cli('orders.csv')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), EXPECTED)
        self.assertEqual(result.stdout, json.dumps(EXPECTED, indent=2) + '\n')

    def test_cli_requires_csv_path(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn('csv_path', result.stderr)


class LambdaHandlerTest(unittest.TestCase):
    def test_sample_event_uses_sample_csv(self):
        self.assertEqual(SAMPLE_EVENT['csv_text'], ORDERS_CSV.read_text(encoding='utf-8'))

    def test_sample_event_returns_expected_summary(self):
        self.assertEqual(lambda_handler(SAMPLE_EVENT, None), EXPECTED)

    def test_result_is_json_serializable(self):
        self.assertEqual(json.loads(json.dumps(lambda_handler(SAMPLE_EVENT, None))), EXPECTED)

    def test_rejects_missing_or_non_string_csv_text(self):
        for event in ({}, {'csv_text': 123}, None, 'order_id,region,amount_usd'):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'csv_text'):
                lambda_handler(event, None)

    def test_rejects_missing_column(self):
        with self.assertRaisesRegex(ValueError, 'amount_usd'):
            lambda_handler({'csv_text': 'order_id,region\n1,East\n'}, None)

    def test_rejects_non_numeric_amount(self):
        with self.assertRaisesRegex(ValueError, 'not a number'):
            lambda_handler({'csv_text': 'order_id,region,amount_usd\n1,East,abc\n'}, None)

    def test_rejects_short_row(self):
        with self.assertRaisesRegex(ValueError, 'missing values'):
            lambda_handler({'csv_text': 'order_id,region,amount_usd\n1,East\n'}, None)


if __name__ == '__main__':
    unittest.main()
