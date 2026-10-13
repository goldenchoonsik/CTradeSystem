# -*- coding: utf-8 -*-
"""
종목명 원천 조회기 (Symbol Name Resolver)
========================================
종목 이름을 코드에 손으로 박아 넣는 대신, 그 종목의 **데이터 원천/실시간 접속처가 실제로 쓰는 이름**을
찾아서 종목 마스터 원장에 넣는다.

- 공급자(provider)는 ``fn(symbol, market_type) -> Optional[str]`` 형태의 함수이며 이름을 모르면 None 을 돌려준다.
- 공급자는 등록 순서대로 시도하고, 처음으로 이름을 돌려준 공급자의 이름/출처를 채택한다.
- 조회 실패/예외는 절대 전파하지 않는다 (None). 이름을 지어내지 않는다.
- 기본 공급자: 업비트 마켓 목록(공개 API). 증권사 공급자(KIS/CREON)는 해당 접속 정보를 가진
  CAiModelBaker 대시보드가 기동 시 ``register_provider`` 로 등록한다.
"""

import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("shared.symbol_name_resolver")

Provider = Callable[[str, str], Optional[str]]

_PROVIDERS: List[Tuple[str, Provider]] = []
_LOCK = threading.Lock()

_UPBIT_MARKETS_URL = "https://api.upbit.com/v1/market/all?isDetails=false"
_UPBIT_TTL_SEC = 3600
_upbit_cache: Dict[str, object] = {"at": 0.0, "names": {}}


def register_provider(source: str, fn: Provider, *, replace: bool = True) -> None:
    """이름 공급자 등록. 같은 source 이름이 이미 있으면 교체한다(대시보드 재기동/재import 대비)."""
    with _LOCK:
        if replace:
            for i, (name, _) in enumerate(_PROVIDERS):
                if name == source:
                    _PROVIDERS[i] = (source, fn)
                    return
        _PROVIDERS.append((source, fn))


def registered_sources() -> List[str]:
    with _LOCK:
        return [name for name, _ in _PROVIDERS]


def _upbit_provider_real(symbol: str, market_type: str = "") -> Optional[str]:
    """업비트 KRW 마켓 종목의 한글명 (공개 API, 인증 불필요)."""
    if not symbol.startswith("KRW-"):
        return None
    now = time.time()
    if now - float(_upbit_cache["at"]) > _UPBIT_TTL_SEC or not _upbit_cache["names"]:
        import requests
        res = requests.get(_UPBIT_MARKETS_URL, timeout=5)
        res.raise_for_status()
        _upbit_cache["names"] = {
            str(m.get("market", "")).upper(): m.get("korean_name") or ""
            for m in res.json() if m.get("market")
        }
        _upbit_cache["at"] = now
    return _upbit_cache["names"].get(symbol) or None


_upbit_provider = _upbit_provider_real  # 테스트가 네트워크를 막을 때 이 이름만 교체한다


def resolve_symbol_name(symbol: str, market_type: str = "") -> Optional[Dict[str, str]]:
    """{"name": ..., "source": ...} 또는 None. 어떤 공급자도 모르면 None (이름을 지어내지 않음)."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    with _LOCK:
        providers = [("UPBIT", _upbit_provider)] + list(_PROVIDERS)
    for source, fn in providers:
        try:
            name = fn(sym, market_type or "")
        except Exception as e:
            logger.info(f"[NameResolver] {source} 조회 실패({sym}): {e}")
            continue
        name = str(name or "").strip()
        if name:
            return {"name": name, "source": source}
    return None


def audit_symbol_names(registry: Dict[str, dict]) -> Dict[str, list]:
    """등록된 종목 이름을 원천 조회 결과와 대조한다 (보고 전용 — 이름을 바꾸지 않는다).

    Returns: {"match": [...], "mismatch": [{symbol, ledger, source_name, source}], "unverifiable": [...]}
    """
    out: Dict[str, list] = {"match": [], "mismatch": [], "unverifiable": []}
    for sym, info in registry.items():
        found = resolve_symbol_name(sym, info.get("market_type", ""))
        if not found:
            out["unverifiable"].append(sym)
        elif found["name"].strip() == str(info.get("name", "")).strip():
            out["match"].append(sym)
        else:
            out["mismatch"].append({"symbol": sym, "ledger": info.get("name"),
                                    "source_name": found["name"], "source": found["source"]})
    return out
