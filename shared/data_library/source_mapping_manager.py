# -*- coding: utf-8 -*-
"""
CTradeSystem - Source Mapping Manager (원천 데이터 출처 매핑 및 자산 대장 매니저)
=============================================================================
1. shared/config/source_mappings.json 선언적 리소스의 원자적 관리
2. 논리 자산 ➔ 주 제공처(Primary Provider) 및 보조 제공처(Fallback Provider) 매핑
3. 자산별 원천 파일, 1:1 사이드카 매니페스트, 최근 수집 시점(Recency), 누적 일수 실시간 감사
4. 신규 자산 등록 및 활성(Active)/비활성(Paused) 구독 상태 동적 관리
"""

import os
import json
import shutil
import tempfile
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

from shared.data_library.paths import (
    data_vault_path, raw_path, raw_root, data_path, DATA_VAULT_ROOT, DATA_ROOT,
    data_vault_config_path
)
from shared.data_library.manifest_manager import ManifestManager
from shared.core.file_lock import file_locked, with_file_lock

logger = logging.getLogger(__name__)

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_LEGACY_MAPPINGS_PATH = os.path.join(os.path.dirname(_MODULE_DIR), "config", "source_mappings.json")
_VAULT_MAPPINGS_PATH = data_vault_config_path("source_mappings.json")

# DataVault/config/ 우선, 없으면 레거시 shared/config/ 폴백 (기본값: DataVault/config/)
_MAPPINGS_FILE_PATH = _VAULT_MAPPINGS_PATH if (os.path.exists(_VAULT_MAPPINGS_PATH) or not os.path.exists(_LEGACY_MAPPINGS_PATH)) else _LEGACY_MAPPINGS_PATH
_LOCK = with_file_lock(_MAPPINGS_FILE_PATH)  # read-modify-write 직렬화


