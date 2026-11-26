import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch
from xml.sax.saxutils import escape

spec = importlib.util.spec_from_file_location(
    'pladis', Path('adapters/yildiz-pladis-careers/adapter.py'))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
valid = adapter.ORIGIN + '/pladis_Careers/job/ab/Role_JR123'
changed = adapter.ORIGIN + '/pladis_Careers/job/Unknown confidential title'
state = {'pending': False, 'events': []}


def sitemap(*locations):
    return ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
            ''.join('<url><loc>' + escape(url) + '</loc></url>' for url in locations) +
            '</urlset>')


with patch.object(adapter, 'validate', return_value=None):
    assert adapter.sitemap_urls(sitemap(valid), state) == [valid]
    for item, rejected, reason in [
        (changed, changed, 'Outside observed public service inventory'),
        (' https://foreign.example/secret ', 'https://foreign.example/secret', 'Unadmitted service/origin alias'),
        (valid + '?token=private', valid + '?token=private', 'Ambiguous route identity'),
        (adapter.SITEMAP, adapter.SITEMAP, 'Non-job inventory entry'),
        (valid, valid, 'Duplicate canonical sitemap URL')
    ]:
        try:
            adapter.sitemap_urls(sitemap(valid, item), state)
        except adapter.ScopeError as exc:
            digest = hashlib.sha256(rejected.encode()).hexdigest()
            assert adapter.failure_details(exc)['message'] == f'{reason}; sitemap entry 2, SHA256 {digest}'
            assert rejected not in str(exc)
        else:
            raise AssertionError('Unapproved inventory passed')

try:
    adapter.sitemap_urls(sitemap(valid), {})
except adapter.ScopeError as exc:
    assert str(exc).startswith('Unknown state schema/fields')
else:
    raise AssertionError('Invalid state was accepted')
print('Valid, unexpected path, foreign origin, query, non-job, duplicate and invalid state checks passed.')
