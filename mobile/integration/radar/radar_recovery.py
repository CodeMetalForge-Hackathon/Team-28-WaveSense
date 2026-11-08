"""Back off failed captures, not just failed USB opens."""


class RecoveryBackoff:
    def __init__(self):
        self.failures = 0
        self.good_since = None

    def failed(self):
        self.good_since = None
        delay = (3.0, 10.0, 30.0, 60.0)[min(self.failures, 3)]
        self.failures += 1
        return delay

    def received(self, now):
        if self.good_since is None:
            self.good_since = now
        # Opening USB is insufficient. Require sustained successful acquisition.
        if now - self.good_since >= 2.0:
            self.failures = 0
