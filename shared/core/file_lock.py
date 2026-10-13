# -*- coding: utf-8 -*-
"""프로세스(스레드) + 파일(fcntl) 이중 잠금 유틸 — JSON 설정 read-modify-write 직렬화용.

· 같은 프로세스: 경로별 RLock (재진입 허용)
· 다른 프로세스: 임시 디렉터리의 해시명 lock 파일에 fcntl.flock (가용한 플랫폼에서만, 실패 시 스레드 락만 사용)
"""
import functools
import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager

try:
    import fcntl  # type: ignore
except ImportError:  # Windows
    fcntl = None

_registry_guard = threading.Lock()
_locks = {}  # abs path -> [RLock, depth]


@contextmanager
def file_locked(path: str):
    key = os.path.abspath(path)
    with _registry_guard:
        entry = _locks.setdefault(key, [threading.RLock(), 0])
    entry[0].acquire()
    fh = None
    try:
        if entry[1] == 0 and fcntl is not None:
            try:
                lock_dir = os.path.join(tempfile.gettempdir(), "ctrade_locks")
                os.makedirs(lock_dir, exist_ok=True)
                fh = open(os.path.join(lock_dir, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".lock"), "a")
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                if fh is not None:
                    fh.close()
                fh = None
        entry[1] += 1
        try:
            yield
        finally:
            entry[1] -= 1
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()
        entry[0].release()


def with_file_lock(path: str):
    """함수 전체를 file_locked(path) 안에서 실행하는 데코레이터"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with file_locked(path):
                return fn(*a, **kw)
        return wrapper
    return deco
