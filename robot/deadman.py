"""Independent deadman for the chassis (runs as a child process).

The board keeps the last motor duty forever, so if the chassis process is
killed (SIGKILL, segfault) or its control thread stalls, nothing would stop
the wheels. This process:
  * reads a heartbeat byte from stdin, written by the chassis control loop
    every tick (~20 ms);
  * sends STOP if no heartbeat arrives for TIMEOUT s (repeats while silent);
  * sends STOP and exits when the parent dies, stdin closes, or on SIGTERM.
It opens its own handle to the board UART and only ever writes STOP frames.

  python -m robot.deadman <parent_pid> <port>
"""
import os, select, signal, sys, time

from robot.board import STOP_FRAME, open_port

TIMEOUT = 0.3


def log(msg):
    print(f"[deadman {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}] {msg}",
          file=sys.stderr, flush=True)


def main():
    parent, port = int(sys.argv[1]), sys.argv[2]
    ser = open_port(port)

    def stop(reason):
        for _ in range(3):
            try:
                ser.write(STOP_FRAME)
                ser.flush()
            except Exception as e:
                log(f"stop write failed: {e}")
            time.sleep(0.01)
        log(f"STOP sent: {reason}")

    def on_term(*_):
        stop("SIGTERM")
        os._exit(0)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, signal.SIG_IGN)   # parent's Ctrl-C is handled by the parent
    fd = sys.stdin.fileno()
    last_beat = time.monotonic()
    last_silent_stop = 0.0
    while True:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            data = os.read(fd, 4096)
            if not data:
                stop("parent closed the heartbeat pipe")
                return
            last_beat, last_silent_stop = time.monotonic(), 0.0
        if os.getppid() != parent:
            stop("parent process died")
            return
        if time.monotonic() - last_beat > TIMEOUT and time.monotonic() - last_silent_stop > 0.10:
            stop(f"no heartbeat for {TIMEOUT * 1000:.0f} ms")
            last_silent_stop = time.monotonic()  # Repeat zero while the controller remains silent.


if __name__ == "__main__":
    main()
