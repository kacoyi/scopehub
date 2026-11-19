import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Queue
import subprocess
import sys
from threading import Thread
import unittest


ROOT = Path(__file__).resolve().parents[2]
QUESTION = {'role': 'user', 'content': 'Explain the synthetic price move.'}
MODEL = 'owned-synthetic:latest'
REPLY = {
    'model': 'owned-synthetic:observed',
    'done': True,
    'message': {'role': 'assistant', 'content': 'Synthetic observation: 模型 replied.'},
}


class TextModelWorkerTests(unittest.TestCase):
    def setUp(self):
        self.received = Queue()
        self.reply = (200, {}, json.dumps(REPLY).encode())
        case = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                case.received.put((self.command, self.path, dict(self.headers), body))
                self.respond()

            def do_GET(self):
                case.received.put((self.command, self.path, dict(self.headers), b''))
                self.respond()

            def log_message(self, *_args):
                pass

            def respond(self):
                status, headers, body = case.reply
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                for name, value in {'Content-Length': str(len(body)), **headers}.items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # An oversized reply can be closed before the peer finishes writing.

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.origin = 'http://127.0.0.1:' + str(server.server_port)
        self.url = self.origin + '/api/chat'

    def request(self, messages=None, **overrides):
        frame = {'destination': self.url, 'request': {
            'model': MODEL, 'messages': [QUESTION] if messages is None else messages, 'stream': False,
        }, **overrides}
        return self.run_worker(json.dumps(frame, ensure_ascii=False).encode() + b'\n')

    def run_worker(self, frame):
        env = {key: value for key, value in os.environ.items() if key.upper() in {'SYSTEMROOT', 'WINDIR'}}
        env.update({
            'PYTHONPATH': os.pathsep.join([str(ROOT), str(ROOT / 'python')]),
            'HTTP_PROXY': self.origin,
            'NO_PROXY': '',
            'OLLAMA_API_KEY': 'synthetic-environment-value',
        })
        return subprocess.run(
            [sys.executable, '-P', '-s', '-B', '-X', 'utf8', '-m', 'worker_runtime.text_model'],
            cwd=ROOT, env=env, input=frame, capture_output=True, timeout=5,
        )

    def test_two_requests_reach_only_the_selected_peer_and_report_the_observed_model(self):
        history = [QUESTION, REPLY['message'], {'role': 'user', 'content': 'Follow up on the synthetic move.'}]
        for messages in ([QUESTION], history):
            with self.subTest(messages=len(messages)):
                response = self.request(messages)
                self.assertEqual((response.returncode, response.stderr), (0, b''))
                self.assertEqual(json.loads(response.stdout), {
                    'model': REPLY['model'], 'text': REPLY['message']['content'],
                })
                method, path, headers, raw = self.received.get_nowait()
                self.assertEqual((method, path), ('POST', '/api/chat'))
                self.assertEqual({key.lower() for key in headers} & {'authorization', 'proxy-authorization', 'cookie'}, set())
                self.assertEqual(json.loads(raw), {'model': MODEL, 'messages': messages, 'stream': False})
        self.assertTrue(self.received.empty())

    def test_unavailable_redirect_and_invalid_replies_do_not_leak_a_peer_body(self):
        for status, headers, body, error in (
            (503, {}, b'private-synthetic-peer-detail', 21),
            (302, {'Location': self.origin + '/redirect-target'}, b'', 21),
            (200, {}, b'{"private-synthetic-peer-detail":true}', 22),
            (200, {}, json.dumps({**REPLY, 'done': False}).encode(), 22),
            (200, {}, json.dumps({**REPLY, 'message': {**REPLY['message'], 'tool_calls': [1]}}).encode(), 22),
            (200, {'Content-Length': '1024'}, json.dumps(REPLY).encode(), 22),
            (200, {}, b'x' * (256 * 1024 + 1), 23),
        ):
            with self.subTest(status=status, error=error):
                self.reply = (status, headers, body)
                response = self.request()
                self.assertEqual(self.received.get_nowait()[:2], ('POST', '/api/chat'))
                self.assertTrue(self.received.empty())
                self.assertEqual((response.returncode, response.stdout, response.stderr), (error, b'', b''))

    def test_invalid_worker_requests_send_zero_http_requests(self):
        for destination in (
            self.url + '?synthetic-query', self.url + '#synthetic-fragment', self.origin,
            self.url.replace('://', '://synthetic-user@'), self.url.replace('127.0.0.1', 'localhost'),
        ):
            with self.subTest(destination=destination):
                response = self.request(destination=destination)
                self.assertEqual((response.returncode, response.stdout, response.stderr), (20, b'', b''))
        response = self.request(request={'model': MODEL, 'messages': [QUESTION], 'stream': True})
        self.assertEqual((response.returncode, response.stdout, response.stderr), (20, b'', b''))
        response = self.request(request={'model': MODEL, 'messages': [QUESTION], 'stream': False, 'tools': []})
        self.assertEqual((response.returncode, response.stdout, response.stderr), (20, b'', b''))
        for messages in (
            [{**QUESTION, 'role': 'system'}, REPLY['message'], QUESTION],
            [QUESTION, QUESTION, QUESTION],
            [QUESTION, REPLY['message']] * 5 + [QUESTION],
        ):
            response = self.request(messages)
            self.assertEqual((response.returncode, response.stdout, response.stderr), (20, b'', b''))
        response = self.run_worker(b'{"destination":"repeated","destination":"field"}\n')
        self.assertEqual((response.returncode, response.stdout, response.stderr), (20, b'', b''))
        self.assertTrue(self.received.empty())
