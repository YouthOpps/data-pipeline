"""A second, encrypted state custody route through the original Actions job log."""
import base64
import sys

from authority import canonical, digest, require

PREFIX = 'IKY491-ESCROW'
CHUNK = 800
MAX_FRAMES = 300
MAX_CHUNKS = 48


class Escrow:
    def __init__(self, authority, seal, sign, stream=None):
        self.authority = authority
        self.seal = seal
        self.sign = sign
        self.stream = stream if stream is not None else sys.__stdout__
        self.sequence = 0
        self.previous = '0' * 64
        self.journal_sha256 = None
        self.terminal = False

    def append(self, label, data):
        require(not self.terminal and self.sequence < MAX_FRAMES, 'Escrow frame limit reached')
        sequence = self.sequence + 1
        envelope = {'schema': 1, 'sequence': sequence, 'previous_cipher_sha256': self.previous,
                    'kind': label, 'custody': self.authority, 'payload': data}
        ciphertext = self.seal(canonical(envelope))
        checksum = digest(ciphertext)
        signature = base64.b64encode(self.sign(ciphertext)).decode('ascii')
        require(64 <= len(signature) <= 120, 'Invalid escrow signature')
        encoded = base64.b64encode(ciphertext).decode('ascii')
        parts = [encoded[offset:offset+CHUNK] for offset in range(0, len(encoded), CHUNK)]
        require(0 < len(parts) <= MAX_CHUNKS, 'Escrow ciphertext exceeds its bound')
        for number, part in enumerate(parts, 1):
            print(PREFIX, sequence, number, len(parts), checksum, signature, part,
                  file=self.stream, flush=True)
        self.sequence = sequence
        self.previous = checksum

    def acknowledge(self, raw, *, pending, opener_count):
        fingerprint = digest(raw)
        self.append('ack', {'journal_sha256': fingerprint, 'pending': pending,
                            'openers_so_far': opener_count,
                            'journal_base64': base64.b64encode(raw).decode('ascii')})
        self.journal_sha256 = fingerprint
        return self.sequence

    def require_pending(self, raw):
        require(digest(raw) == self.journal_sha256 and self.sequence > 0,
                'Exact reservation lacks a flushed encrypted job-log checkpoint')

    def finish(self, evidence):
        self.append('terminal', {**evidence, 'latest_ack_sequence': self.sequence or None,
                                 'latest_journal_sha256': self.journal_sha256})
        self.terminal = True
