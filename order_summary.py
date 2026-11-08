"""Summarize a daily order export using Python's standard library."""

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path


def summarize_orders(csv_path):
    count = 0
    total = Decimal('0.00')
    by_region = {}
    with csv_path.open(encoding='utf-8', newline='') as source:
        for order in csv.DictReader(source):
            region = order['region']
            amount = Decimal(order['amount_usd'])
            count += 1
            total += amount
            by_region[region] = by_region.get(region, Decimal('0.00')) + amount
    return {
        'order_count': count,
        'revenue_usd': f'{total:.2f}',
        'revenue_by_region_usd': {region: f'{amount:.2f}' for region, amount in sorted(by_region.items())},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_path', type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize_orders(args.csv_path), indent=2))


if __name__ == '__main__':
    main()
