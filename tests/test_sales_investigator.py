import importlib.util
import json
import sys
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from sales_investigator import DEFAULT_QUESTION, tools
from sales_investigator.agent import (
    FINDINGS_TOOL, MAX_MODEL_TURNS, converse_tool_config, run_investigation, validate_tool_request,
)
from sales_investigator.agentcore import handle_invocation
from sales_investigator.bedrock import BedrockConverseModel
from sales_investigator.server import create_server
from sales_investigator.simulated import SimulatedBedrockModel

ROOT = Path(__file__).resolve().parent.parent


def tool_use(name, tool_input, tool_use_id='t1'):
    return {'output': {'message': {'role': 'assistant', 'content': [
        {'toolUse': {'toolUseId': tool_use_id, 'name': name, 'input': tool_input}}]}}, 'stopReason': 'tool_use'}


class ScriptedModel:
    """Plays back fixed Converse responses and records each request."""
    info = {'provider': 'test', 'label': 'Scripted', 'model_id': 'scripted'}

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def converse(self, system, messages, tool_config):
        self.requests.append(json.loads(json.dumps(messages)))
        return self.responses.pop(0)


class ToolArithmeticTest(unittest.TestCase):
    def test_west_drops_from_4200_to_3200(self):
        west = next(r for r in tools.compare_region_revenue('2026-11-06', '2026-11-07')['regions']
                    if r['region'] == 'West')
        self.assertEqual(west, {
            'region': 'West', 'baseline_revenue_usd': '4200.00', 'current_revenue_usd': '3200.00',
            'change_usd': '-1000.00', 'change_pct': '-23.8',
        })

    def test_other_regions_are_stable(self):
        regions = {r['region']: r for r in tools.compare_region_revenue('2026-11-06', '2026-11-07')['regions']}
        self.assertEqual((regions['East']['change_usd'], regions['East']['change_pct']), ('50.00', '1.7'))
        self.assertEqual((regions['Central']['change_usd'], regions['Central']['change_pct']), ('-20.00', '-0.8'))

    def test_product_a_explains_800_of_1000(self):
        result = tools.attribute_change_by_product('West', '2026-11-06', '2026-11-07')
        self.assertEqual(result['region_change_usd'], '-1000.00')
        self.assertEqual(
            [(p['product'], p['change_usd'], p['share_of_region_change_pct']) for p in result['products']],
            [('Product A', '-800.00', '80.0'), ('Product B', '-150.00', '15.0'), ('Product C', '-50.00', '5.0')],
        )

    def test_product_changes_sum_to_region_change(self):
        for region in tools.regions():
            result = tools.attribute_change_by_product(region, '2026-11-06', '2026-11-07')
            total = sum(float(p['change_usd']) for p in result['products'])
            self.assertAlmostEqual(total, float(result['region_change_usd']), places=2)

    def test_catalog_only_has_sales_data(self):
        catalog = tools.list_datasets()
        self.assertEqual([d['name'] for d in catalog['datasets']], ['daily_sales'])
        self.assertEqual(catalog['datasets'][0]['dates'], ['2026-11-06', '2026-11-07'])

    def test_same_day_comparison_is_rejected(self):
        with self.assertRaises(tools.ToolInputError):
            tools.compare_region_revenue('2026-11-07', '2026-11-07')


class SimulatedInvestigationTest(unittest.TestCase):
    def setUp(self):
        self.result = run_investigation(DEFAULT_QUESTION, SimulatedBedrockModel())

    def test_trace_runs_real_tools_in_order(self):
        trace = self.result['trace']
        self.assertEqual(self.result['status'], 'complete')
        self.assertEqual([s['tool'] for s in trace],
                         ['list_datasets', 'compare_region_revenue', 'attribute_change_by_product', FINDINGS_TOOL])
        self.assertEqual([s['status'] for s in trace], ['ok', 'ok', 'ok', 'final'])
        self.assertEqual(trace[2]['input'], {'region': 'West', 'baseline_date': '2026-11-06', 'current_date': '2026-11-07'})
        self.assertEqual(trace[1]['result'], tools.compare_region_revenue('2026-11-06', '2026-11-07'))
        self.assertEqual(trace[2]['result'], tools.attribute_change_by_product('West', '2026-11-06', '2026-11-07'))
        self.assertEqual(trace[3]['result'], self.result['findings'])
        self.assertEqual(self.result['model_turns'], 4)

    def test_findings_separate_observed_from_unknown(self):
        findings = self.result['findings']
        observed, unknown = ' '.join(findings['observed']), ' '.join(findings['unknown'])
        for figure in ('$4,200.00', '$3,200.00', '23.8%', 'Product A', '$800.00', '80.0%'):
            self.assertIn(figure, observed)
        self.assertIn('$1,000.00', findings['summary'])
        self.assertIn('Root cause: unknown', unknown)
        self.assertNotIn('inventory', observed.lower())

    def test_missing_inventory_evidence_is_reported(self):
        self.assertIn('no inventory data is available', ' '.join(self.result['findings']['unknown']))

    def test_inventory_note_follows_the_catalog(self):
        catalog = tools.list_datasets()
        catalog['datasets'].append({'name': 'daily_inventory', 'dates': catalog['datasets'][0]['dates']})
        with mock.patch.dict(tools.DATA_TOOLS, {'list_datasets': lambda: catalog}):
            result = run_investigation(DEFAULT_QUESTION, SimulatedBedrockModel())
        self.assertNotIn('no inventory data', ' '.join(result['findings']['unknown']))

    def test_findings_change_when_tool_data_changes(self):
        def fake_compare(baseline_date, current_date):
            result = tools.compare_region_revenue(baseline_date, current_date)
            result['regions'][0].update(current_revenue_usd='3700.00', change_usd='-500.00', change_pct='-11.9')
            return result
        with mock.patch.dict(tools.DATA_TOOLS, {'compare_region_revenue': fake_compare}):
            result = run_investigation(DEFAULT_QUESTION, SimulatedBedrockModel())
        self.assertIn('$3,700.00', result['findings']['summary'])
        self.assertIn('11.9%', result['findings']['summary'])

    def test_other_region_question_is_answered_from_data(self):
        result = run_investigation('Why did East sales drop yesterday?', SimulatedBedrockModel())
        self.assertEqual(result['trace'][2]['input']['region'], 'East')
        self.assertIn('did not drop', result['findings']['summary'])

    def test_first_mentioned_region_is_investigated(self):
        result = run_investigation('Why did East sales drop compared with West?', SimulatedBedrockModel())
        self.assertEqual(result['trace'][2]['input']['region'], 'East')

    def test_simulated_model_label(self):
        self.assertEqual(self.result['model']['label'], 'Simulated Bedrock')


class DispatchLoopTest(unittest.TestCase):
    def test_unknown_tool_is_rejected_and_reported_to_model(self):
        model = ScriptedModel(tool_use('drop_table', {'name': 'sales'}),
                              {'output': {'message': {'role': 'assistant', 'content': [{'text': 'Giving up.'}]}}})
        result = run_investigation('q', model)
        self.assertEqual(result['trace'][0]['status'], 'rejected')
        self.assertIn('Unknown tool', result['trace'][0]['error'])
        tool_result = model.requests[1][-1]['content'][0]['toolResult']
        self.assertEqual((tool_result['toolUseId'], tool_result['status']), ('t1', 'error'))
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['error'], 'Giving up.')

    def test_invalid_inputs_are_rejected(self):
        bad_inputs = [
            ('compare_region_revenue', {'baseline_date': '2026-11-06'}),
            ('compare_region_revenue', {'baseline_date': '2026-11-06', 'current_date': '2025-01-01'}),
            ('attribute_change_by_product', {'region': 'Mars', 'baseline_date': '2026-11-06', 'current_date': '2026-11-07'}),
            ('list_datasets', {'path': '/etc/passwd'}),
            ('list_datasets', 'not an object'),
            (FINDINGS_TOOL, {'summary': 's', 'observed': [], 'unknown': ['u']}),
        ]
        for name, tool_input in bad_inputs:
            with self.subTest(name=name, tool_input=tool_input), self.assertRaises(tools.ToolInputError):
                validate_tool_request(name, tool_input)

    def test_findings_before_measuring_are_rejected(self):
        findings = {'summary': 'West fell $9,999.', 'observed': ['made up'], 'unknown': ['u']}
        model = ScriptedModel(tool_use(FINDINGS_TOOL, findings),
                              {'output': {'message': {'role': 'assistant', 'content': [{'text': 'Stopping.'}]}}})
        result = run_investigation('q', model)
        self.assertEqual(result['trace'][0]['status'], 'rejected')
        self.assertIsNone(result['findings'])
        self.assertEqual(result['status'], 'incomplete')

    def test_loop_is_bounded(self):
        model = ScriptedModel(*[tool_use('list_datasets', {}, f't{i}') for i in range(MAX_MODEL_TURNS + 2)])
        result = run_investigation('q', model)
        self.assertEqual(len(model.requests), MAX_MODEL_TURNS)
        self.assertEqual(result['status'], 'incomplete')
        self.assertIn(f'{MAX_MODEL_TURNS} model turns', result['error'])

    def test_tool_config_matches_dispatch_table(self):
        names = [tool['toolSpec']['name'] for tool in converse_tool_config()['tools']]
        self.assertEqual(names, [*tools.DATA_TOOLS, FINDINGS_TOOL])


class FakeBedrockClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs)))
        return self.responses.pop(0)


class BedrockAdapterTest(unittest.TestCase):
    def test_converse_requests_and_validation(self):
        findings = {'summary': 's', 'observed': ['o'], 'unknown': ['u']}
        client = FakeBedrockClient([
            tool_use('compare_region_revenue', {'baseline_date': '2026-11-06', 'current_date': '2026-11-07'}, 'a'),
            tool_use('attribute_change_by_product', {'region': 'West', 'baseline_date': 'yesterday',
                                                     'current_date': '2026-11-07'}, 'b'),
            tool_use('attribute_change_by_product', {'region': 'West', 'baseline_date': '2026-11-06',
                                                     'current_date': '2026-11-07'}, 'c'),
            tool_use(FINDINGS_TOOL, findings, 'd'),
        ])
        model = BedrockConverseModel('example-model-id', client=client)
        result = run_investigation(DEFAULT_QUESTION, model)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['findings'], findings)
        self.assertEqual([s['status'] for s in result['trace']], ['ok', 'rejected', 'ok', 'final'])
        first = client.calls[0]
        self.assertEqual(first['modelId'], 'example-model-id')
        self.assertEqual(first['messages'], [{'role': 'user', 'content': [{'text': DEFAULT_QUESTION}]}])
        self.assertEqual(first['toolConfig'], converse_tool_config())
        self.assertEqual(first['inferenceConfig']['temperature'], 0)
        self.assertEqual(client.calls[1]['messages'][-1]['content'][0]['toolResult']['status'], 'success')
        self.assertEqual(client.calls[2]['messages'][-1]['content'][0]['toolResult']['status'], 'error')
        self.assertEqual(result['model']['label'], 'Amazon Bedrock')

    def test_model_id_is_required(self):
        with mock.patch.dict('os.environ', {}, clear=True), self.assertRaises(RuntimeError):
            BedrockConverseModel.from_env(client=object())

    @unittest.skipUnless(importlib.util.find_spec('boto3'), 'boto3 is not installed (optional)')
    def test_request_matches_botocore_converse_model(self):
        import boto3
        from botocore.stub import Stubber
        client = boto3.client('bedrock-runtime', region_name='us-east-1',
                              aws_access_key_id='test', aws_secret_access_key='test')
        stubber = Stubber(client)
        dates = {'baseline_date': '2026-11-06', 'current_date': '2026-11-07'}
        for name, tool_input in (('compare_region_revenue', dates),
                                 ('attribute_change_by_product', {'region': 'West', **dates}),
                                 (FINDINGS_TOOL, {'summary': 's', 'observed': ['o'], 'unknown': ['u']})):
            stubber.add_response('converse', {
                'output': {'message': {'role': 'assistant', 'content': [{'toolUse': {
                    'toolUseId': name, 'name': name, 'input': tool_input}}]}},
                'stopReason': 'tool_use', 'usage': {'inputTokens': 1, 'outputTokens': 1, 'totalTokens': 2},
                'metrics': {'latencyMs': 1},
            })
        with stubber:
            result = run_investigation(DEFAULT_QUESTION, BedrockConverseModel('example-model-id', client=client))
        self.assertEqual(result['status'], 'complete')


