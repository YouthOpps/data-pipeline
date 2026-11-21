import contextlib, importlib.util, json, os, tempfile, time
from pathlib import Path
from unittest.mock import patch
spec = importlib.util.spec_from_file_location("adapter", "adapters/google/adapter.py")
a=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
url="https://www.google.com/private"
assert a.rep_allowed(b"User-agent: *\nDisallow: /private\nAllow: /private$",url)
assert not a.rep_allowed(b"User-agent: *\nDisallow: /private",url)
for policy,reason in [(b"User-agent: 123","user-agent token at line 1"), (b"User-agent: *\nAllow: x","rule path, length or wildcard count at line 2"), (b"User-agent: *\nDisallow: /%zz","percent escape at line 2"), (b"\n"*2049,"line bound"), (b"X"*65537,"byte bound"), (b"User-agent: *\n"*33,"user-agent bound"), ((b"User-agent: *\nAllow: /\n\n")*101,"group bound"), (b"User-agent: *\n"+b"Allow: /\n"*513,"rule bound")]:
    try: a.rep_allowed(policy,url)
    except a.AdapterError as e: assert e.stage=="access" and reason in str(e),e
    else: raise AssertionError(reason)
with tempfile.TemporaryDirectory() as td:
    head='a'*40
    env={'GITHUB_REPOSITORY':'YouthOpps/data-pipeline','GITHUB_REF':'refs/heads/main','GITHUB_SHA':head,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1','RUNNER_TEMP':td,'GOOGLE_PACING_ARTIFACT_PATH':td+'/pacing-state.json'}
    state=a.empty_budget(time.time());state.update(in_flight=True,flight_id='a'*32,flight_kind='policy',blocked=True,rate_limit_strikes=2)
    a.validate_budget(state)
    output=Path(td,'step-output');output.touch(mode=0o600);env['GITHUB_OUTPUT']=str(output)
    with patch.dict(os.environ,env), patch.object(a,'_STATE_TRUSTED',True), patch.object(a,'_PUBLISHER_GATE_FAILED',True),patch.object(a,'_HOSTED_IDENTITY',(123,1,'google',head)),patch.object(a,'locked_budget',contextlib.nullcontext),patch.object(a,'journal_verify'),patch.object(a,'load_budget',return_value=state):
        a.export_family_artifact()
        proof=Path(td,'google-pending-review.json')
        evidence=json.loads(proof.read_text());assert evidence['pending_journal']==state and evidence['purpose']=='manual-review-only'
        assert not Path(td,'pacing-state.json').exists()
        assert output.read_text()=='pending_review_ready=true\n'
        proof.unlink()
        with patch.object(a,'_JOURNAL_FATAL',True): a.export_family_artifact()
        assert not proof.exists()
        with patch.object(a,'_STATE_TRUSTED',False): a.export_family_artifact()
        assert not proof.exists()
        with patch.object(a,'_HOSTED_IDENTITY',(122,1,'google',head)):
            try: a.export_family_artifact()
            except a.AdapterError: pass
            else: raise AssertionError("foreign review accepted")
        assert not proof.exists()
print("PASS: REP bounds and decisions, strict owner, pending snapshot, absent healthy output, fatal/untrusted refusal")
