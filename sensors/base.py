"""Shared machinery for the streaming sensor modules.

Each sensor runs one background thread that (re)opens its device, reads
continuously and publishes the latest item. Consumers call snapshot(), which
never blocks on the device. A read failure or unplug closes the device and
retries with backoff; while the by-id path is missing the status is "offline".

Status values: starting, connecting, ok, stale (no new data for stale_after s),
reconnecting, offline, stopped.
"""
import collections, json, logging, os, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = logging.getLogger("sensors")


def device_path(name):
    """by-id path for a sensor, from config/devices.json."""
    with open(os.path.join(ROOT, "config", "devices.json")) as f:
        return json.load(f)[name]


def now_ms():
    return int(time.time() * 1000)


class StreamingSensor:
    name = "sensor"
    stale_after = 1.0      # seconds without new data before "ok" reads as "stale"

    def __init__(self, path):
        self.path = path
        self.frames = 0        # total items published
        self.reconnects = 0
        self.errors = 0
        self.last_error = ""
        self._lock = threading.Lock()
        self._data = None
        self._last_seen = None  # capture time, ms since epoch
        self._times = collections.deque(maxlen=128)
        self._status, self._detail = "starting", ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"sensor-{self.name}", daemon=True)

    # ---- public -------------------------------------------------------
    def start(self):
        self._thread.start()
        return self

    def stop(self, timeout=2.0):
        """Ask the thread to stop. Never waits for slow device closes."""
        self._stop.set()
        self._thread.join(timeout)

    def alive(self):
        return self._thread.is_alive()

    def fps(self, window=2.0):
        t = time.monotonic()
        with self._lock:
            ts = [x for x in self._times if t - x <= window]
        return (len(ts) - 1) / (ts[-1] - ts[0]) if len(ts) >= 2 and ts[-1] > ts[0] else 0.0

    def snapshot(self):
        with self._lock:
            data, seen, status, detail = self._data, self._last_seen, self._status, self._detail
        age = None if seen is None else now_ms() - seen
        if status == "ok" and (age is None or age > self.stale_after * 1000):
            status = "stale"
        # the 2 s rate window lags an outage, so report 0 unless frames are flowing now
        fps = round(self.fps(), 1) if status == "ok" else 0.0
        return dict(name=self.name, status=status, detail=detail, fps=fps,
                    last_seen=seen, age_ms=age, frames=self.frames,
                    reconnects=self.reconnects, data=data)

    # ---- for subclasses ----------------------------------------------
    def _open(self):
        raise NotImplementedError

    def _read(self):
        """Block briefly, publish whatever arrived; raise on failure or stall."""
        raise NotImplementedError

    def _close(self):
        pass

    def _publish(self, data, ts_ms=None):
        with self._lock:
            self._data = data
            self._last_seen = ts_ms if ts_ms is not None else now_ms()
            self._times.append(time.monotonic())
            self._status, self._detail = "ok", ""
        self.frames += 1

    def _set_status(self, status, detail=""):
        with self._lock:
            self._status, self._detail = status, detail

    # ---- thread -------------------------------------------------------
    def _run(self):
        backoff = 0.5
        while not self._stop.is_set():
            if not os.path.exists(self.path):
                self._set_status("offline", "device not present")
                self._stop.wait(0.5)
                continue
            try:
                self._set_status("connecting")
                self._open()
                backoff = 0.5
                while not self._stop.is_set():
                    self._read()
            except Exception as e:  # any failure -> close, back off, reopen
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("%s: %s", self.name, self.last_error)
                self._set_status("reconnecting", self.last_error)
            finally:
                try:
                    self._close()
                except Exception as e:
                    log.warning("%s close: %s", self.name, e)
            if self._stop.is_set():
                break
            self.reconnects += 1
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 4.0)
        self._set_status("stopped")
