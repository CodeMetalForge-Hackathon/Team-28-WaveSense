#!/usr/bin/env python3
"""
MasterPi nudge test: beep, spin one wheel, or roll forward a tiny bit.
Pi 5 expansion board: UART /dev/ttyAMA0 @ 1,000,000 baud, 0xAA 0x55 frames + CRC8
Older I2C board (--i2c): bus 1, address 0x7A, motor registers 31..34
Forward signs (M1 -, M2 +, M3 -, M4 +) from Hiwonder's mecanum.py.
"""
import argparse, signal, struct, sys, time

MAX_DUTY = 50
MAX_TIME = 1.0
FORWARD_SIGN = {1: -1, 2: +1, 3: -1, 4: +1}

def crc8(data):
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0x8C if c & 1 else c >> 1
    return c

class UartBoard:
    def __init__(self, port):
        import serial
        self.ser = serial.Serial(None, 1_000_000, timeout=1)
        self.ser.rts = False
        self.ser.dtr = False
        self.ser.port = port
        self.ser.open()
    def _send(self, func, data):
        body = bytes([func, len(data)]) + bytes(data)
        self.ser.write(b"\xAA\x55" + body + bytes([crc8(body)]))
        self.ser.flush()
    def beep(self):
        self._send(2, struct.pack("<HHHH", 1900, 100, 100, 1))
    def motors(self, duties):
        data = bytes([0x05, len(duties)])
        for m, d in duties.items():
            data += struct.pack("<Bf", m - 1, float(d))
        self._send(3, data)

class I2cBoard:
    ADDR = 0x7A
    MOTOR_REG = 31
    def __init__(self):
        from smbus2 import SMBus, i2c_msg
        self.bus = SMBus(1)
        self.msg = i2c_msg
    def beep(self):
        raise SystemExit("beep not supported on the I2C board; run 'wheel 1' instead")
    def motors(self, duties):
        for m, d in duties.items():
            val = int(d).to_bytes(1, "little", signed=True)[0]
            self.bus.i2c_rdwr(self.msg.write(self.ADDR, [self.MOTOR_REG + m - 1, val]))

def stop(board):
    for _ in range(3):
        try:
            board.motors({1: 0, 2: 0, 3: 0, 4: 0})
        except Exception as e:
            print("stop send failed:", e, file=sys.stderr)
        time.sleep(0.02)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["beep", "wheel", "forward"])
    ap.add_argument("motor", nargs="?", type=int, choices=[1, 2, 3, 4])
    ap.add_argument("--duty", type=float, default=35)
    ap.add_argument("--time", type=float, default=0.4)
    ap.add_argument("--i2c", action="store_true")
    ap.add_argument("--port", default="/dev/ttyAMA0")
    a = ap.parse_args()
    duty = max(0.0, min(a.duty, MAX_DUTY))
    dur = max(0.05, min(a.time, MAX_TIME))
    if a.cmd == "wheel" and a.motor is None:
        ap.error("wheel needs a motor number 1..4")
    board = I2cBoard() if a.i2c else UartBoard(a.port)
    def bail(sig, _frm):
        raise KeyboardInterrupt
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, bail)
    if a.cmd == "beep":
        board.beep()
        print("beep sent. If you heard it, the link works.")
        return
    if a.cmd == "wheel":
        cmd = {a.motor: FORWARD_SIGN[a.motor] * duty}
    else:
        cmd = {m: FORWARD_SIGN[m] * duty for m in (1, 2, 3, 4)}
    print(f"{a.cmd}: {cmd} for {dur:.2f} s")
    try:
        board.motors(cmd)
        time.sleep(dur)
    finally:
        stop(board)
        print("stopped")

if __name__ == "__main__":
    main()
