# -*- coding: utf-8 -*-
"""
CTradeSystem - Live Feed Contract (학습 피처 ↔ 실시간 공급원 계약)
====================================================================
[T-6] 배경
----------
백테스트는 과거 파일에서 어떤 참조든 읽어 올 수 있지만, 실전은 그 참조를 **실시간으로
받을 수 있어야만** 같은 피처를 만들 수 있다. 이 둘이 어긋나면 백테스트에서만 좋은 모델이
배포되고, 실전에서는 그 피처가 0/직전값으로 굳은 채 매매한다 — 조용히, 오류 없이.

실측 (2026-09-28)
------------------
  연구 중이던 KODEX200_S10_NEW1 의 상위 피처 4~6개가 US_TECH_100 / US_500(cTrader CFD)
  기반이었는데, CTrade 에는 cTrader 커넥터가 없다. 수집처 원장은 라이브 연결을 선언하고
  있었지만 **런타임에 그 선언을 이행할 구현이 없었다.**

설계 원칙
---------
- 실전이 지원하는 수집처 목록을 코드에 적지 않는다. CTrade 의 커넥터/파서 모듈을 **탐색**해서
  유도한다. 새 커넥터를 추가하면 게이트가 저절로 열리고, 지우면 저절로 닫힌다.
- 판정 불가는 통과가 아니다. 참조 피처인데 종목/수집처/연결 선언을 못 찾으면 불합격이다.
"""

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_MODULE_DIR))

# 실전 런타임에서 수집처 구현을 찾을 위치 (모듈 이름 규칙으로 수집처를 유도한다)
_LIVE_IMPL_DIRS = (
    os.path.join("CTrade", "connectors"),
    os.path.join("CTrade", "parsers"),
)
_IMPL_SUFFIX_RE = re.compile(r"_(connector|parser|client|feeder|feed)$")


class LiveFeedContractError(RuntimeError):
    """학습에 쓰인 피처를 실전에서 만들 수 없을 때 발생."""

    def __init__(self, findings: Sequence["FeatureLiveSupport"]):
        self.findings = list(findings)
        super().__init__(
            "학습 피처를 실전에서 공급할 수 없습니다 — 배포를 중단합니다:\n"
            + "\n".join(f"  · {f.describe()}" for f in self.findings)
        )


@dataclass(frozen=True)
class FeatureLiveSupport:
    feature_id: str
    symbol: Optional[str]
    declared_provider: Optional[str]
    supported: bool
    reason: str = ""

    def describe(self) -> str:
        if self.supported:
            return f"{self.feature_id}: 공급 가능 ({self.symbol} / {self.declared_provider})"
        return f"{self.feature_id}: 공급 불가 — {self.reason}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "symbol": self.symbol,
            "declared_provider": self.declared_provider,
            "supported": self.supported,
            "reason": self.reason,
        }


def implemented_live_providers(project_root: str = _PROJECT_ROOT) -> set:
    """실전 런타임이 실제로 구현한 수집처 집합 (모듈 탐색으로 유도, 하드코딩 없음).

    모듈 이름이 수집처보다 구체적인 경우가 있어(`yahoo_futures_feeder` 가 원장의 `YAHOO` 를
    담당한다) 각 이름의 밑줄 접두어도 함께 넣는다:
        yahoo_futures_feeder    → {YAHOO_FUTURES, YAHOO}
        binance_futures_radar   → {BINANCE_FUTURES_RADAR, BINANCE_FUTURES, BINANCE}
    """
    found = set()
    for rel in _LIVE_IMPL_DIRS:
        d = os.path.join(project_root, rel)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".py") or fn.startswith("__") or fn.startswith("base_"):
                continue
            stem = _IMPL_SUFFIX_RE.sub("", fn[:-3]).upper()
            if not stem:
                continue
            parts = stem.split("_")
            for i in range(1, len(parts) + 1):
                found.add("_".join(parts[:i]))
    return found


def _live_provider_of(symbol: str) -> Optional[str]:
    """종목의 실시간 연결 수집처 (수집처 원장의 live_connection 선언). 선언이 없으면 None."""
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        maps = SourceMappingManager.load_all_mappings()
        clean = SourceMappingManager.clean_symbol(symbol)
        entry = maps.get(symbol) or maps.get(clean)
        if entry is None:
            for k, v in maps.items():
                if SourceMappingManager.clean_symbol(k) == clean:
                    entry = v
                    break
        if not entry:
            return None
        lc = entry.get("live_connection") or {}
        if not lc or lc.get("is_active") is False:
            return None
        return str(lc.get("provider", "")).strip().upper() or None
    except Exception:
        return None


def audit_live_feature_support(feature_ids: Iterable[str]) -> List[FeatureLiveSupport]:
    """피처 목록 중 참조 종목에 의존하는 것들이 실전에서 공급 가능한지 감사한다.

    타겟 자신의 피처는 매매 대상 종목이므로 항상 공급 가능하다고 본다.
    """
    from shared.config.config_manager import symbol_from_ref_key
    from shared.features.feature_registry import parse_reference_feature_id

    impl = implemented_live_providers()
    out: List[FeatureLiveSupport] = []
    for fid in feature_ids or []:
        parsed = parse_reference_feature_id(str(fid))
        if not parsed:
            continue  # 타겟 자체 피처 — 매매 대상이므로 공급원이 곧 타겟이다
        key = parsed[0]
        symbol = symbol_from_ref_key(key)
        if not symbol:
            out.append(FeatureLiveSupport(fid, None, None, False,
                                          f"참조 키 '{key}' 가 종목 원장에 없습니다"))
            continue
        provider = _live_provider_of(symbol)
        if not provider:
            out.append(FeatureLiveSupport(fid, symbol, None, False,
                                          f"{symbol} 에 활성 실시간 연결(live_connection) 선언이 없습니다"))
            continue
        if provider not in impl:
            out.append(FeatureLiveSupport(
                fid, symbol, provider, False,
                f"{symbol} 은 {provider} 실시간 연결을 선언했지만 실전 런타임에 {provider} 구현이 없습니다 "
                f"(구현된 수집처: {', '.join(sorted(impl)) or '없음'})"))
            continue
        out.append(FeatureLiveSupport(fid, symbol, provider, True))
    return out


def assert_live_feature_support(feature_ids: Iterable[str], log_fn=None) -> List[FeatureLiveSupport]:
    """공급 불가 피처가 하나라도 있으면 ``LiveFeedContractError`` 를 던진다 (fail-closed)."""
    findings = audit_live_feature_support(feature_ids)
    if log_fn:
        for f in findings:
            log_fn(f"  {'✅' if f.supported else '🚨'} [공급 계약] {f.describe()}")
    unsupported = [f for f in findings if not f.supported]
    if unsupported:
        raise LiveFeedContractError(unsupported)
    return findings
