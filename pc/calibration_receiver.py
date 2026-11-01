"""Dedicated LAN-only calibration receiver; never exposes serial or control routes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import json
import threading
import time

PAGE=Path(__file__).with_name('calibration_page')/'index.html'

class Receiver:
 def __init__(self,gate,host='0.0.0.0',port=8893):
  self.gate=gate
  outer=self
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    u=urlsplit(self.path); q=parse_qs(u.query)
    if q.get('token',[None])[0]!=outer.gate.token: return self.send_error(403)
    if u.path=='/':data=PAGE.read_bytes();kind='text/html; charset=utf-8'
    elif u.path=='/probe':
     if q.get('session',[None])[0]!=outer.gate.page_session:return self.send_error(403)
     with outer.gate.lock:
      outer.gate.last_poll=time.monotonic()
      active_probe=outer.gate.armed
     data=json.dumps({'probe':active_probe}).encode();kind='application/json'
    else:return self.send_error(404)
    self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
   def do_POST(self):
    if self.path not in ('/ready','/ack','/event'):return self.send_error(404)
    try:
     size=int(self.headers.get('Content-Length','0'))
     if not 0<size<4096:raise ValueError('size')
     d=json.loads(self.rfile.read(size));g=outer.gate
     if self.path=='/ready':g.ready(d['token'],d['session'],d['width'],d['height'])
     elif self.path=='/ack':g.ack(d['token'],d['session'],d['probe'])
     else:g.event(d['token'],d['session'],d['probe'],d['kind'],d['x'],d['y'],d['width'],d['height'],d.get('client_time'))
    except (ValueError,KeyError,TypeError,json.JSONDecodeError):return self.send_error(400)
    self.send_response(204);self.end_headers()
  self.server=ThreadingHTTPServer((host,port),Handler)
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True,name='calibration-page')
 def start(self):self.thread.start()
 def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
