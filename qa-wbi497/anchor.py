"""Establish an authenticated per-run escrow public key before publisher access."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from authority import ROOT, Custody, api, canonical, digest, require


def paths():
    runner=Path(os.environ['RUNNER_TEMP'])
    require(runner.resolve()==runner, 'Unsafe recovery namespace')
    return runner/'wbi497-signer'/'private.pem', runner/'wbi497-recovery-key'/'anchor.json'


def openssl(*args, **kwargs):
    return subprocess.check_output(['openssl', *args], timeout=20, **kwargs)


def public_key(private):
    return openssl('pkey','-in',str(private),'-pubout').decode('ascii')


def verify_anchor(custody):
    private, public = paths()
    info=private.lstat()
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==0o600 and info.st_uid==os.getuid()
            and info.st_nlink==1 and private.resolve()==private, 'Unsafe ephemeral signer')
    raw=public.read_bytes()
    require(json.loads(raw)=={'schema':1,'custody':custody.receipt(),'public_key_pem':public_key(private)},
            'Recovery key does not bind exact custody')
    value=os.environ.get('WBI_RECOVERY_ANCHOR_ID',''); expected=os.environ.get('WBI_RECOVERY_ANCHOR_DIGEST','')
    require(re.fullmatch('[1-9][0-9]*',value) and re.fullmatch('[a-f0-9]{64}',expected),
            'Before-wire key upload is unavailable')
    artifact_id=int(value)
    row=None
    for delay in (1,2,3,4):
        try:
            row=api(f'/actions/artifacts/{artifact_id}')
        except OSError:
            time.sleep(delay)
        else:
            break
    require(row is not None and row['id']==artifact_id and row['digest']=='sha256:'+expected
            and row['name']==f'wbi497-recovery-key-{custody.run}-1' and row['expired'] is False
            and row['workflow_run']['id']==custody.run and row['workflow_run']['head_sha']==custody.head
            and row['workflow_run']['head_branch']==custody.receipt()['branch'], 'Recovery anchor ownership differs')
    return private, {'id':artifact_id,'zip_sha256':expected,'anchor_sha256':digest(raw)}


def main():
    custody=Custody((ROOT/'qa-wbi497/scope.json').read_bytes())
    proof=custody.launch()
    private, public=paths()
    private.parent.mkdir(mode=0o700)
    public.parent.mkdir(mode=0o700)
    private.touch(mode=0o600,exist_ok=False)
    raw=openssl('genpkey','-algorithm','EC','-pkeyopt','ec_paramgen_curve:prime256v1')
    private.write_bytes(raw)
    with public.open('xb') as stream:
        stream.write(canonical({'schema':1,'custody':proof,'public_key_pem':public_key(private)}))
        stream.flush();os.fsync(stream.fileno())


if __name__=='__main__':
    main()
