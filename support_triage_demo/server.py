"""Local web server for the support triage demo (Python standard library only)."""

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .bedrock import DEFAULT_TIMEOUT_SECONDS, BedrockConfigError, BedrockConverseProvider
from .contract import PRIORITIES, ROUTABLE_QUEUES
from .evaluation import evaluate
from .simulated import FAULTS, SimulatedLegacyProvider, simulated_bedrock_provider
from .tickets import load_tickets
from .workflow import ReviewDesk, triage

INDEX_HTML = Path(__file__).resolve().parent / 'static' / 'index.html'
MAX_BODY_BYTES = 4096
DEFAULT_PORT = 8766


def fixed_provider(provider):
    def factory(body):
        if body and body.get('fault', 'none') != 'none':
            raise ValueError('Fault injection is only available for Amazon Bedrock (SIMULATED).')
        return provider
    return factory


def default_providers(live_bedrock=None):
    """Provider factories by ID. A request uses exactly the provider it names; there is no fallback."""
    providers = {'legacy-simulated': fixed_provider(SimulatedLegacyProvider()),
                 'bedrock-simulated': lambda body: simulated_bedrock_provider((body or {}).get('fault', 'none'))}
    if live_bedrock is not None:
        providers[live_bedrock.info['id']] = fixed_provider(live_bedrock)
    return providers


class App:
    def __init__(self, providers=None, desk=None):
        self.providers = providers or default_providers()
        self.desk = desk or ReviewDesk(load_tickets())

    def provider_infos(self):
        return [factory(None).info for factory in self.providers.values()]

    def config(self):
        return {'providers': self.provider_infos(), 'queues': list(ROUTABLE_QUEUES), 'priorities': list(PRIORITIES),
                'faults': list(FAULTS)}

    def handle_post(self, path, body):
        if path == '/api/triage':
            ticket = self.desk.get_ticket(body.get('ticket_id'))
            if ticket is None:
                raise ValueError('Unknown ticket.')
            factory = self.providers.get(body.get('provider'))
            if factory is None:
                raise ValueError('Unknown or unavailable provider.')
            return self.desk.record_triage(triage(ticket, factory(body)))
        if path == '/api/evaluate':
            ids = body.get('providers') or [info['id'] for info in self.provider_infos() if info['simulated']]
            if not isinstance(ids, list) or any(provider_id not in self.providers for provider_id in ids):
                raise ValueError('Unknown or unavailable provider.')
            return evaluate([self.providers[provider_id](None) for provider_id in ids],
                            [ticket for ticket in self.desk.tickets.values()])
        if path == '/api/review':
            return self.desk.review(body.get('ticket_id'), body.get('queue'))
        if path == '/api/reset':
            self.desk.reset()
            return {'ok': True}
        return None


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SupportTriageDemo/1.0'

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self._send(HTTPStatus.OK, INDEX_HTML.read_bytes(), 'text/html; charset=utf-8')
            elif self.path == '/api/config':
                self._json(HTTPStatus.OK, app.config())
            elif self.path == '/api/tickets':
                self._json(HTTPStatus.OK, app.desk.snapshot())
            else:
                self._json(HTTPStatus.NOT_FOUND, {'error': 'Not found.'})

        def do_POST(self):
            # Block cross-site form posts: browsers cannot send this content type cross-origin without CORS.
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                                  {'error': 'Content-Type must be application/json.'})
            origin = self.headers.get('Origin')
            if origin and origin != f'http://{self.headers.get("Host")}':
                return self._json(HTTPStatus.FORBIDDEN, {'error': 'Cross-origin requests are not allowed.'})
            length = int(self.headers.get('Content-Length') or 0)
            if length > MAX_BODY_BYTES:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {'error': 'Request body is too large.'})
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
            except json.JSONDecodeError:
                return self._json(HTTPStatus.BAD_REQUEST, {'error': 'Request body must be JSON.'})
            if not isinstance(body, dict):
                return self._json(HTTPStatus.BAD_REQUEST, {'error': 'Request body must be a JSON object.'})
            try:
                result = app.handle_post(self.path, body)
            except ValueError as error:
                return self._json(HTTPStatus.BAD_REQUEST, {'error': str(error)})
            if result is None:
                return self._json(HTTPStatus.NOT_FOUND, {'error': 'Not found.'})
            self._json(HTTPStatus.OK, result)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload).encode('utf-8'), 'application/json')

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    return Handler


def create_server(app=None, host='127.0.0.1', port=DEFAULT_PORT):
    return ThreadingHTTPServer((host, port), make_handler(app or App()))


def build_parser():
    parser = argparse.ArgumentParser(prog='python3 -m support_triage_demo',
                                     description='Run the support ticket triage demo locally.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--bedrock', action='store_true',
                        help='Also offer live Amazon Bedrock (billable; needs boto3 and AWS credentials).')
    parser.add_argument('--model-id',
                        help='Bedrock model or inference profile ID (default: $SUPPORT_TRIAGE_BEDROCK_MODEL_ID).')
    parser.add_argument('--region', help='AWS Region for Bedrock (default: $SUPPORT_TRIAGE_BEDROCK_REGION).')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT_SECONDS,
                        help='Bedrock connect and read timeout in seconds (default: %(default)s).')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    live = None
    if args.bedrock:
        try:
            live = BedrockConverseProvider.from_env(args.model_id, args.region, args.timeout)
        except BedrockConfigError as error:
            parser.error(str(error))
    server = create_server(App(default_providers(live)), args.host, args.port)
    mode = f'live Bedrock enabled: {live.model_id} in {live.region}' if live else 'offline, SIMULATED providers'
    print(f'Support triage demo ({mode}): http://{args.host}:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
