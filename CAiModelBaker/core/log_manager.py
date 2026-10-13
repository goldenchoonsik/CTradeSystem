"""quant_engine_log.txt 크기 관리.

종전 결함 (실측 2026-09-24): 브라우저가 "로그 저장" 클릭 때마다 세션 전체 로그를 append 하고,
학습 종료 시 그 파일 전체(159MB / 120만 줄)를 세션 reports/train_run.log 로 복사했다.
→ 상한 없는 성장 + 세션 폴더마다 동일 크기 복제.

정책 (CTrade/main.py 의 trade_debug.log 와 동일 규격):
  - 활성 로그: 최대 20MB, 초과 시 .1 → .2 → .3 순으로 회전 (백업 3개, 총 상한 ≈ 80MB)
  - 세션 train_run.log: 활성 로그의 마지막 2MB 만 복사
"""
import os
import threading

QUANT_LOG_MAX_BYTES = 20 * 1024 * 1024
QUANT_LOG_BACKUP_COUNT = 3
TRAIN_RUN_LOG_TAIL_BYTES = 2 * 1024 * 1024

_lock = threading.Lock()


def _rotate(path: str, backup_count: int) -> None:
    oldest = f"{path}.{backup_count}"
    if os.path.exists(oldest):
        os.remove(oldest)
    for i in range(backup_count - 1, 0, -1):
        src = f"{path}.{i}"
        if os.path.exists(src):
            os.replace(src, f"{path}.{i + 1}")
    os.replace(path, f"{path}.1")


def append_with_rotation(path: str, content: str,
                         max_bytes: int = QUANT_LOG_MAX_BYTES,
                         backup_count: int = QUANT_LOG_BACKUP_COUNT) -> int:
    """content 를 path 에 append. 기록 후 max_bytes 를 넘기면 회전. 기록한 바이트 수 반환."""
    data = content.encode("utf-8")
    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "ab") as f:
            f.write(data)
        if os.path.getsize(path) > max_bytes:
            _rotate(path, backup_count)
    return len(data)


def copy_tail(src: str, dst: str, max_bytes: int = TRAIN_RUN_LOG_TAIL_BYTES) -> int:
    """src 의 마지막 max_bytes 만 dst 로 복사 (줄 경계로 정렬). 복사한 바이트 수 반환."""
    size = os.path.getsize(src)
    with open(src, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()  # 잘린 첫 줄 버림
        data = f.read()
    with open(dst, "wb") as f:
        if size > max_bytes:
            f.write(f"[... 앞부분 {size - len(data):,} bytes 생략 — 마지막 {len(data):,} bytes 만 보존 ...]\n".encode("utf-8"))
        f.write(data)
    return len(data)
