"""AWS Lambda entry point for the daily order summary."""

from decimal import InvalidOperation

from order_summary import summarize_csv_text


def lambda_handler(event, context):
    csv_text = event.get('csv_text') if isinstance(event, dict) else None
    if not isinstance(csv_text, str):
        raise ValueError('Event must be a JSON object with a "csv_text" string.')
    try:
        return summarize_csv_text(csv_text)
    except KeyError as error:
        raise ValueError(f'CSV is missing required column {error}; expected order_id,region,amount_usd.') from None
    except InvalidOperation:
        raise ValueError('CSV contains an amount_usd value that is not a number.') from None
    except TypeError:
        raise ValueError('CSV contains a row with missing values.') from None
