# -*- coding: utf-8 -*-
"""종목 마스터 원장 전체 보기 — 원장 + 위성 테이블(수집 대장/참조 관계/CTrade 설정)을 한 표로 합친다 (읽기 전용).

화면(Raw Store)이 "원장 안의 모든 종목이 지금 무엇으로 쓰이는지"를 한눈에 보게 하고, 정의에서 어긋난 항목
(이름 미확인, 사용처 미확인)을 눈에 띄게 한다. 이 모듈은 아무것도 쓰지 않는다.

상태(state)
  collecting  : 수집 대장(source_mappings)에 있음 — Raw Store 표에 보이는 종목
  registered  : 원장에는 등록됐지만 수집하지 않음 (CTrade 매매/피처 로직 등 다른 용도)
  catalog     : 아직 시스템에 등록되지 않은 알려진 종목(검색 후보)
"""
import json
import os

from shared.core import stream_keys as sk

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRADING_CONFIG_PATH = os.path.join(REPO_ROOT, "CTrade", "config", "trading_config.json")

NAME_SOURCE_LABELS = {"UPBIT": "업비트", "KIS": "KIS", "CREON": "CREON", "manual": "직접 입력", "unresolved": "미확인"}


def _ctrade_usages() -> dict:
    """CTrade 매매 설정에서 쓰는 심볼 -> 사용처 목록 (스캘핑 대상, 선후행 풀, 상시 구독 스트림)"""
    usages = {}
    try:
        with open(TRADING_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return usages

    def add(sym, where):
        if sym:
            usages.setdefault(str(sym).strip().upper(), []).append(where)

    targets = cfg.get("SCALPING_TARGETS")
    for t in (targets.values() if isinstance(targets, dict) else targets or []):
        if isinstance(t, dict):
            add(t.get("symbol"), "CTrade 스캘핑 대상")
            add(t.get("reference_symbol"), "CTrade 연동 참조")
    for item in cfg.get("GLOBAL_LEAD_LAG_POOL", []) or []:
        if isinstance(item, dict):
            add(item.get("symbol"), "CTrade 선후행 풀")
    for key in cfg.get("GLOBAL_BINANCE_ALWAYS_ON", []) or []:
        add(sk.to_ledger_key(key), "CTrade 상시 구독")
    return usages


def build_ledger_overview() -> dict:
    from shared.config.config_manager import load_symbols_registry
    from shared.data_library.source_mapping_manager import SourceMappingManager
    from shared.data_library.symbol_relations_manager import SymbolRelationsManager

    ledger = load_symbols_registry(include_catalog=True)
    mappings = SourceMappingManager.load_all_mappings()

    used_by = {}   # 심볼 -> ["참조 관계: 타겟 069500 의 참조", ...]
    for target, info in SymbolRelationsManager.load_all_relations().items():
        used_by.setdefault(str(target).upper(), []).append("참조 관계: 타겟")
        for ref in info.get("references", []):
            rsym = str(ref.get("symbol", "")).strip().upper()
            if rsym:
                used_by.setdefault(rsym, []).append(f"참조 관계: {target} 의 참조")
    ctrade = _ctrade_usages()

    items = []
    for sym, e in ledger.items():
        collecting = sym in mappings
        catalog = bool(e.get("catalog"))
        state = "catalog" if catalog else ("collecting" if collecting else "registered")
        usage = []
        if collecting:
            usage.append("수집 대장")
        usage += used_by.get(sym, [])
        usage += ctrade.get(sym, [])

        flags = []
        if not catalog:
            if e.get("name_source") == "unresolved":
                flags.append("name_unresolved")
            if not usage:
                flags.append("no_usage")

        items.append({
            "symbol": sym,
            "name": e.get("name", sym),
            "name_source": e.get("name_source"),
            "name_source_label": NAME_SOURCE_LABELS.get(e.get("name_source"), "기록 없음"),
            "market_type": e.get("market_type"),
            "exchange": e.get("exchange"),
            "asset_id": e.get("asset_id"),
            "tradable": bool(e.get("tradable", True)),
            "state": state,
            "usage": usage,
            "flags": flags,
        })

    order = {"collecting": 0, "registered": 1, "catalog": 2}
    items.sort(key=lambda x: (order[x["state"]], x["asset_id"] or x["symbol"], x["symbol"]))
    summary = {
        "ledger_total": len(items),
        "collecting": sum(1 for i in items if i["state"] == "collecting"),
        "registered": sum(1 for i in items if i["state"] == "registered"),
        "catalog": sum(1 for i in items if i["state"] == "catalog"),
        "reference_only": sum(1 for i in items if i["state"] != "catalog" and not i["tradable"]),
        "name_unresolved": sum(1 for i in items if "name_unresolved" in i["flags"]),
        "no_usage": sum(1 for i in items if "no_usage" in i["flags"]),
    }
    return {"summary": summary, "items": items}
