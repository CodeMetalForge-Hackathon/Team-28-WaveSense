"""Hiwonder Pi 5 expansion board protocol (UART /dev/ttyAMA0 @ 1,000,000 baud).

Frames: AA 55 | func | len | data... | crc8(func, len, data)   (see robot/nudge.py)
  func 3, data 05 n {idx-1 (u8), duty (f32 LE)}*n   motor duty, -100..100
  func 0, data 04 <u16 mV>                          battery report, sent by the
                                                    board unprompted (~9 Hz)
Signs and the duty cap come from robot/nudge.py (verified in robot/MOTORS.md).
"""
import struct

from robot.nudge import FORWARD_SIGN, MAX_DUTY, crc8

PORT = "/dev/ttyAMA0"
BAUD = 1_000_000
MOTORS = (1, 2, 3, 4)          # 1 front-left, 2 front-right, 3 rear-left, 4 rear-right

__all__ = ["PORT", "BAUD", "MOTORS", "FORWARD_SIGN", "MAX_DUTY", "frame", "motor_frame",
           "STOP_FRAME", "FrameParser", "open_port"]


def frame(func, data):
    body = bytes([func, len(data)]) + bytes(data)
    return b"\xAA\x55" + body + bytes([crc8(body)])


def motor_frame(duties):
    """duties: {motor: signed board duty}. Clamped to +-MAX_DUTY here as a last line of defence."""
    data = bytes([0x05, len(duties)])
    for m, d in duties.items():
        d = max(-MAX_DUTY, min(MAX_DUTY, float(d)))
        data += struct.pack("<Bf", m - 1, d)
    return frame(3, data)


STOP_FRAME = motor_frame({m: 0.0 for m in MOTORS})


def open_port(port=PORT):
    import serial
    s = serial.Serial(None, BAUD, timeout=0.05, write_timeout=0.1)
    s.rts = False
    s.dtr = False
    s.port = port
    s.open()
    return s


class FrameParser:
    """Incremental parser for frames sent by the board; yields (func, data) with valid CRC."""

    def __init__(self):
        self.buf = bytearray()
        self.bad_crc = 0

    def feed(self, chunk):
        self.buf += chunk
        out = []
        while True:
            i = self.buf.find(b"\xAA\x55")
            if i < 0:
                del self.buf[:-1]
                return out
            del self.buf[:i]
            if len(self.buf) < 4:
                return out
            n = self.buf[3]
            if len(self.buf) < 5 + n:
                return out
            body = bytes(self.buf[2:4 + n])
            if crc8(body) == self.buf[4 + n]:
                out.append((body[0], body[2:]))
                del self.buf[:5 + n]
            else:
                self.bad_crc += 1
                del self.buf[:2]
