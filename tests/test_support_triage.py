import json
import threading
import unittest
import urllib.error
import urllib.request

from support_triage_demo.contract import ContractError, parse_output
from support_triage_demo.providers import ProviderError, ProviderTimeout
from support_triage_demo.server import App, create_server
from support_triage_demo.simulated import SimulatedLegacyProvider
from support_triage_demo.tickets import load_tickets
from support_triage_demo.workflow import ReviewDesk, triage

TICKETS = {ticket['id']: ticket for ticket in load_tickets()}
VALID = {'queue': 'billing', 'priority': 'high', 'reason': 'Duplicate charge.', 'review_required': False}


class FakeProvider:
    info = {'id': 'fake', 'label': 'Fake', 'simulated': True}

    def __init__(self, output=None, error=None):
        self.output, self.error = output, error

    def complete(self, system_prompt, ticket_text):
        if self.error:
            raise self.error
        return self.output


class ContractTest(unittest.TestCase):
    def test_valid_output(self):
        self.assertEqual(parse_output(json.dumps(VALID)), VALID)

    def test_code_fenced_json_is_accepted(self):
        self.assertEqual(parse_output('```json\n' + json.dumps(VALID) + '\n```'), VALID)

    def test_malformed_outputs_are_rejected(self):
        cases = {
            'not json': 'Sure! Billing, high priority.',
            'not an object': json.dumps([VALID]),
            'missing field': json.dumps({k: v for k, v in VALID.items() if k != 'priority'}),
            'extra field': json.dumps({**VALID, 'confidence': 0.9}),
            'disallowed queue': json.dumps({**VALID, 'queue': 'refunds'}),
            'model cannot pick human_review': json.dumps({**VALID, 'queue': 'human_review'}),
            'bad priority': json.dumps({**VALID, 'priority': 'P1'}),
            'empty reason': json.dumps({**VALID, 'reason': ' '}),
            'long reason': json.dumps({**VALID, 'reason': 'x' * 301}),
            'string boolean': json.dumps({**VALID, 'review_required': 'false'}),
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(ContractError):
                parse_output(text)


class WorkflowTest(unittest.TestCase):
    def test_legacy_baseline_routes_clear_ticket(self):
        record = triage(TICKETS['T-101'], SimulatedLegacyProvider())
        self.assertEqual(record['status'], 'routed')
        self.assertEqual(record['result']['queue'], 'billing')
        self.assertIsNone(record['error'])
        self.assertTrue(record['provider']['simulated'])

    def test_legacy_baseline_flags_ambiguous_and_escalated_tickets(self):
        for ticket_id in ('T-106', 'T-107', 'T-108'):
            with self.subTest(ticket_id):
                self.assertEqual(triage(TICKETS[ticket_id], SimulatedLegacyProvider())['status'], 'needs_review')

    def test_failures_fail_safe_to_human_review(self):
        cases = {
            'timeout': FakeProvider(error=ProviderTimeout('no answer in 5s')),
            'provider error': FakeProvider(error=ProviderError('AccessDenied')),
            'malformed': FakeProvider(output='{"queue": "billing"'),
            'disallowed queue': FakeProvider(output=json.dumps({**VALID, 'queue': 'refunds'})),
            'unexpected bug': FakeProvider(error=KeyError('output')),
        }
        for name, provider in cases.items():
            with self.subTest(name):
                record = triage(TICKETS['T-101'], provider)
                self.assertEqual(record['result']['queue'], 'human_review')
                self.assertTrue(record['result']['review_required'])
                self.assertEqual(record['status'], 'needs_review')
                self.assertTrue(record['fail_safe'])
                self.assertTrue(record['error'])

    def test_urgent_always_requires_review(self):
        output = json.dumps({**VALID, 'priority': 'urgent'})
        self.assertTrue(triage(TICKETS['T-101'], FakeProvider(output=output))['result']['review_required'])

    def test_review_desk_flow(self):
        desk = ReviewDesk(load_tickets())
        state = desk.record_triage(triage(TICKETS['T-106'], SimulatedLegacyProvider()))
        self.assertEqual(state['status'], 'needs_review')
        self.assertIsNone(state['routed_to'])
        with self.assertRaises(ValueError):
            desk.review('T-106', 'human_review')
        state = desk.review('T-106', 'returns')
        self.assertEqual((state['status'], state['routed_to'], state['routed_by']),
                         ('routed', 'returns', 'human reviewer'))
        with self.assertRaises(ValueError):
            desk.review('T-101', 'billing')
        with self.assertRaisesRegex(ValueError, 'not waiting'):
            desk.review('T-106', 'billing')
        self.assertEqual(desk.snapshot()[5]['state']['routed_to'], 'returns')
        desk.record_triage(triage(TICKETS['T-101'], SimulatedLegacyProvider()))
        with self.assertRaisesRegex(ValueError, 'not waiting'):
            desk.review('T-101', 'shipping')


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

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, headers={
            'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_index_and_config(self):
        with urllib.request.urlopen(self.base + '/') as response:
            self.assertIn(b'SIMULATED', response.read())
        status, config = self.request('/api/config')
        self.assertEqual(status, 200)
        self.assertIn('legacy-simulated', [p['id'] for p in config['providers']])

    def test_triage_then_review(self):
        status, state = self.request('/api/triage', {'ticket_id': 'T-108', 'provider': 'legacy-simulated'})
        self.assertEqual((status, state['status']), (200, 'needs_review'))
        status, state = self.request('/api/review', {'ticket_id': 'T-108', 'queue': 'general'})
        self.assertEqual((status, state['routed_to']), (200, 'general'))
        status, tickets = self.request('/api/tickets')
        self.assertEqual(next(t for t in tickets if t['id'] == 'T-108')['state']['routed_by'], 'human reviewer')

    def test_bad_requests(self):
        self.assertEqual(self.request('/api/triage', {'ticket_id': 'nope', 'provider': 'legacy-simulated'})[0], 400)
        self.assertEqual(self.request('/api/triage', {'ticket_id': 'T-101', 'provider': 'nope'})[0], 400)
        self.assertEqual(self.request('/api/triage', {}, {'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/triage', {}, {'Content-Type': 'text/plain'})[0], 415)


if __name__ == '__main__':
    unittest.main()
