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

A ready-to-use test event built from `orders.csv` is in `events/sample_event.json`. If `csv_text` is missing, or the CSV is malformed (missing `region` or `amount_usd` columns, rows with too few or too many values, or amounts that are not finite numbers), the function raises a `ValueError` with a plain-language message, which Lambda reports as a function error.

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
## Support ticket triage demo (`support_triage_demo`)

A standalone, one-screen local app for a fictional retailer, Juniper & Pine Outfitters, that routes synthetic support tickets with an AI model. Every ticket in `support_triage_demo/data/tickets.json` is made up; there is no real customer or ticket data. It needs only Python 3 (no packages, no network, no AWS account):

```sh
python3 -m support_triage_demo
```

Then open http://127.0.0.1:8766/ and stop the server with `Ctrl+C`.

### Workflow and output contract

1. Pick a ticket in the inbox and choose **Triage ticket**.
2. The provider must return exactly the shared contract (`support_triage_demo/contract.py`): `queue` (billing, shipping, returns, technical, account, general), `priority` (low, medium, high, urgent), `reason` (at most 300 characters), and `review_required` (boolean). Anything else is rejected.
3. Tickets with `review_required: false` are routed automatically. Others wait for a person, who confirms or changes the queue and chooses **Confirm route**. Urgent tickets always need review.
4. If the provider fails or returns output that does not validate, the ticket goes to the `human_review` queue with `review_required: true` and the error is shown. It is never routed automatically.

### Providers

| Provider | What it is |
| --- | --- |
| **Legacy provider (SIMULATED)** | The pilot baseline: a deterministic keyword script in the existing provider's response envelope (`support_triage_demo/simulated.py`). |
| **Amazon Bedrock (SIMULATED)** | The real Converse adapter (`support_triage_demo/bedrock.py`) with a deterministic fake `bedrock-runtime` client. No AWS call. It can inject a timeout, malformed output, or a disallowed queue. |
| **Amazon Bedrock (LIVE, opt-in)** | Only offered when started with `--bedrock`. Makes billable Converse calls. |

Both offline providers share one keyword script, so they differ only in wire format. The header and every provider label say SIMULATED. Each request uses exactly the provider it names: if Bedrock fails, the ticket goes to human review and no other provider is tried.

### Two-minute demo script

1. **Baseline:** select T-101, keep **Legacy provider (SIMULATED)**, choose **Triage ticket**. It is routed to `billing` automatically.
2. **Migration:** switch to **Amazon Bedrock (SIMULATED)** and triage T-106. You get the same contract; it is ambiguous, so pick a queue and choose **Confirm route** (human review).
3. **Fail-safe:** set **Inject fault** to **Bedrock timeout** and triage T-104. A red FAIL-SAFE banner shows the timeout, the queue is `human_review`, and a reviewer must route it.
4. **Evaluation:** choose **Run evaluation**. All 8 synthetic tickets run through both SIMULATED providers. The table shows expected (from `support_triage_demo/data/eval_labels.json`, written from the ticket text before running any provider) versus actual, with PASS/FAIL per ticket. The labels cover all six queues, one ambiguous ticket, and three that should need human review.

These offline results are **integration checks** of the contract, validation, and workflow. They are not evidence of model quality, latency, or cost.

### Optional: live Amazon Bedrock

This is opt-in and makes billable calls with your own AWS credentials (from the standard AWS credential chain, such as `aws configure` or an SSO profile). No credentials are stored in this repository.

```sh
python3 -m pip install boto3
python3 -m support_triage_demo --bedrock --model-id <model-or-inference-profile-id> --region us-east-1 --timeout 5
```

You can also set `SUPPORT_TRIAGE_BEDROCK_MODEL_ID` and `SUPPORT_TRIAGE_BEDROCK_REGION`. Startup fails with a clear message if the model ID, Region, or boto3 is missing. The adapter:

- imports boto3 only when the live provider is created;
- calls [`Converse`](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) with `temperature: 0` and `maxTokens: 300`;
- sets connect and read timeouts (default 5 seconds, at most 60) and one attempt (no retries), so a slow call becomes a fail-safe within a known bound;
- treats a timeout, an AWS error (for example `AccessDeniedException` or `ThrottlingException`), a stop reason other than `end_turn`, or output that fails the contract (including queues outside the allowed list) as a failure that goes to human review.

The caller needs only `bedrock:InvokeModel` on the chosen model, plus access to that model enabled in the account. For example:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock:InvokeModel",
    "Resource": "arn:aws:bedrock:us-east-1::foundation-model/<model-id>"
  }]
}
```

If you use a cross-Region inference profile, also allow the inference profile ARN and the foundation model in each Region it routes to.

### AWS pilot plan (measured, not simulated)

The offline demo proves the wiring only. A short pilot in the retailer's AWS account would measure:

1. **Routing quality:** a few hundred historical tickets with PII removed, labeled by support leads the same way as `eval_labels.json` (acceptable queues, priority, review needed). Report queue accuracy, review-flag recall (how many tickets that needed a person were flagged), and the fail-safe rate. Compare against the legacy provider on the same set. Agree pass thresholds before the run.
2. **p95 latency:** time each Converse call from the client (and record `metrics.latencyMs` from the response) at a realistic request rate. Report p50 and p95 and how many calls hit the timeout. Set `--timeout` from the measured p95 plus headroom.
3. **Cost per ticket:** sum `usage.inputTokens` and `usage.outputTokens` from each response, multiply by the published on-demand Bedrock price for the chosen model and Region, and divide by tickets processed. Check against AWS Cost Explorer for the pilot period.
4. **Governance:** a dedicated IAM role limited to `bedrock:InvokeModel` on the approved model, CloudTrail for API activity, and Bedrock model invocation logging if the retailer wants to keep prompts and outputs. Keep humans reviewing every `review_required` ticket during the pilot.

### Tests

```sh
python3 -m unittest discover -s tests -t . -v
```

`tests/test_support_triage.py` covers the contract, the legacy baseline, the review workflow, and the HTTP API. `tests/test_support_triage_bedrock.py` covers the Converse request shape, timeouts, AWS errors, malformed and disallowed output, finite timeout configuration, lazy boto3 import, no fallback, and the evaluation. When boto3 is installed, one more test checks the request against botocore's Converse model and the client timeout and retry settings. `tests/test_support_triage_browser.py` drives the page in Chromium when Playwright is installed (set `CHROME_PATH` to use an existing Chrome) and is skipped otherwise.
