# Daily order summary

This interview demo starts with a local Python job that summarizes a CSV export. All orders are synthetic. Run it with Python 3 from this directory.

```sh
python3 order_summary.py orders.csv
```

The five sample orders sum to $400.00. East is $150.00, West is $150.00, and Central is $100.00. See `expected_summary.json` for the baseline fixture. Revenue uses decimal arithmetic and is represented as a string with two decimal places.

## Sales Anomaly Investigator (browser demo)

A single-screen local app that answers **"Why did West sales drop yesterday?"** with a bounded tool-use agent over synthetic two-day sales data. It needs only Python 3 (no AWS account, credentials, network calls, or extra packages). Start it from this directory:

```sh
python3 -m sales_investigator
```

Then open http://127.0.0.1:8765/ and choose **Investigate**. Stop the server with `Ctrl+C`.

The header always shows a **Simulated Bedrock** badge in this offline mode: a deterministic stand-in model picks the tool calls. Nothing is sent to AWS.

### What it shows

- **Data:** `sales_investigator/data/sales_two_day.csv` has revenue by date, region, and product for 2026-11-06 and 2026-11-07. The fixture's "today" is 2026-11-08, so "yesterday" is 2026-11-07. West falls from $4,200.00 to $3,200.00 (a $1,000.00 or 23.8% decline). Product A falls $800.00, which is 80.0% of that decline. There is no inventory data.
- **Tools** (`sales_investigator/tools.py`): read-only Python functions that use decimal arithmetic. `list_datasets` lists what data exists. `compare_region_revenue` compares every region between two days. `attribute_change_by_product` splits one region's change by product.
- **Loop** (`sales_investigator/agent.py`): `run_investigation` sends Converse-format messages to a model for at most 6 turns. It checks every requested tool name and input against the tool's JSON schema, runs the tool, and returns the result to the model. The model finishes by calling `submit_findings`, which has separate `observed` and `unknown` lists.
- **Simulated Bedrock** (`sales_investigator/simulated.py`): requests the same tools in order, then writes findings using only the tool results in the conversation. The figures and the trace come from the real tool runs; neither the model nor the page hardcodes them.
- **Answer:** the "Observed contribution" list holds measured figures. The "Unknown root cause" list says that sales data alone cannot explain *why* Product A fell, and that there is no inventory data, so a stockout cannot be confirmed or ruled out.
- **Trace:** each step can be expanded to show the tool name, the input the model sent, the actual result, and the final findings.

### Optional: use real Amazon Bedrock locally

This mode is opt-in and makes billable Bedrock Converse API calls with your configured AWS credentials. It needs `boto3` and access to a model that supports tool use in the Converse API.

```sh
python3 -m pip install boto3
BEDROCK_MODEL_ID=<model-or-inference-profile-id> python3 -m sales_investigator --bedrock --region us-east-1
```

The badge then shows **Amazon Bedrock** with the model ID. `sales_investigator/bedrock.py` calls [`Converse`](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html) with a `toolConfig`, following [client-side tool use](https://docs.aws.amazon.com/bedrock/latest/userguide/tool-use-client-side.html). The caller needs `bedrock:InvokeModel` permission on the model. The same validation applies: an unknown tool or invalid input is not executed. It is sent back to the model as an error `toolResult` and marked **rejected** in the trace.

### Optional: AgentCore Runtime entry point

`agentcore_runtime/main.py` wraps the same loop with `BedrockAgentCoreApp` and `@app.entrypoint` from the [Amazon Bedrock AgentCore Python SDK](https://github.com/aws/bedrock-agentcore-sdk-python). This follows the [HTTP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html) (`POST /invocations`, `GET /ping`, port 8080). Payload: `{"prompt": "...", "model_id": "<optional, overrides BEDROCK_MODEL_ID>", "simulated": false}`. Passing `"simulated": true` runs the offline model, which is useful as a smoke test.

To try the entry point locally (no AWS calls in simulated mode):

```sh
python3 -m venv .venv && .venv/bin/pip install bedrock-agentcore boto3
PYTHONPATH=. .venv/bin/python agentcore_runtime/main.py
curl -X POST http://localhost:8080/invocations -H "Content-Type: application/json" \
  -d '{"prompt": "Why did West sales drop yesterday?", "simulated": true}'
```

To deploy, follow [Direct code deployment for Python](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html) using the AgentCore CLI path. This creates AWS resources and incurs charges; the demo does not need it. Prerequisites from [Get started with the AgentCore CLI](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-get-started-cli.html): Node.js 20.12+, `uv`, Python 3.10+, configured AWS credentials, and the [AgentCore permissions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html).

```sh
npm install -g @aws/agentcore
agentcore create --name SalesInvestigator --template agent-python-minimal
cp agentcore_runtime/main.py agentcore_runtime/pyproject.toml SalesInvestigator/app/agent/
cp -r sales_investigator SalesInvestigator/app/agent/
cd SalesInvestigator
agentcore deploy
agentcore invoke --runtime agent \
  --payload '{"prompt": "Why did West sales drop yesterday?", "model_id": "<model-or-inference-profile-id>"}'
```

The Runtime execution role needs `bedrock:InvokeModel` on the chosen model (see the execution role section of the permissions page). To clean up, run `agentcore remove all --yes` and then `agentcore deploy --yes`.

### Tests

The tests check the arithmetic, the trace, the missing-inventory note, tool name and input validation, the turn limit, the Bedrock request shape (with a fake client), the AgentCore entry point, and the local HTTP endpoints. They run with the existing suite:

```sh
python3 -m unittest discover -s tests -t . -v
```

`tests/test_browser_flow.py` drives the page in Chromium. It runs only when Playwright is installed (`pip install playwright && python3 -m playwright install chromium`, or set `CHROME_PATH` to a Chrome binary), and is skipped otherwise. When boto3 is installed, one more test checks the Converse request against botocore's API model.

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
