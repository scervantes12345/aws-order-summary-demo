"""AWS Lambda entry point for the daily order summary."""

import csv
import io
from decimal import Decimal, InvalidOperation

from order_summary import summarize_rows

REQUIRED_COLUMNS = ('region', 'amount_usd')


def validated_rows(csv_text):
    reader = csv.DictReader(io.StringIO(csv_text, newline=''))
    missing = [column for column in REQUIRED_COLUMNS if column not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(
            f'CSV header is missing required column(s): {", ".join(missing)}; expected order_id,region,amount_usd.'
        )
    for row in reader:
        if None in row or None in row.values():
            raise ValueError(f'CSV line {reader.line_num} does not have exactly {len(reader.fieldnames)} values.')
        try:
            amount = Decimal(row['amount_usd'])
        except InvalidOperation:
            amount = None
        if amount is None or not amount.is_finite():
            raise ValueError(f'CSV line {reader.line_num} has an amount_usd value that is not a number.')
        yield row


def lambda_handler(event, context):
    csv_text = event.get('csv_text') if isinstance(event, dict) else None
    if not isinstance(csv_text, str):
        raise ValueError('Event must be a JSON object with a "csv_text" string.')
    return summarize_rows(validated_rows(csv_text))
