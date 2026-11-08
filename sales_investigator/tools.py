"""Read-only analysis tools over the synthetic two-day sales fixture.

Every tool returns JSON-serializable data. Money is a string with two decimals and
percentages are strings with one decimal, computed with decimal arithmetic.
"""

import csv
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / 'data' / 'sales_two_day.csv'
# The fixture's "today". "Yesterday" is therefore 2026-11-07, compared with 2026-11-06.
AS_OF_DATE = '2026-11-08'
CENT = Decimal('0.01')
TENTH = Decimal('0.1')


class ToolInputError(ValueError):
    """A tool request was rejected before or during execution."""


@lru_cache(maxsize=1)
def _rows():
    with DATA_PATH.open(encoding='utf-8', newline='') as source:
        return tuple(
            (row['date'], row['region'], row['product'], Decimal(row['revenue_usd']))
            for row in csv.DictReader(source)
        )


def _ordered(values):
    return list(dict.fromkeys(values))


def dates():
    return sorted(set(row[0] for row in _rows()))


def regions():
    return _ordered(row[1] for row in _rows())


def products():
    return _ordered(row[2] for row in _rows())


def _money(amount):
    return f'{amount.quantize(CENT, rounding=ROUND_HALF_UP):.2f}'


def _pct(part, whole):
    if whole == 0:
        return None
    return f'{(part / whole * 100).quantize(TENTH, rounding=ROUND_HALF_UP):.1f}'


def _revenue(date, region=None, product=None):
    return sum(
        (amount for row_date, row_region, row_product, amount in _rows()
         if row_date == date and region in (None, row_region) and product in (None, row_product)),
        Decimal('0.00'),
    )


def _check_dates(baseline_date, current_date):
    if baseline_date == current_date:
        raise ToolInputError('baseline_date and current_date must be different days.')


def list_datasets():
    return {
        'as_of_date': AS_OF_DATE,
        'datasets': [{
            'name': 'daily_sales',
            'description': 'Synthetic revenue in USD by date, region, and product.',
            'fields': ['date', 'region', 'product', 'revenue_usd'],
            'dates': dates(),
            'regions': regions(),
            'products': products(),
        }],
    }


def compare_region_revenue(baseline_date, current_date):
    _check_dates(baseline_date, current_date)
    comparison = []
    for region in regions():
        before, after = _revenue(baseline_date, region), _revenue(current_date, region)
        comparison.append({
            'region': region,
            'baseline_revenue_usd': _money(before),
            'current_revenue_usd': _money(after),
            'change_usd': _money(after - before),
            'change_pct': _pct(after - before, before),
        })
    return {'baseline_date': baseline_date, 'current_date': current_date, 'regions': comparison}


def attribute_change_by_product(region, baseline_date, current_date):
    _check_dates(baseline_date, current_date)
    region_change = _revenue(current_date, region) - _revenue(baseline_date, region)
    attribution = []
    for product in products():
        before = _revenue(baseline_date, region, product)
        after = _revenue(current_date, region, product)
        attribution.append({
            'product': product,
            'baseline_revenue_usd': _money(before),
            'current_revenue_usd': _money(after),
            'change_usd': _money(after - before),
            'share_of_region_change_pct': _pct(after - before, region_change),
            '_change': after - before,
        })
    # Largest move in the same direction as the region's change comes first.
    attribution.sort(key=lambda item: item['_change'] if region_change <= 0 else -item['_change'])
    for item in attribution:
        del item['_change']
    return {
        'region': region,
        'baseline_date': baseline_date,
        'current_date': current_date,
        'region_change_usd': _money(region_change),
        'products': attribution,
    }


def _date_property(description):
    return {'type': 'string', 'enum': dates(), 'description': description}


def tool_specs():
    """Converse-style tool specs. The JSON schemas are also used to validate inputs."""
    date_pair = {
        'baseline_date': _date_property('Earlier day to compare from (YYYY-MM-DD).'),
        'current_date': _date_property('Later day to compare to (YYYY-MM-DD).'),
    }
    return {
        'list_datasets': {
            'description': 'List the datasets available to the investigator, with their dates, regions, and products.',
            'schema': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
        },
        'compare_region_revenue': {
            'description': 'Compare total revenue for every region between two days.',
            'schema': {
                'type': 'object', 'properties': date_pair,
                'required': ['baseline_date', 'current_date'], 'additionalProperties': False,
            },
        },
        'attribute_change_by_product': {
            'description': "Break one region's revenue change between two days down by product.",
            'schema': {
                'type': 'object',
                'properties': {
                    'region': {'type': 'string', 'enum': regions(), 'description': 'Region to break down.'},
                    **date_pair,
                },
                'required': ['region', 'baseline_date', 'current_date'], 'additionalProperties': False,
            },
        },
    }


DATA_TOOLS = {
    'list_datasets': list_datasets,
    'compare_region_revenue': compare_region_revenue,
    'attribute_change_by_product': attribute_change_by_product,
}
