"""Prevent two bot runtimes from owning the same durable queue."""
import fcntl
import os
from pathlib import Path


class RuntimeLock:
    def __init__(self, database):
        self.path = None if str(database) == ':memory:' else Path(str(Path(database).resolve()) + '.runtime.lock')
        self.fd = None

    def acquire(self):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RuntimeError('Another Vincent runtime owns this database') from None
        self.fd = fd

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
