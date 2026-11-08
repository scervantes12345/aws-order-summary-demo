# Daily order summary

This interview demo starts with a local Python job that summarizes a CSV export. All orders are synthetic. Run it with Python 3 from this directory.

```sh
python3 order_summary.py orders.csv
```

The five sample orders sum to $400.00. East is $150.00, West is $150.00, and Central is $100.00. See `expected_summary.json` for the baseline fixture. Revenue uses decimal arithmetic and is represented as a string with two decimal places.
