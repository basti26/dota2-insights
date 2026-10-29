"""Bounded failure-only inner framing metadata; no payload or repair output."""
import hashlib
import re

MAX_BYTES = 1024 * 1024
MAX_MESSAGES = 4096
STATUSES = ('framing_complete', 'payload_length_out_of_bounds', 'header_truncated',
            'message_budget', 'packet_budget', 'diagnostic_error')


def inspect_inner(data, *, tick, message_limit=MAX_MESSAGES):
    if not isinstance(data, bytes) or type(message_limit) is not int or not 1 <= message_limit <= MAX_MESSAGES:
        raise ValueError('packet_diagnostic_input')
    result = {'schema_version': 1, 'kind': 'inner_packet_failure_diagnostic',
              'tick': tick if type(tick) is int and 0 <= tick < 2**32 else None,
              'packet_bytes': len(data), 'header_count': 0, 'packet_sha256': None,
              'status': 'packet_budget', 'invalid_header': None,
              'eligible_for_analysis': False, 'raw_payload_saved': False}
    if len(data) > MAX_BYTES:
        return result
    result['packet_sha256'] = hashlib.sha256(data).hexdigest()
    bit = 0
    total = len(data)*8

    def read(n):
        nonlocal bit
        if bit+n > total:
            raise EOFError
        value = 0
        for offset in range(n):
            value |= ((data[(bit+offset)//8] >> ((bit+offset)%8)) & 1) << offset
        bit += n
        return value

    for index in range(message_limit):
        if total-bit < 8:
            result['status'] = 'framing_complete'
            break
        start = bit
        try:
            kind = read(6)
            extra = {0: 0, 16: 4, 32: 8, 48: 28}[kind & 48]
            if extra:
                kind = (kind & 15) | (read(extra) << 4)
            size = 0
            for shift in range(0, 35, 7):
                byte = read(8)
                size |= (byte & 127) << shift
                if byte < 128:
                    break
            size &= 0xffffffff  # Same pinned Source 2 varuint32 framing.
        except EOFError:
            result.update(status='header_truncated', invalid_header={'message_index': index+1, 'start_bit': start})
            break
        header = {'message_index': index+1, 'start_bit': start, 'payload_start_bit': bit,
                  'type_id': kind, 'declared_bytes': size}
        if size*8 > total-bit:
            result.update(status='payload_length_out_of_bounds', invalid_header=header)
            break
        bit += size*8  # Skip content; never decode/hash/export individual message payloads.
        result['header_count'] += 1
    else:
        result['status'] = 'framing_complete' if total-bit < 8 else 'message_budget'
    result.update(stopped_bit=bit, remaining_bits=total-bit)
    return result


def public_summary(value):
    """Do not propagate arbitrary nested text or raw payloads from a manifest."""
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('kind') != 'inner_packet_failure_diagnostic' or value.get('status') not in STATUSES):
        return None
    result = {'schema_version': 1, 'kind': value['kind'], 'status': value['status'],
              'eligible_for_analysis': False, 'raw_payload_saved': False}
    for key in ('tick', 'packet_bytes', 'header_count', 'stopped_bit', 'remaining_bits'):
        n = value.get(key)
        result[key] = n if type(n) is int and 0 <= n <= 2**32 else None
    digest = value.get('packet_sha256')
    result['packet_sha256'] = digest if isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest) else None
    header = value.get('invalid_header')
    result['invalid_header'] = ({k: header[k] for k in
        ('message_index', 'start_bit', 'payload_start_bit', 'type_id', 'declared_bytes')
        if type(header.get(k)) is int and 0 <= header[k] <= 2**32} if isinstance(header, dict) else None)
    return result


def attach(parser, data_error):
    """Pinned private callback: one failure record, original exception preserved."""
    original = parser._dispatch_inner_packet
    records = []

    def dispatch(data):
        try:
            return original(data)
        except data_error:
            if not records:
                try:
                    records.append(inspect_inner(data, tick=parser.tick))
                except Exception:
                    # A diagnostic must never replace the actual parser failure.
                    records.append({'schema_version': 1, 'kind': 'inner_packet_failure_diagnostic',
                                    'status': 'diagnostic_error', 'eligible_for_analysis': False,
                                    'raw_payload_saved': False})
            raise

    parser._dispatch_inner_packet = dispatch
    return records
