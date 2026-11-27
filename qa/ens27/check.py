import copy
import http.client
import socket
import ssl
import sys
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, 'adapters/fr-ens-paris-international-selection')
import adapter

for reason, expected in [
    (ssl.SSLCertVerificationError('private.example'), 'SSLCertVerificationError'),
    (ssl.SSLError('private.example'), 'SSLError'),
    (socket.gaierror('private.example'), 'gaierror'),
    (TimeoutError('private.example'), 'TimeoutError'),
    (ConnectionResetError('private.example'), 'ConnectionResetError'),
    (ConnectionRefusedError('private.example'), 'ConnectionRefusedError'),
    ('private.example', None),
]:
    adapter.AUDIT['requests'].clear()
    adapter.GATE_FAILED = False
    journal = Mock(spec=adapter.Journal)
    journal.first_wire_proof = Mock()
    journal.read.return_value = copy.deepcopy(adapter.NATIVE_FLOOR)
    with patch.object(adapter, 'wait_for_request'), patch.object(
            adapter.OPENER, 'open', side_effect=urllib.error.URLError(reason)) as opener:
        try:
            adapter._request(journal, adapter.ROBOTS)
        except urllib.error.URLError:
            pass
        else:
            raise AssertionError('missing failure')
    opener.assert_called_once()
    witness = adapter.AUDIT['requests'][0]['transport']
    assert witness.get('url_error_reason') == expected
    assert 'private.example' not in str(witness)
    assert not any(witness[key] for key in ('connection_started', 'connection_established', 'response_started'))
    assert adapter.GATE_FAILED
    assert journal.save.call_args.args[0]['physical_pending']

for failure in (True, False):
    witness = {'connection_established': False}
    conn = adapter.WitnessConnection('fixture.invalid', witness=witness)
    with patch.object(http.client.HTTPSConnection, 'connect', side_effect=TimeoutError() if failure else None):
        try:
            conn.connect()
        except TimeoutError:
            assert failure
    assert witness['connection_started'] and witness['connection_established'] is (not failure)
    with patch.object(http.client.HTTPSConnection, 'getresponse', side_effect=ConnectionResetError()):
        try:
            conn.getresponse()
        except ConnectionResetError:
            pass
    assert witness['response_started'] and witness['statuses'] == []
print('Seven wrapped errors and two transport paths verified with no network')
