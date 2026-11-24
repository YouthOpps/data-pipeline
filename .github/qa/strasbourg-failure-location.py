"""Exercise the production error formatter with local exceptions only."""
import importlib.util
import json
import sys
from pathlib import Path

path = Path('adapters/fr-universite-de-strasbourg-m1-admissions/adapter.py')
spec = importlib.util.spec_from_file_location('strasbourg', path)
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)

assert adapter.failure_location(ValueError('secret')) == {}
try:
    adapter.validate_records([{}])
except Exception as error:
    details = adapter.failure_location(error)
    assert details['function'] == 'validate_records' and details['line'] > 0
    assert set(details) == {'function', 'line'}
else:
    raise AssertionError('Invalid input accepted')

for status, expected in [(403, {'http_status': 403}), (429, {'http_status': 429}), (True, {}), (999, {}), (None, {})]:
    try:
        raise adapter.AdapterError('secret signed URL and sensitive body', status=status)
    except adapter.AdapterError as error:
        result = adapter.failure_location(error)
        assert result == expected and 'secret' not in json.dumps(result)
print('Error location, status boundaries and redaction verified. Zero publisher requests.')
