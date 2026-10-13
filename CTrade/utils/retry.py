import asyncio
import json
import logging
import os
import tempfile
from typing import Callable, Any, Tuple, Type


async def retry_async(
    coro_func: Callable,
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    logger_name: str = "retry_async",
    **kwargs,
) -> Any:
    """
    비동기 코루틴 함수를 Exponential Backoff로 재시도합니다.

    Args:
        coro_func: 재시도할 async 함수
        *args: 함수 인자
        max_retries: 최대 재시도 횟수 (기본 3)
        base_delay: 첫 번째 재시도 대기 시간(초). 이후 2배씩 증가 (기본 1.0s)
        exceptions: 재시도를 트리거할 예외 타입 튜플
        logger_name: 로그 접두사
        **kwargs: 함수 키워드 인자

    Returns:
        성공 시 함수의 반환값, 모든 재시도 실패 시 None
    """
    delay = base_delay
    for attempt in range(1, max_retries + 2):  # 1회 원본 + max_retries 재시도
        try:
            return await coro_func(*args, **kwargs)
        except exceptions as e:
            if attempt > max_retries:
                logging.error(
                    f"[{logger_name}] 모든 재시도 소진 ({max_retries}회). 마지막 오류: {type(e).__name__} - {e}"
                )
                return None
            logging.warning(
                f"[{logger_name}] {attempt}회 시도 실패 ({type(e).__name__}). "
                f"{delay:.1f}초 후 재시도... (최대 {max_retries}회)"
            )
            await asyncio.sleep(delay)
            delay *= 2


def atomic_write_json(file_path: str, data: Any) -> None:
    """
    JSON 데이터를 원자적(Atomic)으로 파일에 씁니다.

    직접 덮어쓰기 대신 임시 파일에 먼저 쓴 뒤 os.replace()로 교체하여,
    읽기/쓰기 Race Condition으로 인한 빈 파일 파싱 오류를 방지합니다.
    (os.replace는 POSIX 시스템에서 원자적으로 동작합니다)

    Args:
        file_path: 최종 저장 경로
        data: JSON 직렬화 가능한 데이터
    """
    dir_name = os.path.dirname(file_path)
    try:
        # 같은 디렉터리에 임시 파일 생성 (교체 시 같은 파일시스템 파티션 보장)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=dir_name,
            suffix=".tmp",
            delete=False,
        ) as tmp_f:
            tmp_path = tmp_f.name
            json.dump(data, tmp_f, indent=4, ensure_ascii=False)

        # 임시 파일 → 최종 경로로 원자적 교체
        os.replace(tmp_path, file_path)

    except Exception as e:
        logging.error(f"[atomic_write_json] Failed to write {file_path}: {e}")
        # 임시 파일이 남아있으면 정리
        try:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise
