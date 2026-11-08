import importlib.util
import json
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from support_triage_demo.bedrock import BedrockConfigError, BedrockConverseProvider
from support_triage_demo.contract import ROUTABLE_QUEUES
from support_triage_demo.evaluation import DISCLAIMER, evaluate, load_labels
from support_triage_demo.providers import SYSTEM_PROMPT
from support_triage_demo.server import App, create_server, default_providers
from support_triage_demo.simulated import SimulatedBedrockRuntime, SimulatedLegacyProvider, simulated_bedrock_provider
from support_triage_demo.tickets import load_tickets, ticket_text
from support_triage_demo.workflow import triage

TICKETS = load_tickets()
BY_ID = {ticket['id']: ticket for ticket in TICKETS}
VALID = {'queue': 'shipping', 'priority': 'medium', 'reason': 'Tracking is stuck.', 'review_required': False}


class ReadTimeoutError(Exception):
    """Same class name as botocore.exceptions.ReadTimeoutError."""


class ClientError(Exception):
    def __init__(self, code):
        super().__init__(f'An error occurred ({code})')
        self.response = {'Error': {'Code': code, 'Message': 'denied'}}


class ScriptedClient:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def converse_response(text, stop_reason='end_turn'):
    return {'output': {'message': {'role': 'assistant', 'content': [{'text': text}]}}, 'stopReason': stop_reason}


def live(client):
    return BedrockConverseProvider('example.model-v1', 'us-east-1', timeout_seconds=3, client=client)


class BedrockAdapterTest(unittest.TestCase):
    def test_converse_request_and_valid_output(self):
        client = ScriptedClient(converse_response(json.dumps(VALID)))
        record = triage(BY_ID['T-102'], live(client))
        self.assertEqual((record['status'], record['result']), ('routed', VALID))
        self.assertEqual(client.calls, [{
            'modelId': 'example.model-v1',
            'system': [{'text': SYSTEM_PROMPT}],
            'messages': [{'role': 'user', 'content': [{'text': ticket_text(BY_ID['T-102'])}]}],
            'inferenceConfig': {'maxTokens': 300, 'temperature': 0},
        }])
        self.assertTrue(client.calls[0]['messages'][0]['content'][0]['text'].startswith('Subject: Package tracking'))
        self.assertFalse(record['provider']['simulated'])
        self.assertIn('LIVE', record['provider']['label'])

    def test_failures_route_to_human_review(self):
        cases = {
            'read timeout': (ScriptedClient(error=ReadTimeoutError('Read timeout')), 'Timeout: No Bedrock response'),
            'access denied': (ScriptedClient(error=ClientError('AccessDeniedException')), 'AccessDeniedException'),
            'throttled': (ScriptedClient(error=ClientError('ThrottlingException')), 'ThrottlingException'),
            'prose': (ScriptedClient(converse_response('Billing, high priority.')), 'not valid JSON'),
            'disallowed queue': (ScriptedClient(converse_response(json.dumps({**VALID, 'queue': 'vip'}))),
                                 "'vip' is not an allowed queue"),
            'truncated': (ScriptedClient(converse_response('{"queue": "ship', 'max_tokens')), "'max_tokens'"),
            'no text': (ScriptedClient({'output': {'message': {'content': [{'image': {}}]}}, 'stopReason': 'end_turn'}),
                        'no text content'),
            'not a dict': (ScriptedClient(None), 'not an object'),
        }
        for name, (client, message) in cases.items():
            with self.subTest(name):
                record = triage(BY_ID['T-102'], live(client))
                self.assertEqual(record['result']['queue'], 'human_review')
                self.assertTrue(record['result']['review_required'])
                self.assertEqual(record['status'], 'needs_review')
                self.assertIn(message, record['error'])
                self.assertEqual(record['provider']['id'], 'bedrock-live')

    def test_timeout_message_uses_configured_timeout(self):
        record = triage(BY_ID['T-101'], live(ScriptedClient(error=ReadTimeoutError())))
        self.assertIn('3s timeout', record['error'])

    def test_configuration_is_required_and_timeout_is_finite(self):
        client = ScriptedClient()
        for kwargs in ({'model_id': '', 'region': 'us-east-1'}, {'model_id': 'm', 'region': ''},
                       {'model_id': 'm', 'region': 'r', 'timeout_seconds': 0},
                       {'model_id': 'm', 'region': 'r', 'timeout_seconds': float('inf')},
                       {'model_id': 'm', 'region': 'r', 'timeout_seconds': float('nan')},
                       {'model_id': 'm', 'region': 'r', 'timeout_seconds': 61}):
            with self.subTest(kwargs), self.assertRaises(BedrockConfigError):
                BedrockConverseProvider(client=client, **kwargs)

    def test_from_env(self):
        env = {'SUPPORT_TRIAGE_BEDROCK_MODEL_ID': 'env.model', 'SUPPORT_TRIAGE_BEDROCK_REGION': 'eu-west-1'}
        with mock.patch.dict('os.environ', env), mock.patch.object(BedrockConverseProvider, '_live_client'):
            provider = BedrockConverseProvider.from_env()
        self.assertEqual((provider.model_id, provider.region), ('env.model', 'eu-west-1'))

    def test_missing_boto3_is_a_clear_error(self):
        with mock.patch.dict(sys.modules, {'boto3': None}), self.assertRaisesRegex(BedrockConfigError, 'boto3'):
            BedrockConverseProvider('m', 'us-east-1')

    def test_boto3_is_imported_lazily(self):
        code = 'import sys, support_triage_demo.server; print("boto3" in sys.modules)'
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'False')

    @unittest.skipUnless(importlib.util.find_spec('boto3'), 'boto3 is not installed (optional)')
    def test_live_client_config_and_request_match_botocore_model(self):
        from botocore.stub import Stubber
        provider = BedrockConverseProvider('example.model-v1', 'us-west-2', timeout_seconds=4)
        config = provider.client.meta.config
        self.assertEqual((config.read_timeout, config.connect_timeout, config.region_name), (4, 4, 'us-west-2'))
        self.assertEqual(config.retries['total_max_attempts'], 1)
        with Stubber(provider.client) as stubber:
            stubber.add_response('converse', {**converse_response(json.dumps(VALID)),
                                              'usage': {'inputTokens': 1, 'outputTokens': 1, 'totalTokens': 2},
                                              'metrics': {'latencyMs': 1}})
            self.assertEqual(triage(BY_ID['T-102'], provider)['result'], VALID)


class SimulatedBedrockTest(unittest.TestCase):
    def test_uses_the_real_adapter_with_a_fake_client(self):
        provider = simulated_bedrock_provider()
        self.assertIsInstance(provider, BedrockConverseProvider)
        self.assertIsInstance(provider.client, SimulatedBedrockRuntime)
        self.assertTrue(provider.info['simulated'])
        self.assertIn('SIMULATED', provider.info['label'])

    def test_faults(self):
        expected = {'timeout': 'Timeout:', 'malformed': 'not valid JSON', 'disallowed_queue': "'refunds'"}
        for fault, message in expected.items():
            with self.subTest(fault):
                record = triage(BY_ID['T-101'], simulated_bedrock_provider(fault))
                self.assertEqual((record['result']['queue'], record['status']), ('human_review', 'needs_review'))
                self.assertIn(message, record['error'])
        with self.assertRaises(ValueError):
            simulated_bedrock_provider('explode')

    def test_contract_parity_with_legacy_baseline(self):
        for ticket in TICKETS:
            with self.subTest(ticket['id']):
                self.assertEqual(triage(ticket, simulated_bedrock_provider())['result'],
                                 triage(ticket, SimulatedLegacyProvider())['result'])


class EvaluationTest(unittest.TestCase):
    def test_labels_cover_queues_ambiguity_and_review(self):
        labels = load_labels()['labels']
        self.assertGreaterEqual(len(labels), 6)
        self.assertEqual(set(labels), set(BY_ID))
        self.assertEqual({queue for label in labels.values() for queue in label['expected_queues']},
                         set(ROUTABLE_QUEUES))
        self.assertTrue(any(len(label['expected_queues']) > 1 for label in labels.values()))
        self.assertGreaterEqual(sum(label['expected_review_required'] for label in labels.values()), 2)

    def test_simulated_providers_pass_integration_checks(self):
        report = evaluate([SimulatedLegacyProvider(), simulated_bedrock_provider()], TICKETS)
        self.assertEqual(report['summary'], {'legacy-simulated': {'passed': 8, 'total': 8},
                                             'bedrock-simulated': {'passed': 8, 'total': 8}})
        self.assertEqual(report['disclaimer'], DISCLAIMER)
        self.assertIn('not evidence of model quality, latency, or cost', DISCLAIMER)

    def test_failures_are_reported_per_ticket(self):
        report = evaluate([simulated_bedrock_provider('timeout')], TICKETS)
        rows = {row['ticket_id']: row['actual']['bedrock-simulated'] for row in report['rows']}
        self.assertFalse(rows['T-101']['passed'])
        self.assertEqual(rows['T-101']['checks'], {'queue': False, 'priority': True, 'review_required': False})
        self.assertIn('Timeout', rows['T-101']['error'])
        self.assertEqual(report['summary']['bedrock-simulated']['passed'], 0)


class NoFallbackTest(unittest.TestCase):
    def test_live_failure_does_not_fall_back(self):
        client = ScriptedClient(error=ReadTimeoutError())
        app = App(default_providers(live(client)))
        with mock.patch.object(SimulatedLegacyProvider, 'complete') as legacy, \
                mock.patch.object(SimulatedBedrockRuntime, 'converse') as simulated:
            state = app.handle_post('/api/triage', {'ticket_id': 'T-101', 'provider': 'bedrock-live'})
        legacy.assert_not_called()
        simulated.assert_not_called()
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(state['triage']['provider']['id'], 'bedrock-live')
        self.assertEqual(state['triage']['result']['queue'], 'human_review')

    def test_live_provider_is_opt_in(self):
        self.assertNotIn('bedrock-live', App().providers)
        self.assertTrue(all(info['simulated'] for info in App().provider_infos()))

    def test_cli_bedrock_flag_requires_configuration(self):
        result = subprocess.run([sys.executable, '-m', 'support_triage_demo', '--bedrock', '--port', '0'],
                                capture_output=True, text=True, env={'PATH': ''}, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn('model or inference profile ID', result.stderr)


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = create_server(App(), port=0)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_timeout_demo_and_review(self):
        status, state = self.post('/api/triage', {'ticket_id': 'T-104', 'provider': 'bedrock-simulated',
                                                  'fault': 'timeout'})
        self.assertEqual((status, state['triage']['result']['queue']), (200, 'human_review'))
        status, state = self.post('/api/review', {'ticket_id': 'T-104', 'queue': 'technical'})
        self.assertEqual((status, state['routed_to'], state['routed_by']), (200, 'technical', 'human reviewer'))

    def test_fault_only_for_simulated_bedrock(self):
        self.assertEqual(self.post('/api/triage', {'ticket_id': 'T-101', 'provider': 'legacy-simulated',
                                                   'fault': 'timeout'})[0], 400)
        self.assertEqual(self.post('/api/triage', {'ticket_id': 'T-101', 'provider': 'bedrock-simulated',
                                                   'fault': 'explode'})[0], 400)

    def test_evaluate(self):
        status, report = self.post('/api/evaluate', {})
        self.assertEqual(status, 200)
        self.assertEqual([p['id'] for p in report['providers']], ['legacy-simulated', 'bedrock-simulated'])
        self.assertEqual(len(report['rows']), 8)
        self.assertEqual(self.post('/api/evaluate', {'providers': ['bedrock-live']})[0], 400)


if __name__ == '__main__':
    unittest.main()
