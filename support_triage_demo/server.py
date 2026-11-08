"""Local web server for the support triage demo (Python standard library only)."""

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .contract import PRIORITIES, ROUTABLE_QUEUES
from .simulated import SimulatedLegacyProvider
from .tickets import load_tickets
from .workflow import ReviewDesk, triage

INDEX_HTML = Path(__file__).resolve().parent / 'static' / 'index.html'
MAX_BODY_BYTES = 4096
DEFAULT_PORT = 8766


def default_providers():
    legacy = SimulatedLegacyProvider()
    return {legacy.info['id']: lambda body: legacy}


class App:
    def __init__(self, providers=None, desk=None):
        self.providers = providers or default_providers()
        self.desk = desk or ReviewDesk(load_tickets())

    def provider_infos(self):
        return [factory(None).info for factory in self.providers.values()]

    def config(self):
        return {'providers': self.provider_infos(), 'queues': list(ROUTABLE_QUEUES), 'priorities': list(PRIORITIES)}

    def handle_post(self, path, body):
        if path == '/api/triage':
            ticket = self.desk.get_ticket(body.get('ticket_id'))
            if ticket is None:
                raise ValueError('Unknown ticket.')
            factory = self.providers.get(body.get('provider'))
            if factory is None:
                raise ValueError('Unknown or unavailable provider.')
            return self.desk.record_triage(triage(ticket, factory(body)))
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
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    server = create_server(App(), args.host, args.port)
    print(f'Support triage demo (offline, SIMULATED providers): http://{args.host}:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
