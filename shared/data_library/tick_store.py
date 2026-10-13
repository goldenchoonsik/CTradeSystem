# -*- coding: utf-8 -*-
"""틱 원천(Creon) 읽기 창구 — 소비자는 이 모듈만 거치고, 틱이 디스크에 어떻게 놓였는지 알 필요가 없다.

레이아웃(일자별 저장소, CAiModelBaker/core/tick_day_store.py 가 쓴다):
  raw/creon/tick/A{sym}/YYYY-MM-DD.csv  + .meta.json (+ 충돌이 있으면 .csv.conflicts.json)
날짜 상태(meta status): OPEN(병합 중) · VALID / ACCEPTED_DEVIATION(봉인) · QUARANTINED(1분봉과 불일치) · LOST(영구 결손, 파일 없음)

경로로 원천을 다루는 소비자(헬스 매트릭스·품질 감사·원천 탐색)를 위해 종목마다 '논리 경로' `raw/creon/tick/A{sym}_tick.csv` 를 둔다.
디스크에 그 파일은 없다 — symbol_from_path / path_has_data 가 그 경로를 종목으로 바꿔 이 창구로 연결한다.
구현은 day_store_reader.DayStoreReader(1분봉 candle_store 와 공유) — 여기는 틱의 루트·접미·거래량 키만 정한다.
"""
import os
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from shared.data_library.day_store_reader import DayStoreReader, core_symbol as _core
from shared.data_library.paths import data_vault_path

SEALED = ("VALID", "ACCEPTED_DEVIATION")


def tick_root() -> str:
    return data_vault_path("raw", "creon", "tick")


_R = DayStoreReader(lambda: tick_root(), "_tick.csv", "tick_volume")     # tick_root 를 호출 때 조회 — 테스트가 바꿔 끼울 수 있다


def logical_path(symbol: str) -> str:
    """경로 기반 소비자용 논리 경로(파일은 없다)"""
    return os.path.join(tick_root(), f"A{_core(symbol)}_tick.csv")


def day_dir(symbol: str) -> str:
    return _R.day_dir(symbol)


def _day_metas(symbol: str) -> Dict[str, Dict[str, Any]]:
    return _R.day_metas(symbol)


def symbol_from_path(path: Optional[str]) -> Optional[str]:
    """논리 경로 `.../creon/tick/A{sym}_tick.csv` 이면 종목 코드, 아니면 None"""
    return _R.symbol_from_path(path)


def has_data(symbol: str) -> bool:
    return _R.has_data(symbol)


def path_has_data(path: Optional[str]) -> bool:
    """경로로 존재를 묻는 소비자용 — 틱 논리 경로면 창구가 답하고, 그 밖의 경로는 파일 존재·크기로 답한다."""
    return _R.path_has_data(path)


def days(symbol: str) -> Dict[str, Dict[str, Any]]:
    """{날짜: {status, rows, volume, sha256, first, last, ratio, reason}} — 날짜 오름차순(LOST 포함)."""
    return _R.days(symbol)


def dates(symbol: str, include_lost: bool = False) -> set:
    """데이터가 실제로 있는 날짜 집합"""
    return _R.dates(symbol, include_lost)


def last_timestamp(symbol: str) -> Optional[str]:
    return _R.last_timestamp(symbol)


def pending_conflicts(symbol: str) -> List[Dict[str, Any]]:
    """'확인 필요' 항목(YYYY-MM-DD.csv.conflicts.json, status=pending)"""
    return _R.pending_conflicts(symbol)


def signature(symbol: str) -> str:
    """캐시 키 — 일자 폴더 안 파일들의 (이름, 크기, mtime) 해시."""
    return _R.signature(symbol)


def read(symbol: str, start: Optional[str] = None, end: Optional[str] = None, columns: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """틱 데이터를 DataFrame 으로. start/end 는 'YYYY-MM-DD'(양 끝 포함). 날짜 오름차순, 날 안은 파일(체결) 순서."""
    return _R.read(symbol, start, end, columns)
