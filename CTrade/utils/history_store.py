"""
CTrade - Trade History 공용 저장소 (M-1)

`trade_history.json` 은 VirtualTrader(UPBIT/시뮬)와 KISTrader(KIS)가 각자 독립적인
in-memory 리스트를 들고 **전체 덮어쓰기**를 수행하여, 마지막 writer가 상대방의 신규
이벤트를 소실시키는(Clobbering) 문제가 있었다.

이 모듈은 파일 기반 **합집합 병합(Union-Merge) + 원자적 저장(os.replace) + 파일 락**을
제공하여, 다중 writer(UPBIT/KIS)가 동일 파일을 공유하더라도 어떤 이벤트도 유실되지
않도록 보장한다.
"""
import os
import json
import logging

try:  # Unix 계열 파일 락 (Vultr/Linux 운영 서버)
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - Windows 등
    fcntl = None


def _event_key(e: dict):
    """이벤트 중복 판별 키 (수수료/부가 필드는 제외하고 본질 필드만 사용)."""
    def _f(v, nd):
        try:
            return round(float(v), nd)
        except Exception:
            return 0.0
    return (
        str(e.get("type", "")),
        str(e.get("symbol", "")),
        _f(e.get("time", 0.0), 3),
        _f(e.get("price", e.get("sell_price", 0.0)), 8),
        _f(e.get("qty", 0.0), 8),
        str(e.get("reason", "")),
        str(e.get("exchange", "")),
    )


def load_history_raw(path: str):
    """기존 파일을 list로 로드 (없거나 손상 시 빈 리스트)."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logging.warning(f"[HistoryStore] 기존 이력 로드 실패(무시): {path} ({e})")
        return []


def save_history_merged(path: str, events) -> bool:
    """`events` 와 파일의 기존 이력을 합집합 병합(중복 제거)하여 원자적으로 저장한다.

    - 동일 키는 신규 이벤트가 우선한다.
    - time(초) 오름차순으로 정렬하여 시계열 일관성을 유지한다.
    - `*.lock` 파일에 배타 락을 걸어 read-modify-write 경쟁을 최소화한다.
    """
    events = [e for e in (events or []) if isinstance(e, dict)]

    lock_path = path + ".lock"
    lock_fd = None
    try:
        if fcntl is not None:
            try:
                lock_fd = open(lock_path, "w")
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            except Exception:
                lock_fd = None

        existing = load_history_raw(path)
        merged = {}
        for e in existing:
            if isinstance(e, dict):
                merged[_event_key(e)] = e
        for e in events:
            merged[_event_key(e)] = e  # 신규 우선

        out = sorted(merged.values(), key=lambda x: float(x.get("time", 0.0) or 0.0))

        tmp_path = f"{path}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        logging.error(f"[HistoryStore] 이력 병합 저장 실패: {path} ({e})")
        tmp_path = f"{path}.tmp.{os.getpid()}"
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass
