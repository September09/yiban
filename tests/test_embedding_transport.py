"""Exercise local HTTP embedding transport, including a blocked request cancellation."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

from yiban.memory.retrieval import OllamaEmbedding, RetrievalUnavailable
from yiban.turns import Cancellation, Cancelled


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        data={'models':[{'name':'test-model','digest':'a'*64}]} if self.server.valid else []
        raw=json.dumps(data).encode();self.send_response(200);self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.body=body;self.server.entered.set()
        if self.server.block:self.server.release.wait(3)
        raw=json.dumps({'embeddings':[[1.0]+[0.0]*15 for _ in body['input']]}).encode()
        try:self.send_response(200);self.end_headers();self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass


class EmbeddingTransportTests(unittest.TestCase):
    def setUp(self):
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.http.valid=True;self.http.block=False;self.http.entered=threading.Event();self.http.release=threading.Event()
        self.worker=threading.Thread(target=self.http.serve_forever,daemon=True);self.worker.start()
        self.provider=OllamaEmbedding({'model':'test-model','base_url':f'http://127.0.0.1:{self.http.server_port}'})
    def tearDown(self):self.http.release.set();self.http.shutdown();self.http.server_close();self.worker.join()

    def test_contract_and_bad_metadata(self):
        self.assertIn('a'*64,self.provider.fingerprint(Cancellation(),time.monotonic()+2))
        self.assertEqual(len(self.provider.embed(['hello'],Cancellation(),time.monotonic()+2)),1)
        self.assertFalse(self.http.body['truncate']);self.assertEqual(self.http.body['options']['num_ctx'],4096)
        self.http.valid=False
        with self.assertRaises(RetrievalUnavailable):self.provider.fingerprint(Cancellation(),time.monotonic()+2)

    def test_cancellation_interrupts_blocked_network_read(self):
        self.http.block=True;cancel=Cancellation();errors=[]
        def request():
            try:self.provider.embed(['hello'],cancel,time.monotonic()+10)
            except Exception as error:errors.append(error)
        task=threading.Thread(target=request);task.start();self.assertTrue(self.http.entered.wait(1))
        cancel.cancel();task.join(1)
        self.assertFalse(task.is_alive());self.assertIsInstance(errors[0],Cancelled)

    def test_timeout_does_not_leave_pending_request(self):
        self.http.block=True;started=time.monotonic()
        with self.assertRaises(RetrievalUnavailable):self.provider.embed(['hello'],Cancellation(),started+.1)
        self.assertLess(time.monotonic()-started,1)


if __name__=='__main__':unittest.main()
