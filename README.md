# Daily order summary

This interview demo starts with a local Python job that summarizes a CSV export. All orders are synthetic. Run it with Python 3 from this directory.

```sh
python3 order_summary.py orders.csv
```

The five sample orders sum to $400.00. East is $150.00, West is $150.00, and Central is $100.00. See `expected_summary.json` for the baseline fixture. Revenue uses decimal arithmetic and is represented as a string with two decimal places.

## Run the tests

The tests use only the Python standard library (`unittest`). They check the CLI output, the shared calculation logic, and the Lambda handler against `expected_summary.json`.

```sh
python3 -m unittest discover -s tests -t . -v
```

## Run on AWS Lambda

`lambda_function.py` wraps the same `order_summary.py` logic used by the CLI. It expects a JSON event with the CSV contents in a `csv_text` string and returns the summary as JSON:

```json
{"csv_text": "order_id,region,amount_usd\n1001,East,120.50\n..."}
```

A ready-to-use test event built from `orders.csv` is in `events/sample_event.json`. If `csv_text` is missing, or the CSV is malformed, the function raises a `ValueError` with a plain-language message, which Lambda reports as a function error.

The steps below use only the AWS Management Console in a browser and do not create a public endpoint. The function can only be invoked by signed-in AWS users with permission to call it.

### Deploy in the Lambda console

1. Open the AWS console, go to **Lambda**, and choose **Create function**.
2. Choose **Author from scratch**, enter a name such as `order-summary-demo`, and pick a **Python 3.12** (or newer) runtime.
3. Under **Permissions**, keep **Create a new role with basic Lambda permissions**. This only allows the function to write its own logs to CloudWatch.
4. Under **Additional configurations**, leave **Enable function URL** unchecked. Do not add any triggers (such as API Gateway); this keeps the function private.
5. Choose **Create function**.
6. Add the code, using one of these options:
   - **Code editor:** replace the contents of the default `lambda_function.py` with this repository's `lambda_function.py`. Create a new file named `order_summary.py` next to it and paste in this repository's `order_summary.py`. Choose **Deploy**.
   - **Zip upload:** on your computer, run `zip order-summary-lambda.zip lambda_function.py order_summary.py`, then in the **Code** tab choose **Upload from** > **.zip file** and upload it.
7. Under **Runtime settings**, confirm the handler is `lambda_function.lambda_handler` (the default).

### Test in the Lambda console

1. Open the **Test** tab and choose **Create new event**.
2. Name it `sample-orders` and paste the contents of `events/sample_event.json` into **Event JSON**.
3. Choose **Save**, then **Test**.
4. The execution result should show status **Succeeded** and return the same summary as `expected_summary.json`: 5 orders and `"400.00"` in revenue (East `150.00`, West `150.00`, Central `100.00`).

To see the error path, test with an event such as `{}`. The result should show an error saying the event must contain a `csv_text` string.

### Clean up

When you are done, delete the function from the Lambda console. Optionally also delete its IAM role (named after the function) and its CloudWatch log group (`/aws/lambda/<function-name>`).
