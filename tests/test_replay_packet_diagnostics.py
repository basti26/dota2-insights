"""Generated bit streams and callback substitutes, never real replay bodies."""
import types
import unittest
from unittest.mock import patch

from dota2_insights.replay_packet_diagnostics import inspect_inner, public_summary, attach, MAX_BYTES


def encoded(messages):
    bits=[]
    def put(n,width):bits.extend((n>>i)&1 for i in range(width))
    for kind,size,body in messages:
        width=0 if kind<16 else 4 if kind<256 else 8 if kind<4096 else 28
        put(kind if not width else (kind&15)|{4:16,8:32,28:48}[width],6)
        if width:put(kind>>4,width)
        remaining=size
        while remaining>=128:
            put((remaining&127)|128,8);remaining>>=7
        put(remaining,8)
        for byte in body:put(byte,8)
    out=bytearray((len(bits)+7)//8)
    for i,bit in enumerate(bits):out[i//8]|=bit<<(i%8)
    return bytes(out)


class PacketDiagnosticTests(unittest.TestCase):
    def test_scalar_headers_and_large_invalid_length_without_payload_export(self):
        data=encoded([(0,0,b''),(145,3,b'abc'),(554,2,b'xy'),(524550143,1205837,b'')])
        r=inspect_inner(data,tick=80412)
        self.assertEqual(r['header_count'],3)
        self.assertEqual(r['invalid_header']['message_index'],4)
        self.assertEqual(r['invalid_header']['declared_bytes'],1205837)
        self.assertEqual(r['status'],'payload_length_out_of_bounds')
        self.assertFalse(r['eligible_for_analysis'])
        self.assertFalse(r['raw_payload_saved'])
        self.assertNotIn('payload',r)

    def test_valid_empty_and_all_type_widths(self):
        for types_ in ([],[0,15,16,255,256,4095,4096,0xffffffff]):
            r=inspect_inner(encoded([(kind,1,b'x') for kind in types_]),tick=0)
            self.assertEqual(r['status'],'framing_complete')
            self.assertEqual(r['header_count'],len(types_))
            self.assertEqual(r['tick'],0)

    def test_byte_message_and_partial_header_budgets(self):
        self.assertEqual(inspect_inner(b'x'*(MAX_BYTES+1),tick=1)['status'],'packet_budget')
        r=inspect_inner(encoded([(0,0,b'')]*3),tick=1,message_limit=2)
        self.assertEqual(r['status'],'message_budget')
        self.assertEqual(r['header_count'],2)
        self.assertEqual(inspect_inner(b'\xff',tick=1)['status'],'header_truncated')

    def test_public_summary_drops_untrusted_text_and_payload(self):
        r=inspect_inner(encoded([(1,4000,b'')]),tick=1)
        r['payload']='must not propagate';r['invalid_header']['private_text']='private'
        r['tick']=True
        summary=public_summary(r)
        self.assertIsNone(summary['tick'])
        self.assertNotIn('payload',summary)
        self.assertNotIn('private_text',summary['invalid_header'])
        self.assertIsNone(public_summary({'kind':'wrong'}))

    def test_wrapper_only_probes_data_errors_and_keeps_original_exception(self):
        class DataError(Exception):pass
        error=DataError('not exported')
        calls=[]
        def original(data):
            calls.append(data)
            if data==b'bad':raise error
            if data==b'other':raise RuntimeError('other')
            return 123
        parser=types.SimpleNamespace(tick=80412,_dispatch_inner_packet=original)
        records=attach(parser,DataError)
        with patch('dota2_insights.replay_packet_diagnostics.inspect_inner',wraps=inspect_inner) as probe:
            self.assertEqual(parser._dispatch_inner_packet(b'ok'),123)
            probe.assert_not_called()
            with self.assertRaises(RuntimeError):parser._dispatch_inner_packet(b'other')
            self.assertEqual(records,[])
            for _ in range(2):
                with self.assertRaises(DataError) as caught:parser._dispatch_inner_packet(b'bad')
                self.assertIs(caught.exception,error)
            self.assertEqual(probe.call_count,1)
        self.assertEqual(len(records),1)

    def test_diagnostic_error_cannot_replace_parser_failure(self):
        class DataError(Exception):pass
        error=DataError()
        def fail(_):raise error
        parser=types.SimpleNamespace(tick=1,_dispatch_inner_packet=fail)
        records=attach(parser,DataError)
        with patch('dota2_insights.replay_packet_diagnostics.inspect_inner',side_effect=ValueError('probe')):
            with self.assertRaises(DataError) as caught:parser._dispatch_inner_packet(b'x')
        self.assertIs(caught.exception,error)
        self.assertEqual(public_summary(records[0])['status'],'diagnostic_error')


if __name__=='__main__':unittest.main()
