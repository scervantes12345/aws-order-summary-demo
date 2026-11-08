"""Local web server for the Sales Anomaly Investigator (Python standard library only)."""

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import DEFAULT_QUESTION
from .agent import run_investigation
from .simulated import SimulatedBedrockModel

INDEX_HTML = Path(__file__).resolve().parent / 'static' / 'index.html'
MAX_QUESTION_LENGTH = 300
MAX_BODY_BYTES = 4096


def make_handler(model):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SalesAnomalyInvestigator/1.0'

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self._send(HTTPStatus.OK, INDEX_HTML.read_bytes(), 'text/html; charset=utf-8')
            elif self.path == '/api/config':
                self._json(HTTPStatus.OK, {'model': model.info, 'default_question': DEFAULT_QUESTION})
            else:
                self._json(HTTPStatus.NOT_FOUND, {'error': 'Not found.'})

        def do_POST(self):
            if self.path != '/api/investigate':
                return self._json(HTTPStatus.NOT_FOUND, {'error': 'Not found.'})
            # Block cross-site form posts: browsers cannot send this content type cross-origin without CORS.
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {'error': 'Content-Type must be application/json.'})
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
            question = body.get('question') if isinstance(body, dict) else None
            question = (question if isinstance(question, str) else '').strip() or DEFAULT_QUESTION
            if len(question) > MAX_QUESTION_LENGTH:
                return self._json(HTTPStatus.BAD_REQUEST,
                                  {'error': f'Question must be at most {MAX_QUESTION_LENGTH} characters.'})
            try:
                result = run_investigation(question, model)
            except Exception as error:  # Surface model/API failures (e.g. Bedrock errors) to the page.
                return self._json(HTTPStatus.BAD_GATEWAY, {'error': f'{type(error).__name__}: {error}'})
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


def create_server(model=None, host='127.0.0.1', port=8765):
    return ThreadingHTTPServer((host, port), make_handler(model or SimulatedBedrockModel()))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m sales_investigator', description='Run the Sales Anomaly Investigator locally.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--bedrock', action='store_true',
                        help='Use the real Amazon Bedrock Converse API (needs boto3, AWS credentials, and a model ID).')
    parser.add_argument('--model-id', help='Bedrock model or inference profile ID (default: $BEDROCK_MODEL_ID).')
    parser.add_argument('--region', help='AWS Region for Bedrock (default: your AWS configuration).')
    args = parser.parse_args(argv)
    if args.bedrock:
        from .bedrock import BedrockConfigError, BedrockConverseModel
        try:
            model = BedrockConverseModel.from_env(model_id=args.model_id, region_name=args.region)
        except BedrockConfigError as error:
            parser.error(str(error))
    else:
        model = SimulatedBedrockModel()
    server = create_server(model, args.host, args.port)
    print(f'Sales Anomaly Investigator ({model.info["label"]}): http://{args.host}:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