class AgentCoreEntryPointTest(unittest.TestCase):
    def test_simulated_payload(self):
        result = handle_invocation({'prompt': DEFAULT_QUESTION, 'simulated': True})
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['model']['label'], 'Simulated Bedrock')

    def test_bedrock_payload_uses_model_id(self):
        seen = {}

        def factory(model_id):
            seen['model_id'] = model_id
            return SimulatedBedrockModel()
        handle_invocation({'prompt': DEFAULT_QUESTION, 'model_id': 'm-1'}, bedrock_factory=factory)
        self.assertEqual(seen['model_id'], 'm-1')

    def test_blank_prompt_uses_default_question(self):
        result = handle_invocation({'prompt': '   ', 'simulated': True})
        self.assertEqual(result['question'], DEFAULT_QUESTION)

    def test_rejects_oversized_prompt(self):
        self.assertEqual(handle_invocation({'prompt': 'x' * 301})['status'], 'error')

    def test_main_registers_entrypoint(self):
        runtime = types.ModuleType('bedrock_agentcore.runtime')

        class FakeApp:
            def entrypoint(self, func):
                self.handler = func
                return func
        runtime.BedrockAgentCoreApp = FakeApp
        modules = {'bedrock_agentcore': types.ModuleType('bedrock_agentcore'), 'bedrock_agentcore.runtime': runtime}
        with mock.patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('agentcore_main', ROOT / 'agentcore_runtime' / 'main.py')
            main = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(main)
        self.assertEqual(main.app.handler({'simulated': True})['status'], 'complete')


class WebServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(port=0)
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        headers = {'Content-Type': 'application/json', **(headers or {})}
        with urllib.request.urlopen(urllib.request.Request(self.url + path, data=data, headers=headers)) as response:
            return response.read().decode()

    def test_page_has_persistent_simulated_label(self):
        page = self.request('/')
        self.assertIn('Sales Anomaly Investigator', page)
        self.assertIn('Simulated Bedrock', page)
        self.assertIn('Why did West sales drop yesterday?', page)
        config = json.loads(self.request('/api/config'))
        self.assertEqual(config['model']['label'], 'Simulated Bedrock')

    def test_investigate_endpoint_returns_findings_and_trace(self):
        result = json.loads(self.request('/api/investigate', {'question': DEFAULT_QUESTION}))
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['trace']), 4)
        self.assertIn('$800.00', result['findings']['summary'])

    def test_rejects_long_question(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/investigate', {'question': 'x' * 301})
        self.assertEqual(error.exception.code, 400)

    def test_rejects_cross_site_form_posts(self):
        for headers, code in (({'Content-Type': 'text/plain'}, 415),
                              ({'Origin': 'http://evil.example'}, 403)):
            with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/investigate', {'question': DEFAULT_QUESTION}, headers)
            self.assertEqual(error.exception.code, code)


if __name__ == '__main__':
    unittest.main()
