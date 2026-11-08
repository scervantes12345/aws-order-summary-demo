import json
import subprocess
import sys
import unittest
from pathlib import Path

from lambda_function import lambda_handler
from order_summary import summarize_orders

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

    def test_header_only_csv_is_zero(self):
        self.assertEqual(
            lambda_handler({'csv_text': 'order_id,region,amount_usd\n'}, None),
            {'order_count': 0, 'revenue_usd': '0.00', 'revenue_by_region_usd': {}},
        )

    def test_rejects_missing_or_non_string_csv_text(self):
        for event in ({}, {'csv_text': 123}, None, 'order_id,region,amount_usd'):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'csv_text'):
                lambda_handler(event, None)

    def test_rejects_missing_header_columns(self):
        for csv_text in ('', 'order_id,region\n', 'order_id,region\n1,East\n'):
            with self.subTest(csv_text=csv_text), self.assertRaisesRegex(ValueError, 'missing required column'):
                lambda_handler({'csv_text': csv_text}, None)

    def test_rejects_non_numeric_amount(self):
        for amount in ('abc', '', 'NaN', 'Infinity', '-inf', 'sNaN'):
            with self.subTest(amount=amount), self.assertRaisesRegex(ValueError, 'line 2 .*not a number'):
                lambda_handler({'csv_text': f'order_id,region,amount_usd\n1,East,{amount}\n'}, None)

    def test_rejects_rows_with_wrong_number_of_values(self):
        for csv_text in (
            'order_id,region,amount_usd\n1,East\n',
            'order_id,region,amount_usd\n1,East,1,000.00\n',
            'order_id,amount_usd,region\n1,10.00\n',
        ):
            with self.subTest(csv_text=csv_text), self.assertRaisesRegex(ValueError, 'line 2 does not have exactly 3 values'):
                lambda_handler({'csv_text': csv_text}, None)


if __name__ == '__main__':
    unittest.main()
