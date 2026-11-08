"""Confine TinyRadTool's legacy SIGINT hook to main-thread device construction.
Only the vendor Connection module receives this facade; process-wide signal.signal is unchanged.
"""
import threading

class MainThreadSignals:
    def __init__(self, original):self.original=original
    def __getattr__(self,name):return getattr(self.original,name)
    def signal(self,signum,handler):
        if threading.current_thread() is threading.main_thread():return self.original.signal(signum,handler)
        return self.original.getsignal(signum)

def guard_vendor_signals(connection_module):
    if not isinstance(connection_module.signal,MainThreadSignals):
        connection_module.signal=MainThreadSignals(connection_module.signal)
