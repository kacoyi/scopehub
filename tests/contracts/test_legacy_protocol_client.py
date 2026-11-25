import unittest

from contracts.generated.protocol import Message
from worker_runtime import protocol
from worker_runtime.protocol import decode_line, encode_message


def read_message(frame: bytes) -> Message:
    return decode_line(frame)


class LegacyProtocolClientTests(unittest.TestCase):
    def test_protocol_module_exports_message(self) -> None:
        self.assertIs(getattr(protocol, 'Message'), Message)

    def test_typed_client_round_trips_original_heartbeat(self) -> None:
        frame = (b'{"protocol_version":"1.0","message_id":"00000000-0000-4000-8000-000000000001",'
                 b'"timestamp_ms":1.0,"type":"heartbeat"}\r\n')
        message = read_message(frame)
        self.assertIs(type(message['timestamp_ms']), int)
        self.assertEqual(read_message(encode_message(message)), message)


if __name__ == '__main__':
    unittest.main()
