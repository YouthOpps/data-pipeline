from pathlib import Path
from unittest.mock import patch
from tempfile import TemporaryDirectory
from email.message import Message
import importlib.util, json, hashlib, os, time

import sys
def block_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        raise AssertionError("Offline control attempted network access")
sys.addaudithook(block_network)

def load(name):
    s=importlib.util.spec_from_file_location(name,Path('adapters/gr-iky/adapter.py'))
    a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
    return a
class Response:
    status=429
    headers=Message()
    def __enter__(self): return self
    def __exit__(self,*args): return False
Response.headers['Retry-After']='2400'
from contextlib import contextmanager
@contextmanager
def fixture():
 with TemporaryDirectory() as td:
    os.chmod(td,0o700)
    a=load('first')
    state=a.annotate_reviewed_refusal(a._LEGACY_STATE.copy())
    state.update(observed_at=time.time()-100,not_before=0,starts=[],completed=0)
    data=Path(td,'state.json')
    data.write_text(json.dumps(state));data.chmod(0o600)
    lock=Path(td,'state.lock');lock.touch(mode=0o600)
    collection=Path(td,'collection.lock');collection.touch(mode=0o600)
    a._STATE_PATH,a._STATE_LOCK=str(data),str(lock)
    a._COLLECTION_LOCK_FD=os.open(collection,os.O_RDWR)
    st=os.fstat(a._COLLECTION_LOCK_FD);a._GUARD_COLLECTION_ID=(st.st_dev,st.st_ino)
    yield a,data,lock,state
class Success(Response):
    status=200
    def read(self, size): return b'abc'
class BrokenExit(Response):
    def __exit__(self,*args): raise OSError('response close failed')
class Unreadable(Success):
    def read(self,size): raise OSError('body failed')
for typ in (Success,Response,BrokenExit,Unreadable):
    with fixture() as (a,path,lock,initial):
        with patch.object(a._OPENER,'open',return_value=typ()) as op:
            try: a.request_bytes('https://www.iky.gr/robots.txt',robots=True)
            except (a.HeaderRateLimitStop,OSError): pass
        assert op.call_count==1
        final=json.loads(path.read_text())
        assert final['request_pending'] is (typ in (BrokenExit,Unreadable))
        assert final['refusal_scope']==initial['refusal_scope'] and final['blocked']
        if typ in (Response,BrokenExit):
            assert final['rate_limit_until']>=time.time()+2399 and final['rate_limit_strikes']==1
        for left,right in ((initial,final),(final,initial)):
            merged=a.merge_refusal_scope({},left,right)
            assert merged['request_pending']==final['request_pending']
        print(typ.__name__, 'pass')
with fixture() as (a,path,lock,state):
    with patch.object(a.os,'ftruncate',side_effect=OSError('reservation failure')),patch.object(a._OPENER,'open') as op:
        try: a.request_bytes('https://www.iky.gr/robots.txt',robots=True)
        except OSError: pass
        assert op.call_count==0 and a._GUARD_FATAL
    for invalid in (0,1,'false',None):
        try: a.validate_budget(dict(state,request_pending=invalid))
        except a.AdapterError: pass
        else: raise AssertionError('marker type accepted')
    print('reservation failure and marker type rejection pass')
class Challenge(Success):
    headers=Message()
    headers['Content-Type']='text/html'
    def read(self,size): return b'<html><body><main>Verify you are human</main></body></html>'
for fail in (True,False):
    with fixture() as (a,path,lock,initial):
        real=a.os.ftruncate; counts=[]
        def trunc(fd,size):
            counts.append(size)
            if fail and len(counts)>1: raise OSError('simulated challenge/invalidation fault')
            return real(fd,size)
        with patch.object(a._OPENER,'open',return_value=Challenge()) as op,patch.object(a.os,'ftruncate',side_effect=trunc):
            try: a.request_bytes(a.SOURCE_URL)
            except (OSError,a.AdapterError): pass
            assert op.call_count==1
        fresh=load('second'); fresh._STATE_PATH,fresh._STATE_LOCK=str(path),str(lock)
        with fresh.locked_budget(): retained=fresh.load_budget()
        assert retained['blocked'] and (retained['request_pending'] if fail else retained['html_global_hold'])
        with patch.object(fresh._OPENER,'open') as op:
            try: fresh.request_bytes(a.SOURCE_URL)
            except fresh.AdapterError: pass
            assert op.call_count==0
        print('HTML challenge guarded, fault=',fail)
