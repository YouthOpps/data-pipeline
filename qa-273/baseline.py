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
    s=importlib.util.spec_from_file_location(name,Path('before.py'))
    a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
    return a
class Response:
    status=429
    headers=Message()
    def __enter__(self): return self
    def __exit__(self,*args): return False
Response.headers['Retry-After']='2400'
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
    real=a.os.ftruncate
    calls=[]
    def trunc(fd,size):
        calls.append(size)
        if len(calls)>1: raise OSError('simulated storage fault')
        return real(fd,size)
    with patch.object(a._OPENER,'open',return_value=Response()) as opener,patch.object(a.os,'ftruncate',side_effect=trunc):
        try: a.request_bytes('https://www.iky.gr/robots.txt',robots=True)
        except OSError: pass
        assert opener.call_count==1
    saved=json.loads(data.read_text())
    assert a._GUARD_FATAL and saved['rate_limit_strikes']==0 and a._GUARD_FAILED_STATE['rate_limit_strikes']==1
    fresh=load('second');fresh._STATE_PATH,fresh._STATE_LOCK=str(data),str(lock)
    with fresh.locked_budget(): recovered=fresh.load_budget()
    assert recovered==saved and recovered['rate_limit_until']==0
    print(json.dumps(dict(actual_network_requests=0,simulated_requests=1,
        ftruncate_calls=len(calls),fresh_strikes=recovered['rate_limit_strikes'],
        fresh_embargo=recovered['rate_limit_until'],in_memory_strikes=a._GUARD_FAILED_STATE['rate_limit_strikes'],
        simulated_retry_after=2400,remaining_api_only_block=recovered['blocked'],
        guard_fatal=a._GUARD_FATAL,adapter_sha256=hashlib.sha256(Path(a.__file__).read_bytes()).hexdigest())))
