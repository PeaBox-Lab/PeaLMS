import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
import traceback
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .db import init_db
from .seed import seed
from . import services
from . import hris

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / 'static'
DEMO_MODE = os.environ.get('LMS_DEMO_MODE','false').lower() in ('1','true','yes')
ALLOW_REMOTE_DEMO = os.environ.get('LMS_ALLOW_REMOTE_DEMO','false').lower() in ('1','true','yes')
SESSION_SECRET = os.environ.get('LMS_SESSION_SECRET') or secrets.token_hex(32)

ROLE_PERMISSIONS = {
  'learner': {'read','complete_self'},
  'manager': {'read','complete_self'},
  'instructor': {'read','complete_any'},
  'auditor': {'read','audit'},
  'learning_admin': {'read','manage_learning','manage_people','hris','audit','complete_any'},
  'platform_admin': {'read','manage_learning','manage_people','hris','audit','complete_any'}
}

def issue_demo_token(role, actor):
    payload = json.dumps({'role':role,'actor':actor,'exp':int(time.time())+3600}, separators=(',',':')).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b'=')
    signature = hmac.new(SESSION_SECRET.encode(), encoded, hashlib.sha256).hexdigest().encode()
    return (encoded + b'.' + signature).decode()

def verify_token(token):
    try:
        encoded, signature = token.encode().split(b'.',1)
        expected = hmac.new(SESSION_SECRET.encode(), encoded, hashlib.sha256).hexdigest().encode()
        if not hmac.compare_digest(signature, expected): return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + b'=' * (-len(encoded)%4)))
        if int(payload.get('exp',0)) < int(time.time()): return None
        if payload.get('role') not in ROLE_PERMISSIONS: return None
        return payload
    except Exception: return None

class Handler(BaseHTTPRequestHandler):
    server_version = 'CorporateLMS/0.1.0'

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    @property
    def identity(self):
        auth = self.headers.get('Authorization','')
        return verify_token(auth[7:]) if auth.startswith('Bearer ') else None
    @property
    def role(self): return (self.identity or {}).get('role')
    @property
    def actor(self): return (self.identity or {}).get('actor')

    def require(self, permission):
        if not self.identity:
            self.json({'error':'unauthorized','message':'A valid session is required'}, 401)
            return False
        if permission not in ROLE_PERMISSIONS.get(self.role,set()):
            self.json({'error':'forbidden','message':f'Role {self.role} lacks {permission}'}, 403)
            return False
        return True

    def json(self, payload, status=200):
        raw = json.dumps(payload, default=str).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        self.end_headers(); self.wfile.write(raw)

    def body(self):
        length = int(self.headers.get('Content-Length','0'))
        if length < 0 or length > 5_000_000: raise ValueError('Invalid request body length')
        raw = self.rfile.read(length) if length else b'{}'
        return json.loads(raw.decode('utf-8'))

    def static(self, path):
        rel = 'index.html' if path in ('/','') else path.lstrip('/')
        file = (STATIC / rel).resolve()
        if STATIC.resolve() not in file.parents or not file.is_file():
            return self.json({'error':'not_found'},404)
        data = file.read_bytes(); mime = mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
        self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        try:
            parsed=urlparse(self.path); path=parsed.path; qs=parse_qs(parsed.query)
            if path == '/api/health': return self.json({'status':'ok','version':'0.1.0','demo_mode':DEMO_MODE})
            if path.startswith('/api/') and not self.require('read'): return
            if path == '/api/dashboard':
                return self.json(services.personal_dashboard(self.actor) if self.role in ('learner','manager') else services.dashboard())
            if path == '/api/me': return self.json(services.get_employee(self.actor) or {'employee_id':self.actor,'display_name':self.actor,'roles':[]})
            if path == '/api/employees':
                if not self.require('manage_people'): return
                return self.json(services.list_employees())
            if path == '/api/courses': return self.json(services.list_courses())
            if path == '/api/policies':
                if not self.require('manage_learning'): return
                return self.json(services.list_policies())
            if path == '/api/assignments':
                requested = qs.get('employee_id',[None])[0]
                if self.role in ('learner','manager'):
                    requested = self.actor
                elif not self.require('complete_any'): return
                return self.json(services.list_assignments(requested))
            if path == '/api/audit':
                if not self.require('audit'): return
                return self.json(services.audit_events(int(qs.get('limit',[200])[0])))
            if path == '/api/import-jobs':
                if not self.require('hris'): return
                return self.json(services.import_jobs())
            return self.static(path)
        except Exception as exc: self.fail(exc)

    def do_POST(self):
        try:
            path=urlparse(self.path).path; data=self.body()
            if path == '/api/session':
                if not DEMO_MODE: return self.json({'error':'disabled','message':'Demo sessions are disabled; configure enterprise authentication.'},403)
                role = str(data.get('role','learner'))
                actor = str(data.get('actor','')).strip()
                if role not in ROLE_PERMISSIONS or not actor: return self.json({'error':'validation_error','message':'Valid role and actor are required'},400)
                return self.json({'token':issue_demo_token(role,actor),'role':role,'actor':actor,'expires_in':3600})
            if path == '/api/employees':
                if not self.require('manage_people'): return
                return self.json(services.create_employee(data,self.actor),201)
            if path == '/api/courses':
                if not self.require('manage_learning'): return
                return self.json(services.create_course(data,self.actor),201)
            if path == '/api/policies':
                if not self.require('manage_learning'): return
                return self.json(services.create_policy(data,self.actor),201)
            if path == '/api/assignments':
                if not self.require('manage_learning'): return
                return self.json(services.assign_course(data['employee_id'],data['course_code'],data.get('due_days',30),self.actor),201)
            if path == '/api/assignments/complete':
                if not self.identity: return self.json({'error':'unauthorized','message':'A valid session is required'},401)
                target = str(data['employee_id'])
                if self.role in ('learner','manager'):
                    if not self.require('complete_self'): return
                    if self.actor != target: return self.json({'error':'forbidden','message':'This role may complete only its own assignments'},403)
                elif not self.require('complete_any'): return
                return self.json(services.complete_assignment(target,data['course_code'],data.get('score'),self.actor))
            if path == '/api/policies/apply':
                if not self.require('manage_learning'): return
                return self.json({'assignments_created':services.apply_policies(actor=self.actor)})
            if path == '/api/hris/analyze':
                if not self.require('hris'): return
                result=hris.analyze(data.get('csv',''),data.get('mode','full'),int(data.get('max_population_change_pct',25)))
                result.pop('normalized',None); return self.json(result)
            if path == '/api/hris/apply':
                if not self.require('hris'): return
                result=hris.apply(data.get('csv',''),data.get('filename','employee_extract.csv'),data.get('mode','full'),self.actor,int(data.get('max_population_change_pct',25)))
                return self.json(result,201)
            return self.json({'error':'not_found'},404)
        except (ValueError,KeyError) as exc: self.json({'error':'validation_error','message':str(exc)},400)
        except Exception as exc: self.fail(exc)

    def fail(self, exc):
        traceback.print_exc()
        self.json({'error':'internal_error','message':'The operation failed. Check server logs for details.'},500)

def main():
    parser=argparse.ArgumentParser(description='Corporate LMS MVP server')
    parser.add_argument('--host',default=os.environ.get('LMS_HOST','127.0.0.1'))
    parser.add_argument('--port',type=int,default=int(os.environ.get('LMS_PORT','8080')))
    parser.add_argument('--no-seed',action='store_true')
    args=parser.parse_args()
    if DEMO_MODE and args.host not in ('127.0.0.1','localhost','::1') and not ALLOW_REMOTE_DEMO:
        parser.error('Demo mode may bind only to loopback unless LMS_ALLOW_REMOTE_DEMO=true is explicitly set')
    init_db()
    if not args.no_seed: seed()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    print(f'Corporate LMS v0.1.0 running at http://{args.host}:{args.port}')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
