# -*- coding: utf-8 -*-
"""Creon 1분봉 원천 읽기 창구 — 소비자는 이 모듈만 거치고, 1분봉이 디스크에 어떻게 놓였는지 알 필요가 없다.

레이아웃(일자별 저장소, CAiModelBaker/core/candle_day_store.py 가 쓴다):
  raw/creon/candle_1m/A{sym}/YYYY-MM-DD.csv  + .meta.json (+ 충돌이 있으면 .csv.conflicts.json)
  선물·지수도 같은 방식이다 — 원장 키가 종목 코드 자리에 온다(10100 → A10100/, U180 → AU180/).
날짜 상태(meta status): OPEN(병합 중) · VALID / ACCEPTED_DEVIATION(봉인) · QUARANTINED(봉인 검사 불합격) · LOST(영구 결손, 파일 없음)

경로로 원천을 다루는 소비자는 종전처럼 `raw/creon/candle_1m/A005930_1m.csv` · `10100_1m.csv` 를 조립해 두고 path_has_data / read_csv 로 묻는다
(그 파일은 디스크에 없다 — 창구가 종목으로 바꿔 일자 저장소를 읽는다). 구현은 day_store_reader.DayStoreReader(틱 tick_store 와 공유).
"""
import os
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from shared.data_library.day_store_reader import DayStoreReader, core_symbol as _core
from shared.data_library.paths import data_vault_path

SEALED = ("VALID", "ACCEPTED_DEVIATION")


def candle_root() -> str:
    return data_vault_path("raw", "creon", "candle_1m")


_R = DayStoreReader(lambda: candle_root(), "_1m.csv", "volume")


def day_dir(symbol: str) -> str:
    return _R.day_dir(symbol)


def symbol_from_path(path: Optional[str]) -> Optional[str]:
    """논리 경로 `.../creon/candle_1m/<이름>_1m.csv` 이면 종목(원장 키), 아니면 None"""
    return _R.symbol_from_path(path)


def has_data(symbol: str) -> bool:
    return _R.has_data(symbol)


def path_has_data(path: Optional[str]) -> bool:
    """경로로 존재를 묻는 소비자용 — 1분봉 논리 경로면 창구가 답하고, 그 밖의 경로는 파일 존재·크기로 답한다."""
    return _R.path_has_data(path)


def is_day_layout(symbol: str) -> bool:
    """이 종목의 1분봉이 일자 저장소(A{sym}/ 폴더)에 있는가 — 있으면 같은 이름의 통합 CSV 보다 우선한다."""
    return _R.has_data(symbol)


def path_exists(path: Optional[str]) -> bool:
    """레이아웃 전환기용 존재 질의 — 일자 저장소에 있거나(논리 경로), 통합 CSV 가 실제로 있으면 True.
    (path_has_data 는 논리 경로에 대해 일자 저장소만 본다. 아직 통합 CSV 인 종목도 놓치지 않도록 소비자는 이쪽을 쓴다.)"""
    if _R.path_has_data(path):
        return True
    return bool(path and os.path.exists(path) and os.path.getsize(path) > 0)


def days(symbol: str) -> Dict[str, Dict[str, Any]]:
    """{날짜: {status, rows, volume, sha256, first, last, ratio, reason}} — 날짜 오름차순(LOST 포함)."""
    return _R.days(symbol)


def dates(symbol: str, include_lost: bool = False) -> set:
    """데이터가 실제로 있는 날짜 집합"""
    return _R.dates(symbol, include_lost)


def last_timestamp(symbol: str) -> Optional[str]:
    return _R.last_timestamp(symbol)


def pending_conflicts(symbol: str) -> List[Dict[str, Any]]:
    return _R.pending_conflicts(symbol)


def signature(symbol: str) -> str:
    """캐시 키 — 일자 폴더 안 파일들의 (이름, 크기, mtime) 해시."""
    return _R.signature(symbol)


def read(symbol: str, start: Optional[str] = None, end: Optional[str] = None,
         columns: Optional[Iterable[str]] = None) -> pd.DataFrame:
    """1분봉을 DataFrame 으로. start/end 는 'YYYY-MM-DD'(양 끝 포함). 날짜 오름차순."""
    return _R.read(symbol, start, end, columns)


def read_csv(path: str, **kw: Any) -> pd.DataFrame:
    """`pd.read_csv(path, ...)` 의 대체 — 논리 경로면 일자 저장소 전체를, 실제 파일이면 그 파일을 읽는다."""
    return _R.read_csv(path, **kw)


def volumes(symbol: str, sealed_only: bool = True) -> Dict[str, float]:
    """{날짜: 1분봉 거래량 합} — 틱 봉인의 기준값. 기본은 봉인된 날만(봉인 전 값은 기준으로 삼지 않는다)."""
    return {d: e["volume"] for d, e in _R.days(symbol).items()
            if e["volume"] is not None and e["status"] != "LOST" and (not sealed_only or e["status"] in SEALED)}