class SourceMappingManager:
    """원천 데이터 출처 매핑 및 수집 대상 자산 대장을 전담하는 매니저"""

    # 1단계: 제공처별 실물 API 호출 검증 완료된 원천 수집 가능 스케일 (Raw Capabilities)
    PROVIDER_RAW_CAPABILITIES: Dict[str, List[str]] = {
        "UPBIT": ["TICK", "1S", "1M", "1D"],
        "BINANCE": ["TICK", "1S", "1M", "1D"],
        "COINBASE": ["TICK", "1M", "1D"],
        "CTRADER": ["TICK", "1M", "1D"],
        "CREON": ["TICK", "1M", "1D"],
        "KIS": ["TICK", "1M", "5M", "1D"],
        "CME": ["1M", "5M", "1D"],
    }

    # 1단계: 신규 자산 자동 등록 시 provider별 기본 보조(fallback) 제공처
    # (미매칭 provider는 CME로 떨어지므로, 실사용 중인 provider는 반드시 여기 명시할 것)
    FALLBACK_SOURCE_BY_PROVIDER: Dict[str, str] = {
        "BINANCE": "COINBASE",
        "COINBASE": "BINANCE",
        "UPBIT": "BINANCE",
        "CREON": "KIS",
        "KIS": "YAHOO",
    }

    # 2단계: 실무 퀀트 핵심 6대 표준 주기
    QUANT_CORE_STANDARDS: List[str] = ["10s", "1m", "5m", "15m", "1h", "1d"]

    # 2단계: 실무 퀀트 전체 스펙트럼 캔들 목록
    QUANT_FULL_SPECTRUM: List[str] = [
        "1s", "5s", "10s", "30s",
        "1m", "3m", "5m", "10m", "15m", "30m",
        "1h", "2h", "4h",
        "1d", "1w"
    ]

    _cache: Optional[Dict[str, Any]] = None
    _last_mtime: float = 0.0

    @classmethod
    def clean_symbol(cls, symbol: str) -> str:
        """심볼 앞의 크레온 A 접두사만 정밀 제거 (AAPL, JAPAN_225 등 일반 영문 티커 훼손 방지). 구현은 원장 계층의 단일 정의."""
        from shared.config.config_manager import strip_creon_prefix
        return strip_creon_prefix(symbol)

    @classmethod
    def _canonical_name(cls, symbol: str) -> Optional[str]:
        """종목 마스터 원장(symbols_registry.json) 기준 종목명. 미등록이면 None (이름을 지어내지 않음).

        source_mappings.json 은 name을 자체 보유하는 대신 이 함수로 조회/교정해야 한다.
        """
        from shared.config.config_manager import get_symbol_name
        return get_symbol_name(symbol)

    # 수집 파이프라인상 'macro 시계열'(국내선물·지수·해외선물)로 분류되는 카테고리
    MACRO_CATEGORIES = ("KRX_FUTURES", "KRX_INDEX", "DOMESTIC_FUTURES", "DOMESTIC_INDEX", "OVERSEAS_FUTURES")

    @classmethod
    def macro_series_info(cls, key: str) -> Optional[Tuple[str, str]]:
        """수집 대장에서 macro 시계열로 분류된 종목이면 (원장 심볼 키, 카테고리), 아니면 None.

        key 는 원장 심볼이거나 옛 별칭(FUTURES/spot/nq_futures…). 별칭이 있다고 macro 시계열인 것은 아니다 —
        US_500 같은 CFD 대용은 별칭이 있어도 일반 캔들 파이프라인을 탄다 (분류의 정답은 수집 대장의 category)."""
        from shared.config.config_manager import resolve_macro_symbol
        sym = resolve_macro_symbol(key)
        cat = (cls.load_all_mappings().get(sym) or {}).get("category") if sym else None
        return (sym, cat) if cat in cls.MACRO_CATEGORIES else None

    @staticmethod
    def _ledger_market_type(symbol: str) -> Optional[str]:
        """원장에 등록된 종목의 market_type. 미등록이면 None (이때만 심볼 표기 규칙으로 추정한다)."""
        from shared.config.config_manager import is_symbol_registered, get_symbol_info
        return get_symbol_info(symbol).get("market_type") if is_symbol_registered(symbol) else None

    @staticmethod
    def _is_foreign_listing_of_mapped_asset(symbol: str, mappings: Dict[str, Any]) -> bool:
        """참조 전용 해외 리스팅(예: ETHUSDT, ETH-USD)인데 같은 자산(asset_id)의 거래 대상 리스팅(KRW-ETH)이
        이미 대장에 있으면 True — 파일이 발견됐다고 대장에 별도 자산으로 자동 등재하지 않는다."""
        from shared.config.config_manager import is_symbol_registered, get_symbol_info, get_asset_id, get_asset_listings
        if not is_symbol_registered(symbol) or get_symbol_info(symbol).get("tradable", True):
            return False
        return any(l["symbol"] in mappings and l["symbol"] != symbol and l.get("tradable", True)
                   for l in get_asset_listings(get_asset_id(symbol)))

    @classmethod
    def _ensure_in_ledger(cls, symbol: str, info: Dict[str, Any]) -> None:
        """대장에 있는 심볼이 마스터 원장에 없으면 등록한다 (대장에는 있는데 원장에는 없는 상태 방지)."""
        try:
            from shared.config.config_manager import is_symbol_registered, ensure_symbol_registered
            if is_symbol_registered(symbol):
                return
            role = str(info.get("role", ""))
            ensure_symbol_registered(
                symbol,
                name=str(info.get("name", "")),
                market_type=str(info.get("category", "")) or "OTHER",
                description=str(info.get("description", "")),
                tradable=role in ("target", "target_or_macro"),
            )
            logger.info(f"[SourceMappingManager] 🛡️ 원장 미등록 심볼 자동 등재: {symbol}")
        except Exception as e:
            logger.warning(f"[SourceMappingManager] 원장 등재 실패({symbol}): {e}")

    @classmethod
    def sync_with_symbol_relations(cls, current_mappings: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """
        symbol_relations.json(SSOT)의 모든 타겟 및 참조 종목을 스캔하여
        대장(source_mappings.json)에 누락된 자산을 자동 등록 (Zero-Drift Registry)
        """
        changed = False
        try:
            from shared.data_library.symbol_relations_manager import SymbolRelationsManager
            relations_data = SymbolRelationsManager.load_all_relations()
        except Exception as e:
            logger.warning(f"[SourceMappingManager] symbol_relations 로드 실패: {e}")
            return current_mappings, False
        if not relations_data:
            return current_mappings, False

        # 모든 타겟 및 참조 종목 수집
        symbols_to_verify = {}
        for target_sym, info in relations_data.items():
            clean_tgt = cls.clean_symbol(target_sym)
            symbols_to_verify[clean_tgt] = {
                "name": info.get("name", clean_tgt),
                "category": info.get("market_type", "TARGET"),
                "role": "target",
                "description": info.get("description", "")
            }
            for ref in info.get("references", []):
                r_sym = cls.clean_symbol(ref.get("symbol", ""))
                if r_sym and r_sym not in symbols_to_verify:
                    symbols_to_verify[r_sym] = {
                        "name": ref.get("name", r_sym),
                        "category": ref.get("role", "reference"),
                        "role": ref.get("role", "reference"),
                        "description": ref.get("description", "")
                    }

        for sym, meta in symbols_to_verify.items():
            if sym in current_mappings:
                continue

            # 🛡️ [Anti-Phantom Guard] 모호한 코인 단축코드 및 선물 변형 코드 중복 생성 원천 차단
            from shared.config.config_manager import is_symbol_registered as _is_reg, get_asset_listings as _listings
            if not _is_reg(sym) and any(l["symbol"] in current_mappings for l in _listings(sym)):
                logger.info(f"[SourceMappingManager] 🛡️ 거래소 없는 모호한 코인 심볼 '{sym}' 차단 (이미 정규 자산 존재)")
                continue
            if (sym.startswith("101S") or sym.startswith("101V")) and "10100" in current_mappings:
                logger.info(f"[SourceMappingManager] 🛡️ 국내 선물 코드 '{sym}' 차단 (이미 표준 '10100' 존재)")
                continue

            # 심볼 패턴 기반 자동 메타데이터 생성
            led_mt = cls._ledger_market_type(sym)  # 원장에 있으면 원장이 정답, 없을 때만 표기 규칙으로 추정
            if sym.endswith("-PERP") or "FUTURES" in sym.upper():
                provider = "BINANCE"
                cat = "CRYPTO_FUTURES"
                role = "macro_futures"
                p_type = "15M_VISION"
                c_intervals = ["15m", "1m"]
            elif sym.startswith("KRW-"):
                provider = "UPBIT"
                cat = "CRYPTO_SPOT"
                role = meta.get("role") or "target_or_macro"
                p_type = "1M_ARCHIVE"
                c_intervals = ["1m", "10m", "15m"]
            elif sym.endswith("USDT"):
                provider = "BINANCE"
                cat = "CRYPTO_SPOT"
                role = "leading_stock"
                p_type = "1M_VISION"
                c_intervals = ["1m", "10m", "15m"]
            elif sym.endswith("-USD"):
                provider = "COINBASE"
                cat = "CRYPTO_SPOT"
                role = "leading_stock"
                p_type = "1M_REST"
                c_intervals = ["1m", "10m"]
            elif sym in ("NQ", "ES", "YM", "RTY", "CL", "GC"):
                provider = "CME"
                cat = "US_FUTURES"
                role = "macro_futures"
                p_type = "1M"
                c_intervals = ["1m", "5m", "15m", "1d"]
            elif led_mt in ("GLOBAL_INDEX_CFD", "COMMODITY_CFD", "FX_SPOT") or sym.startswith("US_") or sym.endswith("_CFD"):
                provider = "CTRADER"
                cat = "CFD_INDEX"
                role = "macro_proxy"
                p_type = "1M_PROTOBUF"
                c_intervals = ["1m", "15m"]
            elif (sym.isdigit() and len(sym) == 6) or led_mt in ("KRX_ETF", "KRX_STOCK"):
                provider = "CREON"
                cat = led_mt if led_mt in ("KRX_ETF", "KRX_STOCK") else "KRX_STOCK"
                role = meta.get("role") or "leading_stock"
                p_type = "TICK_AND_1M_AND_1D"
                c_intervals = ["10s", "1m", "1d"]
            else:
                provider = "KIS"
                cat = "GLOBAL_EQUITY"
                role = meta.get("role") or "reference"
                p_type = "1M"
                c_intervals = ["1m"]

            current_mappings[sym] = {
                "symbol": sym,
                "name": cls._canonical_name(sym) or meta["name"],
                "category": cat,
                "role": role,
                "primary_source": provider,
                "primary_type": p_type,
                "fallback_source": cls.FALLBACK_SOURCE_BY_PROVIDER.get(provider, "CME"),
                "fallback_type": "1M",
                "ingestion_resolution": "TICK" if "TICK" in p_type else "1M",
                "canonical_intervals": c_intervals,
                "ingestion_sources": ["TICK", "1M", "1D"] if provider == "CREON" else (["1M", "1D"] if provider in ("UPBIT", "BINANCE", "COINBASE") else (["TICK", "1M"] if "TICK" in p_type else ["1M"])),
                "is_active": True,
                "description": meta.get("description") or f"SSOT(symbol_relations) 자동 동기화 자산 ({provider})",
                "live_connection": cls.build_default_live_connection(sym, cat, role, provider)
            }
            cls._ensure_in_ledger(sym, current_mappings[sym])
            changed = True
            logger.info(f"[SourceMappingManager] 🛡️ [Zero-Drift] symbol_relations에서 신규 자산 자동 감지 및 대장 등재: {sym} ({provider})")

        return current_mappings, changed

    @classmethod
    def auto_discover_vault_manifests(cls, current_mappings: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """
        DataVault 내 정식 .manifest.json 파일들을 전수 스캔(raw 및 normalized)하여
        대장에 누락된 검증 완료 자산을 자동 등록 (Self-Healing / Zero-Maintenance)
        """
        changed = False
        scan_roots = [raw_root(), data_vault_path("normalized")]

        for scan_dir in scan_roots:
            if not os.path.exists(scan_dir):
                continue
            for root, _, files in os.walk(scan_dir):
                for f in files:
                    if f.endswith(".manifest.json") and not f.startswith("."):
                        manifest_path = os.path.join(root, f)
                        try:
                            with open(manifest_path, "r", encoding="utf-8") as mf:
                                meta = json.load(mf)

                            sym = (meta.get("symbol") or "").strip().upper()
                            if not sym:
                                continue

                            clean_sym = sym[1:].strip().upper() if (sym.startswith("A") and len(sym) == 7 and sym[1:].isalnum()) else sym
                            if sym in current_mappings or clean_sym in current_mappings:
                                continue
                            if clean_sym in ("FUTURES", "10100", "101V000", "FUTURES_1M") and any(k in current_mappings for k in ("10100", "FUTURES")):
                                continue
                            if clean_sym in ("SPOT", "U180", "INDEX", "SPOT_1M") and any(k in current_mappings for k in ("U180", "SPOT")):
                                continue
                            if clean_sym in ("NQ_FUTURES", "NQ_FUTURES_1M") and "NQ" in current_mappings:
                                continue
                            if clean_sym in ("SP500_FUTURES", "SP500_FUTURES_1M") and any(k in current_mappings for k in ("ES", "SP500")):
                                continue
                            if (meta.get("provider") or "").upper() == "MACRO":
                                continue
                            if cls._is_foreign_listing_of_mapped_asset(sym, current_mappings):
                                continue

                            provider = (meta.get("provider") or "UNKNOWN").upper()
                            market = meta.get("market", "")
                            source_meta = meta.get("source_meta", {})
                            notes = source_meta.get("notes") or f"{clean_sym} ({provider})"
                            ind_type = str(source_meta.get("indicator_type", "")).lower()

                            role = "macro_proxy" if ("proxy" in ind_type or "PROXY" in market) else "leading_stock"
                            category = market or meta.get("asset_type") or "RAW_INGEST"

                            current_mappings[clean_sym] = {
                                "symbol": clean_sym,
                                "name": notes,
                                "category": category,
                                "role": role,
                                "primary_source": provider,
                                "primary_type": source_meta.get("period", "1M"),
                                "fallback_source": cls.FALLBACK_SOURCE_BY_PROVIDER.get(
                                    provider,
                                    "CME" if "FUTURES" in category or "PROXY" in market else "KIS",
                                ),
                                "fallback_type": "1M",
                                "ingestion_resolution": "TICK" if "tick" in str(source_meta.get("notes", "")).lower() else "1M",
                                "canonical_intervals": ["10s", "1m"] if "tick" in str(source_meta.get("notes", "")).lower() else ["1m"],
                                "is_active": True,
                                "description": f"DataVault {provider} 자동 탐지 자산",
                                "live_connection": cls.build_default_live_connection(clean_sym, category, role, provider)
                            }
                            changed = True
                            logger.info(f"[SourceMappingManager] 🛡️ 신규 자산 자동 탐지 및 대장 등록: {sym} ({provider})")

                        except Exception as err:
                            logger.debug(f"[SourceMappingManager] 매니페스트 읽기 스킵 ({manifest_path}): {err}")

        return current_mappings, changed

    @classmethod
    def load_all_mappings(cls, force_refresh: bool = False) -> Dict[str, Any]:
        """source_mappings.json 로드, 자동 탐지 동기화 및 캐싱"""
        if not os.path.exists(_MAPPINGS_FILE_PATH):
            logger.warning(f"[SourceMappingManager] 매핑 파일 없음: {_MAPPINGS_FILE_PATH}")
            return {}

        try:
            mtime = os.path.getmtime(_MAPPINGS_FILE_PATH)
            if not force_refresh and cls._cache is not None and mtime == cls._last_mtime:
                return cls._cache

            with open(_MAPPINGS_FILE_PATH, "r", encoding="utf-8") as f:
                cls._cache = json.load(f)
                cls._last_mtime = mtime

            # 1. symbol_relations.json 및 DataVault 실물 데이터 전수 크로스 동기화 (Zero-Drift)
            maps, ch1 = cls.sync_with_symbol_relations(cls._cache)
            maps, ch2 = cls.auto_discover_vault_manifests(maps)
            cls._cache = maps
            needs_save = ch1 or ch2

            # 2. live_connection 누락/드리프트 자산에 대한 Self-healing 보정
            live_healed = False
            for s_key, s_info in cls._cache.items():
                if not isinstance(s_info, dict):
                    continue
                p_src = s_info.get("primary_source", "")
                if "live_connection" not in s_info:
                    s_info["live_connection"] = cls.build_default_live_connection(
                        s_key, s_info.get("category", ""), s_info.get("role", ""), p_src
                    )
                    live_healed = True
                else:
                    lc_provider = str(s_info["live_connection"].get("provider", "")).upper()
                    cat_up = str(s_info.get("category", "")).upper()
                    # CREON은 실시간 접속에 절대 쓸 수 없으므로 즉시 교정 (규칙 6 준수)
                    is_creon_violation = lc_provider == "CREON"
                    # 가상자산은 provider가 반드시 이 자산의 실제 primary_source와 일치해야 한다.
                    # 심볼 리터럴 하드코딩으로 생성된 과거 값이 primary_source와 어긋나 있으면
                    # (예: BINANCE 종목인데 live_connection.provider가 UPBIT로 박제된 사례)
                    # 조용히 방치되지 않도록 매 로드마다 재검증한다.
                    is_crypto_drift = (
                        cat_up in ("CRYPTO_SPOT", "CRYPTO_FUTURES")
                        and str(p_src).upper() in ("BINANCE", "COINBASE", "UPBIT")
                        and lc_provider != str(p_src).upper()
                    )
                    if is_creon_violation or is_crypto_drift:
                        s_info["live_connection"] = cls.build_default_live_connection(
                            s_key, s_info.get("category", ""), s_info.get("role", ""), p_src
                        )
                        live_healed = True

            # 3. 종목명(name) SSOT(symbols_registry.json) 재검증 — 과거에는 신규 자산 등록 시
            #    한 번 복사된 뒤 다시는 검증되지 않아 드리프트가 영구 고착됐다(예: 396500이
            #    source_mappings.json에서만 다른 ETF 이름으로 잘못 박제됐던 사례).
            #    live_connection 자가치유와 동일하게 매 로드마다 재검증해 자동 교정한다.
            #    설명(description)도 같은 이유로 원장이 소유한다 (대장마다 문장이 제각각 어긋나 있었다).
            from shared.config.config_manager import get_ledger_fields
            name_healed = False
            for s_key, s_info in cls._cache.items():
                if not isinstance(s_info, dict):
                    continue
                for f, v in get_ledger_fields(s_key, cls._LEDGER_OWNED).items():
                    if f in s_info:
                        # 파일에 사본이 남아 있음 (구버전 저장분) -> 원장 기준으로 교정하고 파일에서는 제거
                        name_healed = True
                    s_info[f] = v  # 메모리에서만 원장 값으로 해석
            if needs_save or live_healed or name_healed:
                cls.save_all_mappings(cls._cache)

            return cls._cache
        except Exception as e:
            logger.error(f"[SourceMappingManager] 매핑 파일 로드 실패: {e}")
            return cls._cache or {}

    @classmethod
    def validate_live_connection(cls, live_conn: Dict[str, Any]) -> Tuple[bool, str]:
        """실시간 접속 정보 유효성 검증 (CREON 실시간 배제 원칙 엄격 강제)"""
        if not isinstance(live_conn, dict):
            return False, "live_connection must be a dictionary"
        provider = str(live_conn.get("provider", "")).upper()
        if provider == "CREON":
            return False, "CREON is strictly prohibited for live_connection (CREON is for historical tick archive only)."
        if not provider:
            return False, "provider is required in live_connection"
        if not live_conn.get("feed_type"):
            return False, "feed_type is required in live_connection"
        if not live_conn.get("symbol"):
            return False, "symbol is required in live_connection"
        return True, "ok"

    @classmethod
    def build_default_live_connection(cls, symbol: str, category: str = "", role: str = "", primary_source: str = "") -> Dict[str, Any]:
        """자산 카테고리 및 원천(primary_source)에 맞는 표준 live_connection 기본값 생성.

        가상자산 provider는 심볼 문자열을 하드코딩 매칭(예: "BTCUSDT"면 무조건 BINANCE)해서
        추측하지 않고, 그 자산에 이미 선언된 primary_source를 그대로 따른다. 심볼 리터럴
        매칭 방식은 새 심볼이 추가될 때마다 사람이 잊지 않고 분기를 추가해야 해서 드리프트가
        나기 쉽고(예: BTCUSDT/BTC-USD가 실제로는 UPBIT에 존재하지도 않는 마켓 코드로
        고정되어 있던 사례), primary_source 기반으로 바꾸면 그런 수동 동기화가 필요 없다.
        """
        clean_sym = cls.clean_symbol(symbol)
        cat = str(category).upper()
        src = str(primary_source or "").upper()

        # 1. 국내 지수/선물/주식/ETF (KRX)
        if "KRX" in cat or clean_sym.isdigit() or clean_sym in ("10100", "U180") or clean_sym.startswith(("101S", "101V")):
            if clean_sym == "U180":
                return {
                    "provider": "KIS",
                    "feed_type": "REST_POLLING",
                    "channel": "index",
                    "symbol": "U180",
                    "required_depth": "BAR_1M",
                    "timeout_sec": 10,
                    "is_active": True
                }
            return {
                "provider": "KIS",
                "feed_type": "WEBSOCKET",
                "channel": "H0STCNT0",
                "symbol": clean_sym,
                "required_depth": "TICK_AND_ORDERBOOK",
                "timeout_sec": 15,
                "is_active": True
            }

        # 2. 해외 주식/ETF
        if cat in ("OVERSEAS_STOCK", "US_STOCK", "US_ETF"):
            tr_key = clean_sym if clean_sym.startswith(("DNAS", "DNYS", "DAMS")) else f"DNAS{clean_sym}"
            return {
                "provider": "KIS",
                "feed_type": "WEBSOCKET",
                "channel": "HDFSCNT0",
                "symbol": tr_key,
                "required_depth": "TICK",
                "timeout_sec": 15,
                "is_active": True
            }

        # 3. 가상자산 (CRYPTO) — provider는 이 자산의 primary_source를 그대로 따른다
        is_crypto = "CRYPTO" in cat or clean_sym.startswith("KRW-") or clean_sym.endswith("-PERP")
        if is_crypto:
            is_futures = clean_sym.endswith("-PERP") or "FUTURES" in cat

            if src == "BINANCE":
                # Binance 거래쌍은 항상 BASE+QUOTE(예: BTCUSDT) 형태다. "-PERP" 접미사만
                # 떼어내는 것만으로는 base 심볼만 남는 경우(예: NVDA-PERP -> NVDA)가 있어
                # 실제 상장 심볼(NVDAUSDT)과 어긋나므로, USDT 마켓이 아니면 명시적으로 붙인다.
                base = clean_sym.replace("-PERP", "").replace("-", "").replace("_", "").upper()
                if not base.endswith("USDT"):
                    base += "USDT"
                stream_sym = base.lower()
                if is_futures:
                    return {
                        "provider": "BINANCE",
                        "feed_type": "WEBSOCKET",
                        "channel": "kline_15m",
                        "symbol": stream_sym,
                        "required_depth": "TICK",
                        "timeout_sec": 10,
                        "is_active": True,
                        "endpoint_url": "wss://fstream.binance.com/ws"
                    }
                return {
                    "provider": "BINANCE",
                    "feed_type": "WEBSOCKET",
                    "channel": "trade",
                    "symbol": stream_sym,
                    "required_depth": "TICK",
                    "timeout_sec": 10,
                    "is_active": True,
                    "endpoint_url": "wss://stream.binance.com:9443/ws"
                }

            if src == "COINBASE":
                return {
                    "provider": "COINBASE",
                    "feed_type": "WEBSOCKET",
                    "channel": "ticker",
                    "symbol": clean_sym,
                    "required_depth": "TICK",
                    "timeout_sec": 10,
                    "is_active": True,
                    "endpoint_url": "wss://ws-feed.exchange.coinbase.com"
                }

            # UPBIT 또는 primary_source 미지정 시 기본값 (마켓 코드는 심볼 그대로 사용, 보통 KRW- 접두사)
            return {
                "provider": "UPBIT",
                "feed_type": "WEBSOCKET",
                "channel": "ticker",
                "symbol": clean_sym,
                "required_depth": "TICK",
                "timeout_sec": 10,
                "is_active": True,
                "endpoint_url": "wss://api.upbit.com/websocket/v1"
            }

        # 4. 글로벌 매크로 선물 (Yahoo REST 연동) — yahoo_ticker는 symbols_registry.json SSOT
        if cat == "OVERSEAS_FUTURES":
            from shared.config.config_manager import load_symbols_registry
            yahoo_ticker = load_symbols_registry().get(clean_sym, {}).get("yahoo_ticker")
            if yahoo_ticker:
                return {
                    "provider": "YAHOO",
                    "feed_type": "REST_POLLING",
                    "channel": "1m_bar",
                    "symbol": yahoo_ticker,
                    "required_depth": "BAR_1M",
                    "timeout_sec": 10,
                    "is_active": True,
                    "endpoint_url": "query1.finance.yahoo.com"
                }

        # 5. cTrader 장외 CFD 대용 지표 7종 (cTrader 공식 Protobuf TCP 직결)
        return {
            "provider": "CTRADER",
            "feed_type": "PROTOBUF_TCP",
            "channel": "ProtoOASpotEvent",
            "symbol": clean_sym,
            "required_depth": "TICK_AND_BAR",
            "timeout_sec": 10,
            "is_active": True,
            "endpoint_url": "live.ctrader.com:5035"
        }

    @classmethod
    def get_live_connection(cls, symbol: str) -> Optional[Dict[str, Any]]:
        """특정 자산의 live_connection 설정 조회"""
        clean_sym = cls.clean_symbol(symbol)
        mappings = cls.load_all_mappings()
        info = mappings.get(clean_sym) or mappings.get(symbol)
        if not info:
            return None
        conn = info.get("live_connection")
        if not conn:
            conn = cls.build_default_live_connection(clean_sym, info.get("category", ""), info.get("role", ""), info.get("primary_source", ""))
        return conn

    @classmethod
    @_LOCK
    def update_live_connection(cls, symbol: str, live_conn: Dict[str, Any]) -> Dict[str, Any]:
        """자산의 live_connection 설정 업데이트 및 원자적 영속화"""
        valid, msg = cls.validate_live_connection(live_conn)
        if not valid:
            return {"status": "error", "message": msg}

        clean_sym = cls.clean_symbol(symbol)
        mappings = cls.load_all_mappings(force_refresh=True)
        key = clean_sym if clean_sym in mappings else symbol
        if key not in mappings:
            return {"status": "error", "message": f"Asset '{symbol}' not found in source mappings"}

        mappings[key]["live_connection"] = live_conn
        saved = cls.save_all_mappings(mappings)
        if not saved:
            return {"status": "error", "message": "Failed to save updated mappings"}
        return {"status": "ok", "symbol": key, "live_connection": live_conn}

    @classmethod
    @_LOCK
    def update_source_config(
        cls,
        symbol: str,
        primary_source: Optional[str] = None,
        primary_type: Optional[str] = None,
        fallback_source: Optional[str] = None,
        fallback_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """자산의 과거데이터 출처(주 제공처/타입 및 보조 제공처/타입) 갱신"""
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)
        key = clean_sym if clean_sym in mappings else symbol.strip()
        if key not in mappings:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {clean_sym}"}

        info = mappings[key]
        if primary_source is not None and primary_source.strip():
            info["primary_source"] = primary_source.strip().upper()
        if primary_type is not None:
            info["primary_type"] = primary_type.strip()
        if fallback_source is not None:
            info["fallback_source"] = fallback_source.strip().upper()
        if fallback_type is not None:
            info["fallback_type"] = fallback_type.strip()

        # 주 출처(primary_source)가 변경되면, 새 제공처가 지원하지 않는 기존 원천 스케일을 자동 보정
        new_primary = info.get("primary_source", "CREON").upper()
        supported = cls.PROVIDER_RAW_CAPABILITIES.get(new_primary, ["TICK", "1M", "1D"])
        curr_sources = info.get("ingestion_sources") or []
        valid_sources = [s for s in curr_sources if s in supported]
        if not valid_sources:
            valid_sources = [supported[0]] if supported else []
        info["ingestion_sources"] = valid_sources
        for res_cand in ["TICK", "1S", "1M", "5M", "1D"]:
            if res_cand in valid_sources:
                info["ingestion_resolution"] = res_cand
                break

        success = cls.save_all_mappings(mappings)
        return {
            "status": "ok" if success else "error",
            "symbol": key,
            "asset": mappings[key],
            "message": f"{info.get('name', key)}({key})의 과거데이터 출처 설정이 업데이트되었습니다." if success else "저장 실패"
        }

    @classmethod
    @_LOCK
    def set_archived(cls, symbol: str, archived: bool = True) -> Dict[str, Any]:
        """
        자산을 보관(Archive) 처리 - 완전 삭제와는 다른 개념.
        대장에서 제거하지 않고 별도 상태(archived)로만 표시하여, 나중에도 존재를 알 수 있도록 목록에 계속 노출한다.
        보관 시 수집 구독(is_active)은 자동으로 비활성화되며, 보관 해제는 구독을 자동으로 재개하지 않는다(수동 재활성화 필요).
        """
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)
        key = clean_sym if clean_sym in mappings else symbol.strip()
        if key not in mappings:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {clean_sym}"}

        mappings[key]["archived"] = bool(archived)
        if archived:
            mappings[key]["is_active"] = False

        success = cls.save_all_mappings(mappings)
        return {
            "status": "ok" if success else "error",
            "symbol": key,
            "archived": bool(archived),
            "message": f"{mappings[key].get('name', key)}({key})이 {'보관 처리' if archived else '보관 해제'}되었습니다." if success else "저장 실패"
        }

    @classmethod
    @_LOCK
    def unregister_asset(cls, symbol: str, cascade: bool = False) -> Dict[str, Any]:
        """
        자산을 대장(source_mappings.json)에서 완전히 제거하고,
        연관된 1단계 원천 파일 및 사이드카 매니페스트, 2단계 정규화 파생 Parquet까지 영구 삭제한다.
        되돌릴 수 없는 파괴적 작업이므로 호출 전 확인 절차는 호출자(API 레이어) 책임.

        symbol_relations.json(SSOT)에서 아직 타겟/참조로 살아있는 경우, 삭제하면
        다음 Zero-Drift 동기화 때 자동으로 재등록되어 버리므로(귀신처럼 되살아남),
        cascade=False(기본)면 삭제를 차단하고 참조 목록을 반환한다.
        cascade=True면 SSOT의 해당 관계까지 함께 제거한 뒤 삭제를 진행한다.
        """
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)
        key = clean_sym if clean_sym in mappings else symbol.strip()
        if key not in mappings:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {clean_sym}"}

        from shared.data_library.symbol_relations_manager import SymbolRelationsManager
        referencing = SymbolRelationsManager.find_referencing_targets(key)

        if referencing and not cascade:
            target_desc = ", ".join(f"{r['target_name']}({r['target_symbol']})" for r in referencing)
            return {
                "status": "blocked",
                "symbol": key,
                "referencing_targets": referencing,
                "message": (f"이 심볼은 symbol_relations.json(SSOT)에서 {target_desc}이(가) 아직 참조 중입니다. "
                            f"이대로 삭제하면 다음 동기화 때 자동으로 재등록됩니다. "
                            f"관계를 먼저 해제하거나, 함께 삭제(cascade)를 선택하세요.")
            }

        if referencing and cascade:
            for r in referencing:
                if r["relation"] == "is_target":
                    SymbolRelationsManager.remove_target(r["target_symbol"])
                else:
                    SymbolRelationsManager.remove_reference(r["target_symbol"], key)

        info = mappings[key]
        name = info.get("name", key)
        deleted_files: List[str] = []
        freed_bytes = 0

        # 1단계 원천 파일 + 사이드카 매니페스트 삭제
        primary_src = info.get("primary_source", "CREON")
        category = info.get("category", "")
        raw_stat = cls.find_raw_file_and_manifest(key, primary_src, category)
        if raw_stat["exists"] and os.path.exists(raw_stat["file_path"]):       # 1분봉 일자 저장소의 논리 경로는 파일이 아니다 — 아래 폴더 정리가 맡는다
            fp = raw_stat["file_path"]
            try:
                freed_bytes += os.path.getsize(fp)
                os.remove(fp)
                deleted_files.append(fp)
                mf_path = f"{fp}.manifest.json"
                if os.path.exists(mf_path):
                    os.remove(mf_path)
                    deleted_files.append(mf_path)
            except Exception as e:
                logger.warning(f"[SourceMappingManager] 원천 파일 삭제 실패 ({fp}): {e}")
        freed_bytes += cls._remove_tick_day_store(key, deleted_files)
        freed_bytes += cls._remove_candle_day_store(key, deleted_files)

        # 2단계 정규화 파생 Parquet 전량 삭제 (target_scales를 빈 리스트로 취급하여 모든 스케일 정리)
        try:
            pruned = cls.prune_unselected_normalized_files(key, [])
            for pf in pruned:
                deleted_files.append(pf)
        except Exception as e:
            logger.warning(f"[SourceMappingManager] 정규화 파생 파일 삭제 실패 ({key}): {e}")

        del mappings[key]
        success = cls.save_all_mappings(mappings)

        freed_mb = round(freed_bytes / (1024 * 1024), 2)
        cascade_msg = f" (SSOT 참조 관계 {len(referencing)}건도 함께 해제됨)" if referencing else ""

        return {
            "status": "ok" if success else "error",
            "symbol": key,
            "deleted_files": deleted_files,
            "freed_mb": freed_mb,
            "resurrection_risk": False,
            "message": (f"{name}({key})가 대장 및 원시/정규화 데이터에서 완전히 삭제되었습니다 ({freed_mb} MB 회수).{cascade_msg}"
                        if success else "저장 실패")
        }

    @classmethod
    @_LOCK
    def reset_asset_data(cls, symbol: str) -> Dict[str, Any]:
        """
        자산의 기존 원천 파일(CSV, 사이드카 매니페스트, days.json, unrecoverable.json 등)과
        정규화 파생 Parquet 파일을 안전 격리(quarantine 백업) 후 완전히 삭제한다.
        대장(source_mappings.json) 등록 정보와 설정은 그대로 유지된다.
        """
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)
        key = clean_sym if clean_sym in mappings else symbol.strip()
        if key not in mappings:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {clean_sym}"}

        info = mappings[key]
        name = info.get("name", key)
        raw_clean = cls.clean_symbol(key)
        
        # 매칭할 심볼 후보군 (대소문자 무시)
        target_keys = {key.upper(), raw_clean.upper()}
        if raw_clean:
            target_keys.add(f"A{raw_clean.upper()}")
            if raw_clean.startswith("KRW_") or raw_clean.startswith("KRW-"):
                target_keys.add(raw_clean[4:].upper())

        def _is_target_file(fname: str) -> bool:
            up_name = fname.upper()
            base_no_ext = up_name.split(".")[0]
            stem = base_no_ext.rsplit("_", 1)[0] if "_" in base_no_ext else base_no_ext
            return (base_no_ext in target_keys or stem in target_keys)

        from shared.data_library.paths import data_vault_path, data_path
        quarantine_dir = data_vault_path("quarantine", "reset_backups", f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{raw_clean or key}")
        os.makedirs(quarantine_dir, exist_ok=True)

        deleted_raw_files: List[str] = []
        deleted_norm_files: List[str] = []
        freed_bytes = 0

        # 1. DataVault/raw/ 및 data/historical/ 내 원천 파일 검색 & 격리 백업 후 삭제
        scan_raw_dirs = [
            data_vault_path("raw"),
            data_path("historical")
        ]
        for sdir in scan_raw_dirs:
            if not os.path.exists(sdir):
                continue
            for root, _, files in os.walk(sdir):
                for f in files:
                    if _is_target_file(f):
                        fpath = os.path.join(root, f)
                        try:
                            fsize = os.path.getsize(fpath)
                            freed_bytes += fsize
                            dst_backup = os.path.join(quarantine_dir, f)
                            shutil.copy2(fpath, dst_backup)
                            os.remove(fpath)
                            deleted_raw_files.append(fpath)
                        except Exception as fe:
                            logger.warning(f"[SourceMappingManager] 원천 파일 격리/삭제 실패 ({fpath}): {fe}")

        # 1-1. 틱 일자 저장소 폴더(raw/creon/tick/A{sym}/) — 날짜 이름 파일이라 위 이름 매칭에 걸리지 않으므로 폴더째 격리
        freed_bytes += cls._remove_tick_day_store(key, deleted_raw_files, backup_to=quarantine_dir)
        # 1-2. 1분봉 일자 저장소 폴더(raw/creon/candle_1m/A{sym}/ · 선물·지수는 A10100/ · AU180/) — 같은 이유로 폴더째 격리
        freed_bytes += cls._remove_candle_day_store(key, deleted_raw_files, backup_to=quarantine_dir, backup_name_suffix="_1m")

        # 2. DataVault/normalized/ 내 정규화 Parquet 및 사이드카 매니페스트 검색 & 삭제
        norm_dir = data_vault_path("normalized")
        if os.path.exists(norm_dir):
            for root, _, files in os.walk(norm_dir):
                for f in files:
                    if _is_target_file(f):
                        fpath = os.path.join(root, f)
                        try:
                            freed_bytes += os.path.getsize(fpath)
                            os.remove(fpath)
                            deleted_norm_files.append(fpath)
                        except Exception as ne:
                            logger.warning(f"[SourceMappingManager] 정규화 파일 삭제 실패 ({fpath}): {ne}")

        freed_mb = round(freed_bytes / (1024 * 1024), 2)
        return {
            "status": "ok",
            "symbol": key,
            "name": name,
            "quarantine_backup_dir": quarantine_dir,
            "deleted_raw_files": deleted_raw_files,
            "deleted_norm_files": deleted_norm_files,
            "freed_mb": freed_mb,
            "message": f"{name}({key}) 기존 데이터 {len(deleted_raw_files)}개 원천 파일 격리 및 삭제 완료 ({freed_mb} MB 회수)"
        }


    # 수집 대장은 이름/설명을 저장하지 않는다 — 원장이 소유하고 로드 시 채워진다.
    # (category 는 원장의 market_type 과 달리 '수집 파이프라인 분류'라서 여기 남는다: 예) NVDA-PERP 는 원장에서는
    #  GLOBAL_FUTURES 지만 수집은 CRYPTO_FUTURES 파이프라인을 탄다)
    _LEDGER_OWNED = ("name", "description")

    @classmethod
    def _strip_ledger_names(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """저장용 사본에서 원장이 값을 알고 있는 속성(이름/설명)을 제거 (로드 시 원장에서 해석)"""
        from shared.config.config_manager import get_ledger_fields
        out: Dict[str, Any] = {}
        for sym, info in data.items():
            if isinstance(info, dict):
                owned = get_ledger_fields(sym, cls._LEDGER_OWNED)
                if any(f in info for f in owned):
                    info = {k: v for k, v in info.items() if k not in owned}
            out[sym] = info
        return out

    @classmethod
    def save_all_mappings(cls, data: Dict[str, Any]) -> bool:
        """원자적(Atomic Write)으로 source_mappings.json 저장"""
        dir_name = os.path.dirname(_MAPPINGS_FILE_PATH)
        os.makedirs(dir_name, exist_ok=True)
        temp_name = None
        cache_data = data
        data = cls._strip_ledger_names(data)  # 이름은 종목 마스터 원장에만 저장
        try:
            with file_locked(_MAPPINGS_FILE_PATH), \
                    tempfile.NamedTemporaryFile("w", dir=dir_name, delete=False, encoding="utf-8", suffix=".tmp") as tf:
                temp_name = tf.name
                json.dump(data, tf, indent=2, ensure_ascii=False)
            with file_locked(_MAPPINGS_FILE_PATH):
                os.replace(temp_name, _MAPPINGS_FILE_PATH)
            cls._cache = cache_data
            cls._last_mtime = os.path.getmtime(_MAPPINGS_FILE_PATH)
            return True
        except Exception as e:
            logger.error(f"[SourceMappingManager] 매핑 파일 저장 실패: {e}")
            if temp_name and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
            return False

    @classmethod
    def get_active_mappings(cls) -> Dict[str, Any]:
        """활성화(is_active == True)된 매핑 항목만 반환"""
        all_maps = cls.load_all_mappings()
        return {k: v for k, v in all_maps.items() if v.get("is_active", True)}

    @classmethod
    def _remove_day_dir(cls, d: str, deleted: List[str], backup_to: Optional[str], backup_name: str, what: str) -> int:
        if not os.path.isdir(d):
            return 0
        size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in fs)
        try:
            for r, _, fs in os.walk(d):                     # 봉인본(0o444)도 옮기거나 지울 수 있게
                for f in fs:
                    os.chmod(os.path.join(r, f), 0o644)
            if backup_to:
                os.makedirs(backup_to, exist_ok=True)
                shutil.move(d, os.path.join(backup_to, backup_name))
            else:
                shutil.rmtree(d)
            deleted.append(d + os.sep)
            return size
        except Exception as e:
            logger.warning(f"[SourceMappingManager] {what} 일자 저장소 정리 실패 ({d}): {e}")
            return 0

    @classmethod
    def _remove_tick_day_store(cls, symbol: str, deleted: List[str], backup_to: Optional[str] = None) -> int:
        """틱 일자 저장소 폴더를 지운다(backup_to 가 있으면 그 아래로 옮긴다). 반환: 회수한 바이트."""
        from shared.data_library import tick_store
        d = tick_store.day_dir(cls.clean_symbol(symbol))
        return cls._remove_day_dir(d, deleted, backup_to, os.path.basename(d), "틱")

    @classmethod
    def _remove_candle_day_store(cls, symbol: str, deleted: List[str], backup_to: Optional[str] = None,
                                 backup_name_suffix: str = "") -> int:
        """1분봉 일자 저장소 폴더(candle_1m/A{sym}/)를 지운다(backup_to 가 있으면 그 아래로 옮긴다). 틱 폴더와 이름이 겹치지 않게 백업 이름에 접미사를 붙일 수 있다."""
        from shared.data_library import candle_store
        clean = cls.clean_symbol(symbol)
        keys = [clean]
        try:
            macro = cls.macro_series_info(clean)       # 선물·지수는 원장 심볼 키로 폴더를 만든다(A10100 / AU180)
        except Exception:
            macro = None
        if macro and macro[0] not in keys:
            keys.append(macro[0])
        freed = 0
        for k in keys:
            d = candle_store.day_dir(k)
            freed += cls._remove_day_dir(d, deleted, backup_to, os.path.basename(d) + backup_name_suffix, "1분봉")
        return freed

    @classmethod
    def _creon_interval_candidate_groups(cls, clean_sym: str) -> Dict[str, List[str]]:
        """CREON 심볼의 그래뉼러리티(TICK/1M/1D)별 후보 경로 그룹.
        find_raw_file_and_manifest(단일 파일 우선순위 탐색)와
        RawStoreHealthEngine(그래뉼러리티별 개별 존재 여부 확인)이 공유한다.
        """
        from shared.data_library import tick_store          # 함수 안 import: pandas 를 32-bit 수집기 경로로 끌고 오지 않는다
        return {                                               # 1M 후보는 논리 경로 — 일자 저장소(파일 없음)이거나 아직 통합 CSV 일 수 있다(candle_store.path_exists)
            "TICK": [tick_store.logical_path(clean_sym)],      # 논리 경로(파일 없음) — 존재·읽기는 tick_store 가 답한다
            "1M": [
                data_vault_path("raw", "creon", "candle_1m", f"A{clean_sym}_1m.csv"),
                data_vault_path("raw", "creon", "candle_1m", f"{clean_sym}_1m.csv"),
            ],
            "1D": [
                data_vault_path("raw", "creon", "candle_1d", f"A{clean_sym}_1d.csv"),
                data_vault_path("raw", "creon", "candle_1d", f"{clean_sym}_1d.csv"),
            ],
        }

    @classmethod
    def get_raw_interval_candidate_groups(cls, symbol: str, provider: str, category: str = "") -> Dict[str, List[str]]:
        """제공처 및 심볼에 대한 그래뉼러리티(TICK / 1S / 1M / 5M / 10M / 15M / 1D 등)별 후보 경로 그룹.
        하드코딩을 배제하고, 디스크 실물 디렉토리의 파일들과 표준 주기 후보들을 동적으로 매핑한다.
        """
        clean_sym = cls.clean_symbol(symbol)
        p_up = (provider or "").upper()
        groups: Dict[str, List[str]] = {}

        if p_up == "CREON":
            return cls._creon_interval_candidate_groups(clean_sym)
        elif p_up in ("UPBIT", "BINANCE", "COINBASE"):
            raw_dir = data_vault_path("raw", "crypto", p_up.lower())
            discovered_scales = set()
            if os.path.exists(raw_dir):
                prefix_sym = f"{symbol}_"
                prefix_clean = f"{clean_sym}_"
                for fname in os.listdir(raw_dir):
                    if fname.endswith(".csv"):
                        if fname.startswith(prefix_sym):
                            scale_part = fname[len(prefix_sym):-4]
                            discovered_scales.add(scale_part)
                        elif fname.startswith(prefix_clean):
                            scale_part = fname[len(prefix_clean):-4]
                            discovered_scales.add(scale_part)
            # 1D, 1M 표준 및 디스크 발견 스케일 모두 등록 (순서: 1M, 1D, 10M, 15M, ...)
            scales_to_check = ["1m", "1d"] + [s.lower() for s in sorted(discovered_scales) if s.lower() not in ("1m", "1d")]
            for sc in scales_to_check:
                lbl = sc.upper()
                cands = [
                    os.path.join(raw_dir, f"{symbol}_{sc.lower()}.csv"),
                    os.path.join(raw_dir, f"{clean_sym}_{sc.lower()}.csv"),
                    os.path.join(raw_dir, f"{symbol}_{sc.upper()}.csv"),
                    os.path.join(raw_dir, f"{clean_sym}_{sc.upper()}.csv"),
                ]
                if sc.lower() in ("trade", "tick"):
                    lbl = "TICK"
                groups[lbl] = cands
        elif p_up == "KIS":
            raw_base = data_vault_path("raw", "kis")
            for sc in ("1m", "5m", "1d"):
                lbl = sc.upper()
                groups[lbl] = [
                    os.path.join(raw_base, f"candle_{sc}", f"{clean_sym}_{sc}.csv"),
                    os.path.join(raw_base, f"candle_{sc}", f"A{clean_sym}_{sc}.csv"),
                    os.path.join(raw_base, f"{clean_sym}_{sc}.csv"),
                ]
        elif p_up in ("CME", "YAHOO"):
            raw_base = data_vault_path("raw", "overseas")
            for sc in ("1m", "5m", "60m", "1d"):
                lbl = sc.upper()
                groups[lbl] = [
                    os.path.join(raw_base, f"futures_{sc}", f"{clean_sym}_{sc}.csv"),
                    os.path.join(raw_base, f"futures_{sc}", f"{clean_sym}_{sc.upper()}.csv"),
                    data_vault_path("raw", "cme", "futures", f"{clean_sym}_{sc}.csv"),
                ]
        elif p_up in ("CTRADER", "SPOTWARE"):
            raw_dir = data_vault_path("raw", "ctrader")
            for sc in ("1m", "1d"):
                lbl = sc.upper()
                groups[lbl] = [
                    os.path.join(raw_dir, f"{clean_sym}_{sc}.csv"),
                    os.path.join(raw_dir, f"{symbol}_{sc}.csv"),
                    os.path.join(raw_dir, f"{clean_sym}_{lbl}.csv"),
                    os.path.join(raw_dir, f"{symbol}_{lbl}.csv"),
                ]
        else:
            raw_dir = data_vault_path("raw", p_up.lower())
            for sc in ("1m", "1d", "tick"):
                lbl = sc.upper()
                groups[lbl] = [
                    os.path.join(raw_dir, f"{symbol}_{sc}.csv"),
                    os.path.join(raw_dir, f"{clean_sym}_{sc}.csv"),
                ]

        return groups

    @classmethod
    def find_raw_file_and_manifest(cls, symbol: str, provider: str, category: str) -> Dict[str, Any]:
        """
        자산 심볼 및 제공처에 따른 원천 파일 및 사이드카 매니페스트 실물 검색
        """
        clean_sym = cls.clean_symbol(symbol)
        p_up = provider.upper()

        candidate_paths = []

        if p_up == "CREON":
            # 1. 심볼별 틱/분봉 파일
            candidate_paths.extend([       # 틱은 일자 저장소(파일 하나가 아님)라 '대표 원천 파일' 후보에서 뺀다
                data_vault_path("raw", "creon", "candle_1m", f"A{clean_sym}_1m.csv"),
                data_vault_path("raw", "creon", "candle_1m", f"{clean_sym}_1m.csv"),
                data_vault_path("raw", "creon", "candle_1d", f"A{clean_sym}_1d.csv"),
                data_vault_path("raw", "creon", "candle_1d", f"{clean_sym}_1d.csv"),
            ])
            # 2. 선물/지수(macro 시계열): 원천 파일은 원장 심볼 키로 이름 짓는다 (10100_1m.csv / U180_1m.csv)
            macro = cls.macro_series_info(clean_sym)
            if macro:
                from shared.data_library.paths import macro_raw_path
                candidate_paths.extend([
                    macro_raw_path(macro[0], "1m"),
                ])
        elif p_up in ("CME", "YAHOO"):
            candidate_paths.extend([
                data_vault_path("raw", "overseas", "futures_1m", f"{clean_sym}_1m.csv"),
                data_vault_path("raw", "overseas", "futures_1m", f"{clean_sym}_1M.csv"),
                data_vault_path("raw", "overseas", "futures_1m", f"{clean_sym}_5m.csv"),
                data_vault_path("raw", "overseas", "futures_1m", f"{clean_sym}_60m.csv"),
                data_vault_path("raw", "overseas", "futures_1m", f"{clean_sym}_1d.csv"),
                data_vault_path("raw", "overseas", "futures_5m", f"{clean_sym}_5m.csv"),
                data_vault_path("raw", "overseas", "futures_60m", f"{clean_sym}_60m.csv"),
                data_vault_path("raw", "overseas", "futures_1d", f"{clean_sym}_1d.csv"),
                data_vault_path("normalized", "macro", f"{clean_sym}_1m.parquet"),
                data_vault_path("raw", "cme", "futures", f"{clean_sym}_1m.csv"),
                data_vault_path("raw", "cme", "futures", f"{clean_sym}_1d.csv"),
                data_vault_path("raw", "overseas", f"{clean_sym.lower()}_futures_1m.csv"),
                data_path("historical", "futures", f"{clean_sym}_1m.csv"),
                data_path("historical", "futures", f"{clean_sym}_1d.csv"),
            ])
        elif p_up == "KIS":
            candidate_paths.extend([
                data_vault_path("raw", "kis", "candle_1m", f"{clean_sym}_1m.csv"),
                data_vault_path("raw", "kis", "candle_5m", f"{clean_sym}_5m.csv"),
                data_vault_path("raw", "kis", "candle_1d", f"{clean_sym}_1d.csv"),
                data_vault_path("raw", "kis", f"{clean_sym}_1m.csv"),
                data_path("historical", "kis", f"{clean_sym}_1m.csv"),
                data_path(clean_sym, f"data_snapshot_1m.csv"),
                data_path(clean_sym, f"data_snapshot_5m.csv"),
            ])
        elif p_up == "UPBIT":
            candidate_paths.extend([
                data_vault_path("raw", "crypto", "upbit", f"{symbol}_1m.csv"),
                data_vault_path("raw", "crypto", "upbit", f"{clean_sym}_1m.csv"),
                data_vault_path("raw", "crypto", "upbit", f"{symbol}_1d.csv"),
                data_vault_path("raw", "crypto", "upbit", f"{clean_sym}_1d.csv"),
                data_vault_path("raw", "crypto", "upbit", f"{symbol}_10m.csv"),
                data_vault_path("raw", "crypto", "upbit", f"{clean_sym}_10m.csv"),
                data_vault_path("raw", "crypto", "upbit", f"{symbol}_15m.csv"),
                data_vault_path("raw", "crypto", "upbit", f"{clean_sym}_15m.csv"),
                data_vault_path("raw", "crypto", "upbit", f"{symbol}_trade.csv"),
                data_vault_path("raw", "upbit", "crypto", f"{symbol}_1m.csv"),
                data_path(clean_sym.replace("KRW-", "").replace("KRW_", ""), f"data_snapshot_10m.csv"),
            ])
        elif p_up == "BINANCE":
            candidate_paths.extend([
                data_vault_path("raw", "crypto", "binance", f"{clean_sym}_1m.csv"),
                data_vault_path("raw", "crypto", "binance", f"{symbol}_1m.csv"),
                data_vault_path("raw", "crypto", "binance", f"{clean_sym}_1d.csv"),
                data_vault_path("raw", "crypto", "binance", f"{symbol}_1d.csv"),
                data_vault_path("raw", "crypto", "binance", f"{clean_sym}_15m.csv"),
                data_vault_path("raw", "crypto", "binance", f"{symbol}_15m.csv"),
                data_vault_path("raw", "binance", "crypto", f"{clean_sym}_1m.csv"),
                data_path(clean_sym.replace("KRW-", "").replace("KRW_", ""), f"data_snapshot_10m.csv"),
            ])
        elif p_up == "COINBASE":
            candidate_paths.extend([
                data_vault_path("raw", "crypto", "coinbase", f"{clean_sym}_1m.csv"),
                data_vault_path("raw", "crypto", "coinbase", f"{symbol}_1m.csv"),
                data_vault_path("raw", "crypto", "coinbase", f"{clean_sym}_1d.csv"),
                data_vault_path("raw", "crypto", "coinbase", f"{symbol}_1d.csv"),
                data_vault_path("raw", "crypto", "coinbase", f"{clean_sym}_10m.csv"),
                data_vault_path("raw", "crypto", "coinbase", f"{symbol}_10m.csv"),
                data_vault_path("raw", "coinbase", "crypto", f"{clean_sym}_1m.csv"),
            ])
        elif p_up in ("CTRADER", "SPOTWARE"):
            candidate_paths.extend([
                data_vault_path("raw", "ctrader", f"{clean_sym}_1m.csv"),
                data_vault_path("raw", "ctrader", f"{symbol}_1m.csv"),
                data_vault_path("normalized", "candles_1m", f"{clean_sym}_1m.parquet"),
                data_vault_path("normalized", "candles_1m", f"{symbol}_1m.parquet"),
            ])


        from shared.data_library import candle_store            # 함수 안 import: pandas 를 32-bit 수집기 경로로 끌고 오지 않는다
        found_path = None
        for cp in candidate_paths:
            if candle_store.path_exists(cp):                    # 1분봉 논리 경로는 일자 저장소에 있어도 True
                found_path = cp
                break

        # 1단계 Raw 파일이 없으면 2단계 Normalized 표준 원장 fallback 확인
        if not found_path:
            norm_candidates = [
                data_vault_path("normalized", "candles_15m", f"{clean_sym}_15m.parquet"),
                data_vault_path("normalized", "candles_15m", f"{symbol}_15m.parquet"),
                data_vault_path("normalized", "candles_1m", f"{clean_sym}_1m.parquet"),
                data_vault_path("normalized", "candles_10s", f"{clean_sym}_10s.parquet"),
            ]
            for np_path in norm_candidates:
                if os.path.exists(np_path) and os.path.getsize(np_path) > 0:
                    found_path = np_path
                    break

        if not found_path:
            return {
                "exists": False,
                "file_path": None,
                "file_size": 0,
                "rows": 0,
                "manifest_exists": False,
                "manifest": None,
                "last_timestamp": None,
                "trading_days": 0,
            }

        csym = candle_store.symbol_from_path(found_path)
        if csym and not os.path.exists(found_path):             # 1분봉 일자 저장소 — 통합 CSV 도 매니페스트도 없다. 날짜 메타에서 요약한다
            ds = candle_store.days(csym)
            live = {d: e for d, e in ds.items() if e["status"] != "LOST"}
            d_dir = candle_store.day_dir(csym)
            return {
                "exists": True,
                "file_path": found_path,
                "file_size": sum(os.path.getsize(os.path.join(d_dir, f)) for f in os.listdir(d_dir) if f.endswith(".csv")),
                "rows": sum(int(e["rows"] or 0) for e in live.values()),
                "manifest_exists": True,                              # 날짜별 메타(행·해시·상태)가 매니페스트를 대신한다 — 검증은 candle_day_store.verify
                "manifest": {"day_store": True},
                "last_timestamp": candle_store.last_timestamp(csym),
                "trading_days": len(live),
            }

        file_size = os.path.getsize(found_path)
        manifest_meta = ManifestManager.load_manifest(found_path)
        manifest_exists = manifest_meta is not None

        # 행 수 및 타임스탬프 산출 (매니페스트 우선, 없으면 간이 스캔)
        rows = 0
        last_ts = None
        trading_days = 0

        if manifest_meta:
            integrity = manifest_meta.get("integrity", {})
            rows = integrity.get("row_count", 0)
            last_ts = integrity.get("last_timestamp")
            krx_v = manifest_meta.get("krx_validation", {})
            trading_days = krx_v.get("trading_days", 0)

        if rows == 0 or not last_ts:
            try:
                # 마지막 줄 추출하여 타임스탬프 파악
                with open(found_path, "rb") as f:
                    # 파일 끝부분 4KB 읽기
                    f.seek(max(0, file_size - 4096), os.SEEK_SET)
                    lines = f.read().decode("utf-8", errors="ignore").strip().splitlines()
                    if lines:
                        last_line = lines[-1]
                        parts = last_line.split(",")
                        if parts:
                            last_ts = parts[0].strip()
                # 대략적인 행 수 (헤더 제외)
                rows = max(1, int(file_size / 60))
            except Exception:
                pass

        return {
            "exists": True,
            "file_path": found_path,
            "file_size": file_size,
            "rows": rows,
            "manifest_exists": manifest_exists,
            "manifest": manifest_meta,
            "last_timestamp": last_ts,
            "trading_days": trading_days,
        }

    @classmethod
    def get_feed_registry_status(cls) -> List[Dict[str, Any]]:
        """
        전체 등록 자산들의 출처 매핑 및 실시간 누적/감사 현황 리스트 반환
        """
        mappings = cls.load_all_mappings()
        results = []

        for symbol, info in mappings.items():
            primary_src = info.get("primary_source", "CREON")
            category = info.get("category", "")
            role = info.get("role", "reference")
            is_active = info.get("is_active", True)

            raw_stat = cls.find_raw_file_and_manifest(symbol, primary_src, category)

            # 상태 판정
            if not raw_stat["exists"]:
                status_code = "MISSING"
                status_badge = "🔴 미수집"
                status_desc = "원천 파일 없음 (수집 필요)"
            elif not raw_stat["manifest_exists"]:
                status_code = "UNVERIFIED"
                status_badge = "🟡 매니페스트 부재"
                status_desc = "원천 파일 존재, SHA-256 감사 필요"
            else:
                status_code = "HEALTHY"
                status_badge = "🟢 정상 누적"
                status_desc = "SHA-256 무결성 전수 검증 통과"

            # 1단계 원천 지원 목록 및 현재 설정
            avail_sources = cls.PROVIDER_RAW_CAPABILITIES.get(primary_src.upper(), ["TICK", "1M", "1D"])
            ingestion_sources = info.get("ingestion_sources")
            if not ingestion_sources:
                ingest_res = info.get("ingestion_resolution", "1M")
                if primary_src.upper() == "CREON":
                    ingestion_sources = ["TICK", "1M", "1D"]
                elif primary_src.upper() in ("UPBIT", "BINANCE", "COINBASE"):
                    ingestion_sources = ["1M", "1D"]
                else:
                    ingestion_sources = ["TICK", "1M"] if ingest_res == "TICK" else [ingest_res]

            # 2단계 타겟 스케일
            target_scales = info.get("target_scales") or info.get("canonical_intervals") or ["1m"]

            results.append({
                "symbol": symbol,
                "name": info.get("name", symbol),
                "category": category,
                "role": role,
                "primary_source": primary_src,
                "primary_type": info.get("primary_type", ""),
                "fallback_source": info.get("fallback_source", ""),
                "fallback_type": info.get("fallback_type", ""),
                "ingestion_resolution": info.get("ingestion_resolution", "1M"),
                "ingestion_sources": ingestion_sources,
                "available_raw_sources": avail_sources,
                "canonical_intervals": target_scales,
                "target_scales": target_scales,
                "is_active": is_active,
                "archived": bool(info.get("archived", False)),
                "description": info.get("description", ""),
                "file_exists": raw_stat["exists"],
                "file_path": raw_stat["file_path"],
                "file_size": raw_stat["file_size"],
                "file_size_mb": round(raw_stat["file_size"] / (1024 * 1024), 2) if raw_stat["file_size"] else 0.0,
                "rows": raw_stat["rows"],
                "manifest_verified": raw_stat["manifest_exists"],
                "last_timestamp": raw_stat["last_timestamp"] or "-",
                "trading_days": raw_stat["trading_days"],
                "status_code": status_code,
                "status_badge": status_badge,
                "status_desc": status_desc,
                "live_connection": info.get("live_connection") or cls.build_default_live_connection(symbol, category, role, primary_src),
            })

        return results

    @classmethod
    @_LOCK
    def register_asset(
        cls,
        symbol: str,
        name: str,
        category: str = "KRX_STOCK",
        role: str = "leading_stock",
        primary_source: str = "CREON",
        primary_type: str = "TICK_AND_1M",
        fallback_source: str = "KIS",
        fallback_type: str = "1M",
        description: str = "",
        is_active: bool = True,
        ingestion_resolution: str = "1M",
        canonical_intervals: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """새로운 자산을 수집 대상 대장에 등록"""
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)
        cls._ensure_in_ledger(clean_sym, {"name": name, "category": category, "role": role, "description": description})

        if canonical_intervals is None:
            canonical_intervals = ["10s", "1m"] if ingestion_resolution == "TICK" else ["1m"]
        elif "10s" in canonical_intervals:
            ingestion_resolution = "TICK"

        mappings[clean_sym] = {
            "symbol": clean_sym,
            "name": name.strip(),
            "category": category,
            "role": role,
            "primary_source": primary_source,
            "primary_type": primary_type,
            "fallback_source": fallback_source,
            "fallback_type": fallback_type,
            "ingestion_resolution": ingestion_resolution,
            "canonical_intervals": canonical_intervals,
            "is_active": is_active,
            "description": description.strip()
        }

        success = cls.save_all_mappings(mappings)
        return {
            "status": "ok" if success else "error",
            "message": f"자산 {name}({clean_sym})이 수집 대상 대장에 성공적으로 등록되었습니다." if success else "저장 실패",
            "asset": mappings[clean_sym]
        }

    @classmethod
    @_LOCK
    def toggle_asset_active(cls, symbol: str, is_active: Optional[bool] = None) -> Dict[str, Any]:
        """자산의 수집 구독 활성/비활성 토글"""
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)

        if clean_sym not in mappings:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {clean_sym}"}

        cur_val = mappings[clean_sym].get("is_active", True)
        new_val = (not cur_val) if is_active is None else bool(is_active)
        mappings[clean_sym]["is_active"] = new_val

        success = cls.save_all_mappings(mappings)
        return {
            "status": "ok" if success else "error",
            "symbol": clean_sym,
            "is_active": new_val,
            "message": f"{mappings[clean_sym]['name']}({clean_sym}) 수집이 {'활성화' if new_val else '일시정지'}되었습니다."
        }

    @classmethod
    def get_ingestion_sources(cls, symbol: str) -> List[str]:
        """자산의 원천 수집 대상 스케일(TICK, 1M, 1D 등) 목록 조회"""
        clean_sym = cls.clean_symbol(symbol)
        mappings = cls.load_all_mappings()
        info = mappings.get(clean_sym) or mappings.get(symbol) or {}
        sources = info.get("ingestion_sources")
        if sources:
            return [str(s).upper() for s in sources]
        primary_src = str(info.get("primary_source", "CREON")).upper()
        if primary_src == "CREON":
            return ["TICK", "1M", "1D"]
        ingest_res = info.get("ingestion_resolution", "1M")
        return ["TICK", "1M"] if ingest_res == "TICK" else [ingest_res]

    @classmethod
    def get_provider_capabilities(cls) -> Dict[str, Any]:
        """1단계 제공처별 실제 원천 스케일 및 2단계 퀀트 표준 스펙 반환"""
        return {
            "provider_raw_capabilities": cls.PROVIDER_RAW_CAPABILITIES,
            "quant_core_standards": cls.QUANT_CORE_STANDARDS,
            "quant_full_spectrum": cls.QUANT_FULL_SPECTRUM
        }

    @classmethod
    @_LOCK
    def update_ingestion_sources(cls, symbol: str, sources: List[str]) -> Dict[str, Any]:
        """1단계: 원천 데이터 수집 대상 스케일(ingestion_sources) 복수 설정 갱신"""
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)

        if clean_sym not in mappings:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {clean_sym}"}

        primary_src = mappings[clean_sym].get("primary_source", "CREON").upper()
        supported = cls.PROVIDER_RAW_CAPABILITIES.get(primary_src, ["TICK", "1M", "1D"])

        cleaned = [s.strip().upper() for s in sources if s.strip().upper() in supported]
        if not cleaned:
            return {"status": "error", "message": f"제공처({primary_src})가 지원하는 원천 스케일({', '.join(supported)}) 중 최소 하나를 선택해야 합니다."}

        mappings[clean_sym]["ingestion_sources"] = cleaned
        # 하위 호환을 위한 ingestion_resolution 동기화 (우선순위: TICK > 1S > 1M > 5M > 1D)
        for res_cand in ["TICK", "1S", "1M", "5M", "1D"]:
            if res_cand in cleaned:
                mappings[clean_sym]["ingestion_resolution"] = res_cand
                break

        success = cls.save_all_mappings(mappings)
        return {
            "status": "ok" if success else "error",
            "symbol": clean_sym,
            "ingestion_sources": cleaned,
            "message": f"{mappings[clean_sym]['name']}({clean_sym})의 원천 수집 스케일이 업데이트되었습니다: {', '.join(cleaned)}" if success else "저장 실패"
        }

    @classmethod
    def prune_unselected_normalized_files(cls, symbol: str, target_scales: List[str]) -> List[str]:
        """
        2단계 정규화 파생 Parquet 정리:
        주어진 심볼에 대해 target_scales에 포함되지 않은 미선택/미사용 파생 파일 및 매니페스트를 안전하게 삭제.
        (1단계 원천 데이터 DataVault/raw/는 절대 건드리지 않음)
        """
        from shared.data_library import paths as dpaths
        clean_sym = cls.clean_symbol(symbol)
        norm_root = dpaths.normalized_root()
        if not os.path.exists(norm_root):
            return []

        # 정규화 파일은 원장 심볼 키로 이름 짓는다 — 다른 이름(옛 별칭)은 인정하지 않는다
        alias_set = {clean_sym.upper()}
        target_scales_lower = set(s.lower() for s in target_scales)
        deleted_files = []

        for root, _, files in os.walk(norm_root):
            for f in files:
                if not f.endswith(".parquet"):
                    continue
                base = f[:-8]  # strip '.parquet'
                parts = base.rsplit("_", 1)
                if len(parts) != 2:
                    continue
                f_sym, f_intv = parts[0].upper(), parts[1].lower()

                if f_sym in alias_set and f_intv not in target_scales_lower:
                    pq_file = os.path.join(root, f)
                    mf_file = f"{pq_file}.manifest.json"
                    try:
                        if os.path.exists(pq_file):
                            os.remove(pq_file)
                            deleted_files.append(pq_file)
                        if os.path.exists(mf_file):
                            os.remove(mf_file)
                            deleted_files.append(mf_file)
                        logger.info(f"[SourceMappingManager] 미체크 정규화 파생 파일 자동 정리: {pq_file}")
                    except Exception as e:
                        logger.warning(f"[SourceMappingManager] 파생 파일 정리 실패 ({pq_file}): {e}")

        return deleted_files

    @classmethod
    @_LOCK
    def update_target_scales(cls, symbol: str, scales: List[str], prune_unselected: bool = True) -> Dict[str, Any]:
        """2단계: 정규화 목표 캔들 스케일(target_scales) 설정 갱신 및 미선택 파생 파일 자동 정리"""
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)

        if clean_sym not in mappings:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {clean_sym}"}

        order_map = {k: idx for idx, k in enumerate(cls.QUANT_FULL_SPECTRUM)}
        cleaned = [s.strip().lower() for s in scales if s.strip().lower() in order_map]
        cleaned = sorted(list(dict.fromkeys(cleaned)), key=lambda x: order_map.get(x, 99))

        if not cleaned:
            return {"status": "error", "message": "최소 하나 이상의 유효한 목표 봉 스케일을 선택해야 합니다."}

        mappings[clean_sym]["target_scales"] = cleaned
        mappings[clean_sym]["canonical_intervals"] = cleaned  # 100% 하위 호환

        # 초단기 봉(1s, 5s, 10s 등)이 포함된 경우 1단계 원천 수집에 TICK 또는 1S가 없으면 지능적 자동 승격
        if any(s in ["1s", "5s", "10s", "30s"] for s in cleaned):
            curr_sources = mappings[clean_sym].get("ingestion_sources", [])
            primary_src = mappings[clean_sym].get("primary_source", "CREON").upper()
            supported = cls.PROVIDER_RAW_CAPABILITIES.get(primary_src, ["TICK", "1M", "1D"])
            add_src = "TICK" if "TICK" in supported else ("1S" if "1S" in supported else None)
            if add_src and add_src not in curr_sources:
                curr_sources.append(add_src)
                mappings[clean_sym]["ingestion_sources"] = curr_sources
            # 하위 호환을 위한 ingestion_resolution 동기화
            for res_cand in ["TICK", "1S", "1M", "5M", "1D"]:
                if res_cand in curr_sources:
                    mappings[clean_sym]["ingestion_resolution"] = res_cand
                    break

        success = cls.save_all_mappings(mappings)

        # 2단계 미선택 파생 파일 자동 정리 (Auto-Pruning)
        pruned_files = []
        if success and prune_unselected:
            pruned_files = cls.prune_unselected_normalized_files(clean_sym, cleaned)

        prune_msg = f" (미사용 파생 파일 {len(pruned_files)}개 자동 정리)" if pruned_files else ""
        return {
            "status": "ok" if success else "error",
            "symbol": clean_sym,
            "target_scales": cleaned,
            "canonical_intervals": cleaned,
            "pruned_files": pruned_files,
            "message": f"{mappings[clean_sym]['name']}({clean_sym})의 정규화 목표 스케일이 업데이트되었습니다: {', '.join(cleaned)}{prune_msg}" if success else "저장 실패"
        }

    @classmethod
    @_LOCK
    def update_canonical_intervals(cls, symbol: str, intervals: List[str], prune_unselected: bool = False) -> Dict[str, Any]:
        """하위 호환성을 위한 래퍼 메서드 (기본 prune_unselected=False로 호출자의 의도치 않은 파일 삭제 방지)"""
        return cls.update_target_scales(symbol, intervals, prune_unselected=prune_unselected)

    @classmethod
    @_LOCK
    def batch_update_ingestion_sources(
        cls,
        sources: List[str],
        symbols: Optional[List[str]] = None,
        provider: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        1단계: 복수 자산의 원천 수집 스케일을 일괄 원자적 갱신
        - symbols가 제공되면 해당 심볼들만 갱신
        - provider가 제공되면 해당 primary_source를 가진 모든 자산 갱신
        - 둘 다 없거나 provider == 'ALL'이면 등록된 전체 자산 갱신
        (각 자산별로 해당 제공처가 지원하는 가용 스케일만 필터링하여 안전하게 적용)
        """
        mappings = cls.load_all_mappings(force_refresh=True)
        target_keys = []
        if symbols:
            target_keys = [s.strip().upper() for s in symbols if s.strip().upper() in mappings]
        elif provider and provider.upper() != "ALL":
            p_target = provider.strip().upper()
            target_keys = [k for k, v in mappings.items() if v.get("primary_source", "").upper() == p_target]
        else:
            target_keys = list(mappings.keys())

        if not target_keys:
            return {"status": "error", "message": "업데이트할 대상 자산이 없습니다."}

        updated_count = 0
        for sym in target_keys:
            info = mappings[sym]
            p_src = info.get("primary_source", "CREON").upper()
            supported = cls.PROVIDER_RAW_CAPABILITIES.get(p_src, ["TICK", "1M", "1D"])
            cleaned = [s.strip().upper() for s in sources if s.strip().upper() in supported]
            if not cleaned:
                continue
            info["ingestion_sources"] = cleaned
            for res_cand in ["TICK", "1S", "1M", "5M", "1D"]:
                if res_cand in cleaned:
                    info["ingestion_resolution"] = res_cand
                    break
            updated_count += 1

        success = cls.save_all_mappings(mappings)
        return {
            "status": "ok" if success else "error",
            "updated_count": updated_count,
            "target_symbols": target_keys,
            "message": f"총 {updated_count}개 자산의 원천 수집 스케일이 일괄 업데이트되었습니다." if success else "저장 실패"
        }

    @classmethod
    @_LOCK
    def batch_update_target_scales(
        cls,
        scales: List[str],
        symbols: Optional[List[str]] = None,
        apply_to_all: bool = False,
        prune_unselected: bool = True
    ) -> Dict[str, Any]:
        """
        2단계: 복수 자산의 정규화 목표 캔들 스케일을 일괄 원자적 갱신 및 미선택 파생 파일 자동 정리
        - symbols가 제공되면 해당 심볼들만 갱신
        - apply_to_all이 True이면 등록된 전체 자산 갱신
        - 초단기 봉(1s, 5s, 10s 등) 포함 시 원천에 TICK 또는 1S가 없으면 지능적 자동 승격
        - prune_unselected가 True이면 각 자산별 미선택 파생 Parquet 및 매니페스트 즉시 삭제
        """
        mappings = cls.load_all_mappings(force_refresh=True)
        target_keys = []
        if apply_to_all or not symbols:
            target_keys = list(mappings.keys())
        else:
            target_keys = [s.strip().upper() for s in symbols if s.strip().upper() in mappings]

        if not target_keys:
            return {"status": "error", "message": "업데이트할 대상 자산이 없습니다."}

        order_map = {k: idx for idx, k in enumerate(cls.QUANT_FULL_SPECTRUM)}
        cleaned = [s.strip().lower() for s in scales if s.strip().lower() in order_map]
        cleaned = sorted(list(dict.fromkeys(cleaned)), key=lambda x: order_map.get(x, 99))

        if not cleaned:
            return {"status": "error", "message": "최소 하나 이상의 유효한 목표 봉 스케일을 선택해야 합니다."}

        updated_count = 0
        has_subminute = any(s in ["1s", "5s", "10s", "30s"] for s in cleaned)

        for sym in target_keys:
            info = mappings[sym]
            info["target_scales"] = list(cleaned)
            info["canonical_intervals"] = list(cleaned)

            if has_subminute:
                curr_sources = info.get("ingestion_sources", [])
                primary_src = info.get("primary_source", "CREON").upper()
                supported = cls.PROVIDER_RAW_CAPABILITIES.get(primary_src, ["TICK", "1M", "1D"])
                add_src = "TICK" if "TICK" in supported else ("1S" if "1S" in supported else None)
                if add_src and add_src not in curr_sources:
                    curr_sources.append(add_src)
                    info["ingestion_sources"] = curr_sources
                for res_cand in ["TICK", "1S", "1M", "5M", "1D"]:
                    if res_cand in curr_sources:
                        info["ingestion_resolution"] = res_cand
                        break
            updated_count += 1

        success = cls.save_all_mappings(mappings)

        # 각 자산별 미선택 파생 파일 일괄 정리
        total_pruned = 0
        if success and prune_unselected:
            for sym in target_keys:
                pruned = cls.prune_unselected_normalized_files(sym, cleaned)
                total_pruned += len(pruned)

        prune_msg = f" (미사용 파생 파일 {total_pruned}개 자동 정리)" if total_pruned else ""
        return {
            "status": "ok" if success else "error",
            "updated_count": updated_count,
            "target_symbols": target_keys,
            "target_scales": cleaned,
            "pruned_count": total_pruned,
            "message": f"총 {updated_count}개 자산의 정규화 목표 스케일이 일괄 업데이트되었습니다: {', '.join(cleaned)}{prune_msg}" if success else "저장 실패"
        }

    @classmethod
    def _normalize_pipeline_scales(cls, info: Dict[str, Any], ingestion_sources: Optional[List[str]],
                                   target_scales: Optional[List[str]]) -> Dict[str, Any]:
        """1단계 원천 + 2단계 캔들 입력을 제공처 능력과 퀀트 스펙트럼 기준으로 정규화한다.

        초단기 봉(1s/5s/10s/30s)을 고르면 원천에 TICK(또는 1S)이 있어야 리샘플링이 성립하므로
        누락 시 지능적으로 자동 보강한다 — update_target_scales 와 동일한 규칙.
        """
        primary_src = str(info.get("primary_source", "CREON")).upper()
        supported = cls.PROVIDER_RAW_CAPABILITIES.get(primary_src, ["TICK", "1M", "1D"])

        srcs = [str(x).strip().upper() for x in (ingestion_sources or [])]
        srcs = list(dict.fromkeys([x for x in srcs if x in supported]))

        order_map = {k: idx for idx, k in enumerate(cls.QUANT_FULL_SPECTRUM)}
        scales = [str(x).strip().lower() for x in (target_scales or [])]
        scales = sorted(dict.fromkeys([x for x in scales if x in order_map]), key=lambda x: order_map.get(x, 99))

        if any(x in ("1s", "5s", "10s", "30s") for x in scales):
            add_src = "TICK" if "TICK" in supported else ("1S" if "1S" in supported else None)
            if add_src and add_src not in srcs:
                srcs.append(add_src)

        return {"sources": srcs, "scales": scales, "supported": supported, "primary_source": primary_src}

    @classmethod
    def _assign_pipeline_scales(cls, info: Dict[str, Any], srcs: List[str], scales: List[str]) -> None:
        """정규화된 원천/스케일을 자산 항목에 반영 (하위 호환 필드 동기화 포함)"""
        info["ingestion_sources"] = list(srcs)
        info["target_scales"] = list(scales)
        info["canonical_intervals"] = list(scales)  # 100% 하위 호환
        for res_cand in ("TICK", "1S", "1M", "5M", "1D"):
            if res_cand in srcs:
                info["ingestion_resolution"] = res_cand
                break

    @classmethod
    @_LOCK
    def update_pipeline_scale_mapping(cls, symbol: str, ingestion_sources: List[str],
                                      target_scales: List[str],
                                      prune_unselected: bool = True) -> Dict[str, Any]:
        """1단계 원천(ingestion_sources) ↔ 2단계 캔들(target_scales) 인과 맵핑을 한 번의 저장으로 원자적 갱신.

        update_ingestion_sources 와 update_target_scales 를 잇달아 호출하면 저장이 두 번 일어나 중간 상태가
        원장에 관측된다(초단위 타겟이 먼저 들어가 TICK 없는 조합이 남는 등). 파이프라인 인과 관계는 한 트랜잭션으로 바꾼다.
        """
        clean_sym = str(symbol).strip().upper()
        mappings = cls.load_all_mappings(force_refresh=True)
        if clean_sym not in mappings:
            return {"status": "error", "message": f"등록되지 않은 자산입니다: {clean_sym}"}

        info = mappings[clean_sym]
        norm = cls._normalize_pipeline_scales(info, ingestion_sources, target_scales)
        if not norm["scales"]:
            return {"status": "error", "message": "최소 하나 이상의 유효한 목표 봉 스케일을 선택해야 합니다."}
        if not norm["sources"]:
            return {"status": "error",
                    "message": f"제공처({norm['primary_source']})가 지원하는 원천 스케일"
                               f"({', '.join(norm['supported'])}) 중 최소 하나를 선택해야 합니다."}

        cls._assign_pipeline_scales(info, norm["sources"], norm["scales"])
        success = cls.save_all_mappings(mappings)

        pruned_files = []
        if success and prune_unselected:
            pruned_files = cls.prune_unselected_normalized_files(clean_sym, norm["scales"])

        prune_msg = f" (미사용 파생 파일 {len(pruned_files)}개 자동 정리)" if pruned_files else ""
        return {
            "status": "ok" if success else "error",
            "symbol": clean_sym,
            "ingestion_sources": list(norm["sources"]),
            "target_scales": list(norm["scales"]),
            "canonical_intervals": list(norm["scales"]),
            "pruned_files": pruned_files,
            "message": (f"{info.get('name', clean_sym)}({clean_sym}) 파이프라인 맵핑 갱신: "
                        f"원천 {', '.join(norm['sources'])} ➔ 캔들 {', '.join(norm['scales'])}{prune_msg}")
                       if success else "저장 실패"
        }

    @classmethod
    @_LOCK
    def batch_update_pipeline_scale_mapping(cls, ingestion_sources: List[str], target_scales: List[str],
                                            symbols: Optional[List[str]] = None,
                                            category: Optional[str] = None,
                                            provider: Optional[str] = None,
                                            apply_to_all: bool = False,
                                            prune_unselected: bool = True) -> Dict[str, Any]:
        """복수 자산의 파이프라인 인과 맵핑을 한 번의 저장으로 일괄 갱신.

        대상 선택은 symbols > category > provider > apply_to_all 우선순위. 원천은 제공처별 능력이 다르므로
        자산마다 개별 정규화한다(예: CME 는 TICK 이 없어 초단위 승격이 일어나지 않는다).
        """
        mappings = cls.load_all_mappings(force_refresh=True)

        if symbols:
            target_keys = [str(s).strip().upper() for s in symbols if str(s).strip().upper() in mappings]
        elif category:
            cat = str(category).strip().upper()
            target_keys = [k for k, v in mappings.items() if str(v.get("category", "")).upper() == cat]
        elif provider:
            prov = str(provider).strip().upper()
            target_keys = [k for k, v in mappings.items() if str(v.get("primary_source", "")).upper() == prov]
        elif apply_to_all:
            target_keys = list(mappings.keys())
        else:
            return {"status": "error", "message": "대상(symbols/category/provider/apply_to_all)을 지정해야 합니다."}

        if not target_keys:
            return {"status": "error", "message": "업데이트할 대상 자산이 없습니다."}

        updated, skipped = [], []
        for sym in target_keys:
            info = mappings[sym]
            norm = cls._normalize_pipeline_scales(info, ingestion_sources, target_scales)
            if not norm["scales"] or not norm["sources"]:
                skipped.append(sym)
                continue
            cls._assign_pipeline_scales(info, norm["sources"], norm["scales"])
            updated.append(sym)

        if not updated:
            return {"status": "error",
                    "message": "유효한 원천/캔들 조합을 가진 대상이 없습니다 (제공처 능력과 퀀트 스펙트럼을 확인하세요)."}

        success = cls.save_all_mappings(mappings)

        total_pruned = 0
        if success and prune_unselected:
            for sym in updated:
                total_pruned += len(cls.prune_unselected_normalized_files(sym, mappings[sym]["target_scales"]))

        skip_msg = f", 건너뜀 {len(skipped)}개" if skipped else ""
        prune_msg = f" (미사용 파생 파일 {total_pruned}개 자동 정리)" if total_pruned else ""
        return {
            "status": "ok" if success else "error",
            "updated_count": len(updated),
            "target_symbols": updated,
            "skipped_symbols": skipped,
            "ingestion_sources": [str(x).strip().upper() for x in (ingestion_sources or [])],
            "target_scales": list(mappings[updated[0]]["target_scales"]),
            "pruned_count": total_pruned,
            "message": f"총 {len(updated)}개 자산의 파이프라인 맵핑이 일괄 갱신되었습니다{skip_msg}{prune_msg}"
                       if success else "저장 실패"
        }

    @classmethod
    def prune_all_orphan_normalized_files(cls) -> Dict[str, Any]:
        """
        대장(source_mappings.json)의 target_scales에 등록되어 있지 않은
        모든 고아(Orphan) 정규화 파생 Parquet 및 매니페스트 일괄 검색 및 정리.
        """
        from shared.data_library import paths as dpaths
        mappings = cls.load_all_mappings(force_refresh=True)
        norm_root = dpaths.normalized_root()
        if not os.path.exists(norm_root):
            return {"status": "ok", "deleted_count": 0, "freed_mb": 0.0, "deleted_files": []}

        # 심볼(원장 키)별 활성 target_scales 매핑 빌드
        valid_map = {}
        for sym, info in mappings.items():
            clean_s = cls.clean_symbol(sym)
            scales = set(s.lower() for s in (info.get("target_scales") or info.get("canonical_intervals") or ["1m"]))
            valid_map[clean_s.upper()] = scales

        deleted_files = []
        total_freed_bytes = 0

        for root, _, files in os.walk(norm_root):
            for f in files:
                if not f.endswith(".parquet"):
                    continue
                pq_file = os.path.join(root, f)
                mf_file = f"{pq_file}.manifest.json"
                base = f[:-8]
                parts = base.rsplit("_", 1)
                if len(parts) != 2:
                    continue
                f_sym, f_intv = parts[0].upper(), parts[1].lower()

                is_valid = False
                if f_sym in valid_map and f_intv in valid_map[f_sym]:
                    is_valid = True

                if not is_valid:
                    try:
                        b_size = os.path.getsize(pq_file) if os.path.exists(pq_file) else 0
                        if os.path.exists(pq_file):
                            os.remove(pq_file)
                            deleted_files.append(pq_file)
                            total_freed_bytes += b_size
                        if os.path.exists(mf_file):
                            os.remove(mf_file)
                            deleted_files.append(mf_file)
                        logger.info(f"[SourceMappingManager] 고아 정규화 파일 정리: {pq_file} ({round(b_size/(1024*1024), 2)} MB)")
                    except Exception as e:
                        logger.warning(f"[SourceMappingManager] 고아 파일 정리 실패 ({pq_file}): {e}")

        freed_mb = round(total_freed_bytes / (1024 * 1024), 2)
        return {
            "status": "ok",
            "deleted_count": len(deleted_files),
            "freed_mb": freed_mb,
            "deleted_files": deleted_files,
            "message": f"총 {len(deleted_files)}개 고아 파생 파일 정리 완료 ({freed_mb} MB 공간 회수)"
        }

    @classmethod
    def ensure_asset_registered(
        cls,
        symbol: str,
        name: str,
        category: str = "GLOBAL_INDEX_CFD_PROXY",
        role: str = "macro_proxy",
        primary_source: str = "CTRADER",
        primary_type: str = "1M",
        fallback_source: str = "CME",
        fallback_type: str = "1M",
        description: str = "",
        is_active: bool = True
    ) -> bool:
        """대장에 이미 등록되어 있지 않다면 자동으로 원자적 등록 (Idempotent Hook)"""
        clean_sym = symbol.strip().upper()
        mappings = cls.load_all_mappings()
        if clean_sym in mappings:
            return True
        res = cls.register_asset(
            symbol=clean_sym,
            name=name,
            category=category,
            role=role,
            primary_source=primary_source,
            primary_type=primary_type,
            fallback_source=fallback_source,
            fallback_type=fallback_type,
            description=description,
            is_active=is_active
        )
        return res.get("status") == "ok"

