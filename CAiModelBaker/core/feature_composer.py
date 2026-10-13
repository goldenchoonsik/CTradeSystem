# -*- coding: utf-8 -*-
"""
CAiModelBaker - Feature Composer (온디맨드 시점 결합 엔진)
=========================================================
[메달리온 3단계 / Features Layer]
정규화된 단일 자산 Parquet(Normalized) 파일들을 필요한 시점에 인메모리로 결합(Point-in-Time Join)하여
학습 및 연구용 피처 데이터셋을 구성합니다.

핵심 설계 원칙:
1. 단일 괴물 CSV 덮어쓰기 완전 폐기 (인메모리 즉시 반환 + 세션별 독립 아티팩트 보관)
2. Lookahead-free 결합: pd.merge_asof(..., direction='backward')
3. Coverage Guard (가용 기간 가드):
   - 대장주(삼성전자 등) 미포함 시: 타겟 자산의 풀 데이터셋(예: KODEX 200 38거래일) 100% 활용
   - 대장주 포함 시: 공통 유효 거래일(31거래일)로 안전하게 클램핑하여 가짜 수평선/결측치 유입 원천 방어
"""

import os
import json
import hashlib
import logging
from typing import Optional, List, Dict, Any, Callable
import pandas as pd
import numpy as np

from shared.data_library import paths as dpaths
from shared.data_library.manifest_manager import ManifestManager
from shared.data_library.reference_staleness import (attach_reference, merge_asof_with_stale_cap, resolve_stale_cap,
                                                     NORMALIZED_FLOW_FIELDS, NORMALIZED_BAR_FIELDS)
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.config.config_manager import get_proxy_of, get_proxy_for, ref_prefix, ref_key, get_symbol_info


class FeatureComposer:
    """온디맨드 시점 결합 및 피처셋 구성 엔진"""

    def __init__(self, local_log: Optional[Callable[[str], None]] = None):
        self.log = local_log if local_log is not None else logging.info
        # 결합 중인 타겟 심볼 — 참조 정렬은 타겟의 봉 라벨 규약까지 알아야 계산된다.
        self._compose_target_symbol: Optional[str] = None

    @staticmethod
    def _split_ref_spec(spec, target_interval, prefer_target_interval=False, ref_interval=None):
        """참조 지정(심볼 또는 심볼:인터벌) -> (원장 심볼, 결합할 인터벌)"""
        if ":" in str(spec):
            clean_ref, explicit_intv = str(spec).split(":", 1)
            return SourceMappingManager.clean_symbol(clean_ref), explicit_intv.strip().lower()
        clean_ref = SourceMappingManager.clean_symbol(spec)
        if ref_interval:
            return clean_ref, ref_interval
        return clean_ref, (target_interval if prefer_target_interval else "1m")

    @staticmethod
    def _normalized_path(symbol: str, interval: str) -> Optional[str]:
        """종목의 Normalized Parquet 경로 (없으면 None). 조회 순서의 단일 정의 —
        interval 전용 디렉토리 -> 언더스코어 표기 -> macro 디렉토리(원장 키) -> CFD 대용의 proxy_of 실물 macro 파일."""
        clean_sym = SourceMappingManager.clean_symbol(symbol)
        candidates = [
            dpaths.normalized_path(f"candles_{interval}", f"{clean_sym}_{interval}.parquet"),
            dpaths.normalized_path(f"candles_{interval}", f"{clean_sym.replace('-', '_')}_{interval}.parquet"),
            dpaths.macro_normalized_path(clean_sym, interval),
        ]
        # 단축 심볼(BTC, ETH 등) 접두사 누락 시 대장 표준 심볼(KRW-BTC 등) 자동 탐색 보완
        if clean_sym in ("BTC", "ETH", "USDT", "XRP", "SOL", "DOGE", "ADA"):
            candidates.extend([
                dpaths.normalized_path(f"candles_{interval}", f"KRW-{clean_sym}_{interval}.parquet"),
                dpaths.normalized_path(f"candles_{interval}", f"{clean_sym}USDT_{interval}.parquet"),
            ])
        proxy = get_proxy_of(clean_sym)
        if proxy:
            candidates.append(dpaths.macro_normalized_path(proxy, interval))
        # 역방향: 실물(NQ 등) 파일이 없으면 원장이 선언한 CFD 대용으로 폴백한다.
        # UI 미리보기와 Gold 빌드가 같은 원장을 보게 해 화면/학습 데이터가 갈리지 않도록 한다.
        proxy_for = get_proxy_for(clean_sym)
        if proxy_for:
            candidates.extend([
                dpaths.normalized_path(f"candles_{interval}", f"{proxy_for}_{interval}.parquet"),
                dpaths.macro_normalized_path(proxy_for, interval),
            ])
        return next((p for p in candidates if os.path.exists(p)), None)

    def _coverage_of(self, clean_sym: str, interval: str) -> Dict[str, Any]:
        """참조/시리즈 한 종목의 가용 기간 진단. interval 파일이 없으면 1m 로 폴백한다."""
        path = self._normalized_path(clean_sym, interval)
        actual_intv = interval
        if path is None and interval != "1m":
            path = self._normalized_path(clean_sym, "1m")
            actual_intv = "1m"
        info = {
            "symbol": clean_sym, "interval": actual_intv, "trading_days": 0,
            "start_date": None, "end_date": None, "total_bars": 0, "available": False,
        }
        # 원장이 선언한 CFD 대용으로 채워졌다면 진단에 드러낸다 (실물 데이터로 오인 금지)
        proxy_for = get_proxy_for(clean_sym)
        if path is not None and proxy_for and os.path.basename(path).startswith(f"{proxy_for}_"):
            info["served_by"] = proxy_for
        if path is not None:
            df = pd.read_parquet(path, columns=["timestamp"])
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                dates = set(df["timestamp"].dt.date.unique())
                info.update(trading_days=len(dates), start_date=str(min(dates)), end_date=str(max(dates)),
                            total_bars=len(df), available=True, _dates=dates)
        return info

    def get_coverage_summary(
        self,
        target_symbol: str,
        target_interval: str = "10s",
        ref_symbols: Optional[List[str]] = None,
        macro_symbols: Optional[List[str]] = None,
        prefer_target_interval: bool = False,
        ref_interval: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        타겟 자산 및 참조 자산들의 가용 기간과 공통 유효 거래일을 진단합니다.
        """
        clean_target = SourceMappingManager.clean_symbol(target_symbol)
        summary: Dict[str, Any] = {
            "target": {
                "symbol": clean_target,
                "interval": target_interval,
                "trading_days": 0,
                "start_date": None,
                "end_date": None,
                "total_bars": 0,
                "available": False,
            },
            "references": {},
            "common_trading_days": 0,
            "common_start_date": None,
            "common_end_date": None,
        }

        # 1. 타겟 자산 Normalized 확인
        target_path = dpaths.normalized_path(f"candles_{target_interval}", f"{clean_target}_{target_interval}.parquet")
        target_dates = set()
        if os.path.exists(target_path):
            df_target = pd.read_parquet(target_path, columns=["timestamp"])
            if not df_target.empty:
                df_target["timestamp"] = pd.to_datetime(df_target["timestamp"])
                dates = df_target["timestamp"].dt.date.unique()
                target_dates = set(dates)
                summary["target"]["trading_days"] = len(dates)
                summary["target"]["start_date"] = str(min(dates))
                summary["target"]["end_date"] = str(max(dates))
                summary["target"]["total_bars"] = len(df_target)
                summary["target"]["available"] = True

        common_dates = set(target_dates)

        # 2. 참조 자산들 확인 (대장주: 공통 거래일 교집합에 참여)
        if ref_symbols:
            for sym in ref_symbols:
                clean_ref, target_ref_intv = self._split_ref_spec(sym, target_interval, prefer_target_interval, ref_interval)
                ref_info = self._coverage_of(clean_ref, target_ref_intv)
                if ref_info["available"]:
                    common_dates = common_dates.intersection(ref_info.pop("_dates"))
                else:
                    ref_info.pop("_dates", None)
                summary["references"][clean_ref] = ref_info

        # 3. 시리즈 자산 확인 (매크로 선물/CFD/환율 등 — 캘린더가 달라 교집합에는 참여하지 않는다)
        if macro_symbols:
            for mac in macro_symbols:
                mac_clean = SourceMappingManager.clean_symbol(mac)
                mac_info = self._coverage_of(mac_clean, "1m")
                mac_info.pop("_dates", None)
                summary["references"][mac_clean] = mac_info

        if common_dates:
            summary["common_trading_days"] = len(common_dates)
            summary["common_start_date"] = str(min(common_dates))
            summary["common_end_date"] = str(max(common_dates))
            # 집합 기반 날짜 필터용: compose()에서 날짜 범위(min~max)가 아닌 실제 교집합 날짜만 사용
            summary["_common_dates_set"] = common_dates


        return summary

    def load_normalized(self, symbol: str, interval: str, is_target: bool = False) -> pd.DataFrame:
        """단일 자산 Normalized Parquet을 로드합니다.
        
        Args:
            symbol: 자산 심볼
            interval: 캔들 주기
            is_target: 타겟 자산 여부 (True일 때만 레거시 target_*, upbit_* 어댑터 허용)
        """
        clean_sym = SourceMappingManager.clean_symbol(symbol)
        path = self._normalized_path(clean_sym, interval)
        if path is None:
            raise FileNotFoundError(f"[FeatureComposer] {clean_sym}({interval}) Normalized Parquet을 찾을 수 없습니다")

        proxy_for = get_proxy_for(clean_sym)
        if proxy_for and os.path.basename(path).startswith(f"{proxy_for}_"):
            self.log(f"  ⚠️ {clean_sym}({interval}) 실물 데이터가 없어 원장이 선언한 대용 {proxy_for} 로 채웁니다")

        df = pd.read_parquet(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).astype("datetime64[ns]")
        df = df.sort_values("timestamp").reset_index(drop=True)

        # [하위 호환성 어댑터] 오직 타겟(Base) 자산에 대해서만 필요 시 target_* 및 upbit_* 별칭 자동 보정 (참조/매크로 자산 오염 원천 차단)
        if is_target:
            if "close" in df.columns and "target_close" not in df.columns:
                df["target_close"] = df["close"]
            if "open" in df.columns and "target_open" not in df.columns:
                df["target_open"] = df["open"]
            if "high" in df.columns and "target_high" not in df.columns:
                df["target_high"] = df["high"]
            if "low" in df.columns and "target_low" not in df.columns:
                df["target_low"] = df["low"]
            if "volume" in df.columns and "target_volume" not in df.columns:
                df["target_volume"] = df["volume"]
            if "close" in df.columns and "upbit_close" not in df.columns:
                df["upbit_close"] = df["close"]
            if "volume" in df.columns and "upbit_volume" not in df.columns:
                df["upbit_volume"] = df["volume"]

        return df

    def _attach_reference(self, base_df: pd.DataFrame, clean_ref: str, target_interval: str,
                          relations_meta: Dict[str, Any]) -> pd.DataFrame:
        """참조/시리즈 한 종목을 Point-in-Time 결합한다 (컬럼: ref_{key}_*). 실패해도 나머지 결합은 계속한다."""
        try:
            # 1순위: 타겟과 동일한 인터벌로 먼저 로드 시도, 부재 시 1m fallback (참조 자산이므로 is_target=False)
            chosen_intv = target_interval
            try:
                ref_df = self.load_normalized(clean_ref, target_interval, is_target=False)
            except FileNotFoundError:
                chosen_intv = "1m"
                ref_df = self.load_normalized(clean_ref, "1m", is_target=False)

            ref_df["timestamp"] = pd.to_datetime(ref_df["timestamp"]).astype("datetime64[ns]")
            base_df["timestamp"] = pd.to_datetime(base_df["timestamp"]).astype("datetime64[ns]")

            _pfx = ref_prefix(clean_ref)
            # 🛡️ 유령 컬럼 방역: timestamp 및 참조 자산에 불필요한 target_*, upbit_* 접두사 컬럼은 절대 ref_로 매핑하지 않음
            ref_cols = {
                c: f"{_pfx}{c}" for c in ref_df.columns
                if c != "timestamp" and not c.startswith("target_") and not c.startswith("upbit_")
            }
            ref_df = ref_df.rename(columns=ref_cols)
            # 빈 행을 다루는 방식은 필드의 시간 의미가 정한다 (상태값/흐름값/봉 내부 비율값).
            # stale 상한은 자산군별 정책(T-3) — 종전 이 조립기는 상한 없이 결합해 원천이 며칠 비어도 마지막 값이
            # 무기한 이어졌고, 거래량은 빠진 봉 자리에 직전 봉 값이 복제됐다.
            _flow_cols = [ref_cols[c] for c in ref_cols if c in NORMALIZED_FLOW_FIELDS]
            _bar_cols = [ref_cols[c] for c in ref_cols if c in NORMALIZED_BAR_FIELDS]
            _cap = resolve_stale_cap(str(get_symbol_info(clean_ref).get("market_type", "")) or None, chosen_intv)
            # 참조 결합은 반드시 단일 진입점을 통과한다 — 타임스탬프 규약 보정을 호출자가 빠뜨릴 수 없게 한다.
            # (2026-09-17 사고: 이 자리에서 pd.merge_asof 를 직접 불러 정렬이 통째로 빠져 있었다)
            # 결합 대상은 이름을 붙인 ref_* 컬럼뿐이다. 이름을 바꾸지 않은 target_*/upbit_* 까지 넘기면
            # 타겟의 같은 이름 컬럼과 충돌해 타겟이 _x 로 밀려나고 참조 값이 그 자리를 차지한다.
            base_df = attach_reference(
                base_df, ref_df,
                ref_symbol=clean_ref, ref_interval=chosen_intv,
                target_symbol=self._compose_target_symbol or clean_ref,
                target_interval=target_interval,
                value_cols=list(ref_cols.values()),
                cap=_cap, flow_cols=_flow_cols, bar_cols=_bar_cols,
            )
            join_desc = f"1:1 NATIVE {target_interval}" if chosen_intv == target_interval else f"ASOF {chosen_intv}"
            self.log(f"  🔗 [Point-in-Time Join] {clean_ref}({chosen_intv}) 결합 완료 ({join_desc}, stale 상한 {_cap})")

            # [지표 엔진 호환성 어댑터] role 기반 표준 별칭 투영 (크립토 피처가 고정 컬럼명을 읽는다)
            r_role = str(relations_meta.get(clean_ref, {}).get("role", "")).lower()
            extra_cols = {}
            if r_role == "crypto_spot" or clean_ref == "BTCUSDT":
                for sub_c in ["close", "open", "high", "low", "volume", "taker_ratio", "quote_volume", "taker_quote_ratio", "trade_count"]:
                    ref_col = f"{_pfx}{sub_c}"
                    if ref_col in base_df.columns:
                        extra_cols[f"binance_spot_{sub_c}"] = base_df[ref_col]
            elif r_role == "crypto_futures" or "PERP" in clean_ref:
                for sub_c in ["oi", "oi_value", "funding_rate", "close", "volume"]:
                    for cand in [f"{_pfx}{sub_c}", f"{_pfx}binance_futures_{sub_c}", f"{_pfx}binance_{sub_c}"]:
                        if cand in base_df.columns:
                            extra_cols[f"binance_futures_{sub_c}"] = base_df[cand]
                            if sub_c in ["oi", "oi_value", "funding_rate"]:
                                extra_cols[f"binance_{sub_c}"] = base_df[cand]
                            break
            elif r_role == "crypto_currency" or "USDT" in clean_ref:
                for cand in [f"{_pfx}close", f"{_pfx}upbit_usdt_close"]:
                    if cand in base_df.columns:
                        extra_cols["crypto_usdt_close"] = base_df[cand]
                        break

            if extra_cols:
                base_df = pd.concat([base_df, pd.DataFrame(extra_cols, index=base_df.index)], axis=1)
        except Exception as e:
            self.log(f"  ⚠️ 참조 자산 {clean_ref} 결합 건너뜀: {e}")
        return base_df

    @staticmethod
    def _compute_cache_key(
        target_symbol: str,
        target_interval: str,
        ref_symbols: Optional[List[str]] = None,
        macro_symbols: Optional[List[str]] = None,
        mtf_intervals: Optional[List[str]] = None,
        align_to_common_dates: bool = True,
    ) -> str:
        """피처셋 합성 파라미터 기반 12자리 결정론적 SHA-256 해시 키 생성"""
        payload = {
            "target": str(target_symbol).upper(),
            "interval": str(target_interval).lower(),
            "refs": sorted(str(s).upper() for s in (ref_symbols or [])),
            "macro": sorted(str(s).lower() for s in (macro_symbols or [])),
            "mtf": sorted(str(s).lower() for s in (mtf_intervals or [])),
            "align": bool(align_to_common_dates),
        }
        raw_str = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw_str.encode("utf-8")).hexdigest()[:12]

    def synthesize_cross_asset_features(
        self,
        df: pd.DataFrame,
        target_symbol: str,
        ref_symbols: Optional[List[str]] = None,
        macro_symbols: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """
        타겟 자산과 참조/매크로 자산 간의 교차 팩터 자동 합성:
        1. cross_{ref}_spread: 타겟 대비 대장주 상대 가격 비율 (%)
        2. cross_{ref}_zscore: 스프레드 30바 롤링 Z-Score (평균회귀 알파)
        3. cross_{ref}_corr: 20바 롤링 상관계수 (Lead-Lag 동조화 측정)
        4. macro_basis_ratio: 선물 vs 현물 베이시스율 (%)

        입력(참조 가격)이 없는 행은 NaN 으로 둔다 — 0 은 '괴리 없음/상관 없음' 이라는 거짓 관측이다 (2026-10-02).
        """
        df = df.copy()
        target_close = df["close"] if "close" in df.columns else df.get("target_close")
        if target_close is None:
            return df

        # 1. 참조 대장주와의 교차 팩터
        if ref_symbols:
            for ref in ref_symbols:
                clean_ref = SourceMappingManager.clean_symbol(ref)
                ref_close_col = f"{ref_prefix(clean_ref)}close"
                if ref_close_col in df.columns:
                    _k = ref_key(clean_ref)
                    ref_close = df[ref_close_col]

                    # 스프레드 (%): ((target_close / ref_close) - 1.0) * 100
                    spread_col = f"cross_{_k}_spread"
                    spread = ((target_close / (ref_close.replace(0, np.nan) + 1e-8)) - 1.0) * 100.0
                    df[spread_col] = spread.astype(np.float32)

                    # 30바 롤링 Z-Score
                    zscore_col = f"cross_{_k}_zscore"
                    sp_mean = spread.rolling(30, min_periods=5).mean()
                    sp_std = spread.rolling(30, min_periods=5).std()
                    zscore = (spread - sp_mean) / (sp_std.replace(0, np.nan) + 1e-8)
                    df[zscore_col] = zscore.clip(-5.0, 5.0).astype(np.float32)

                    # 20바 롤링 수익률 상관계수
                    corr_col = f"cross_{_k}_corr"
                    ret_target = target_close.pct_change()
                    ret_ref = ref_close.pct_change()
                    corr = ret_target.rolling(20, min_periods=5).corr(ret_ref)
                    df[corr_col] = corr.clip(-1.0, 1.0).astype(np.float32)

        # 2. 선물-현물 베이시스율 — 결합된 참조/시리즈 중 원장 market_type 이 선물(KRX_FUTURES)과 지수(KRX_INDEX)인 쌍.
        #    컬럼 이름이나 별칭을 보지 않는다 (원장이 종목이 무엇인지를 알려준다).
        joined = [SourceMappingManager.clean_symbol(x) for x in list(ref_symbols or []) + list(macro_symbols or [])]
        fut_syms = [x for x in joined if get_symbol_info(x).get("market_type") == "KRX_FUTURES"]
        idx_syms = [x for x in joined if get_symbol_info(x).get("market_type") == "KRX_INDEX"]
        if fut_syms and idx_syms and f"{ref_prefix(fut_syms[0])}close" in df.columns and f"{ref_prefix(idx_syms[0])}close" in df.columns:
            f_close = df[f"{ref_prefix(fut_syms[0])}close"]
            s_close = df[f"{ref_prefix(idx_syms[0])}close"]
            basis = ((f_close - s_close) / (s_close.replace(0, np.nan) + 1e-8)) * 100.0
            df["macro_basis_ratio"] = basis.clip(-20.0, 20.0).astype(np.float32)

        return df

    @staticmethod
    def sanitize_post_join(df: pd.DataFrame) -> pd.DataFrame:
        """
        결합 후 정제 — **결측을 채우지 않는다.** 무한대(±inf)만 NaN 으로 바꾼다.

        저장 계층(Gold)은 관측된 사실만 담는다. 빈 칸을 무엇으로 볼지는 결합 단계가 필드의 시간 의미로 이미 정했다
        (`attach_reference`: 상태값은 stale 상한까지 이어 쓰기, 흐름값은 원천이 살아 있으면 0·밖이면 NaN).
        여기서 다시 채우면 그 판정을 덮어쓴다.

        [2026-10-02] 종전에는 컬럼 이름 규칙으로 거래량/비율/'그 외 수치 전부'를 fillna(0.0), 가격을 ffill 했다.
          - Silver 에서 NaN(수집 전)이던 BTCUSDT-PERP 미결제약정 90,076행이 Gold 에서 0 이 됐다 — '모름'이
            '미결제약정 0' 이라는 거짓 관측으로 바뀌어, 하류에서는 둘을 구별할 수 없었다.
          - 가격 ffill 은 stale 상한이 무효화한 칸을 다시 메워 상한을 무력화한다 (reference_staleness D-1 과 같은 구조).
        [T-6] 그 전에는 가격에 bfill 까지 걸어 미래 가격이 과거로 역류했다 (ARCHITECTURE.md PIT-1).
        """
        df = df.copy()
        num_cols = [c for c in df.columns if c != "timestamp" and pd.api.types.is_float_dtype(df[c])]
        if num_cols:
            df[num_cols] = df[num_cols].replace([np.inf, -np.inf], np.nan)
        return df

    def compose(
        self,
        target_symbol: str,
        target_interval: str = "10s",
        ref_symbols: Optional[List[str]] = None,
        macro_symbols: Optional[List[str]] = None,
        mtf_intervals: Optional[List[str]] = None,
        align_to_common_dates: bool = True,
        synthesize_cross_features: bool = True,
        session_id: Optional[str] = None,
        dataset_name: Optional[str] = None,
        save_preview: bool = False,
        use_cache: bool = False,
        prefer_target_interval: bool = False,
        ref_interval: Optional[str] = None,
        auto_bind_ssot: bool = False,
        **kwargs,
    ) -> pd.DataFrame:
        """
        타겟 자산에 상위 타임프레임(MTF), 참조 대장주, 매크로 자산을 Point-in-Time Join하고
        교차 팩터(Cross-Asset Features)를 자동 합성하여 완전한 피처셋을 반환합니다.
        """
        clean_target = SourceMappingManager.clean_symbol(target_symbol)
        self.log(f"🧩 [Feature Composer] {clean_target} ({target_interval}) 피처셋 구성 시작...")

        # 호환성 지원: save_snapshot 플래그를 넘겨받은 경우 save_preview로 매핑
        save_preview = save_preview or kwargs.get("save_snapshot", False)

        # 0-1. symbol_relations.json SSOT 메타 조회 (별칭 프로젝션용)
        relations_meta = {}
        try:
            from shared.data_library.symbol_relations_manager import SymbolRelationsManager
            for r in SymbolRelationsManager.get_relations_for_target(clean_target):
                r_sym = SourceMappingManager.clean_symbol(r.get("symbol", ""))
                relations_meta[r_sym] = r
        except Exception as e:
            self.log(f"  ⚠️ symbol_relations.json 조회 건너뜀: {e}")

        # SSOT 자동 바인딩 옵션이 켜져 있고 ref_symbols가 지정되지 않은 경우
        if ref_symbols is None and auto_bind_ssot and relations_meta:
            ref_symbols = list(relations_meta.keys())
            self.log(f"  🔗 [SSOT Auto-Bind] {clean_target} 기본 참조 종목 자동 연결: {ref_symbols}")

        # 0-2. Gold 캐시 확인
        cache_key = self._compute_cache_key(
            clean_target, target_interval, ref_symbols, macro_symbols, mtf_intervals, align_to_common_dates
        )
        cached_gold_path = dpaths.features_path(clean_target, "gold", f"{clean_target}_{target_interval}_{cache_key}.parquet")
        if use_cache and os.path.exists(cached_gold_path):
            self.log(f"  ⚡ [Gold Feature Cache 적중] {cached_gold_path}")
            df_cached = pd.read_parquet(cached_gold_path)
            df_cached["timestamp"] = pd.to_datetime(df_cached["timestamp"])
            return df_cached

        # 1. Base Target 로드
        base_df = self.load_normalized(clean_target, target_interval, is_target=True)
        self.log(f"  📌 Base 타겟 로드 완료: {len(base_df):,}행 ({base_df['timestamp'].dt.date.nunique()}거래일)")

        # 2. Coverage Guard 적용 (참조 자산 포함 시 날짜 클램핑)
        if (ref_symbols or macro_symbols) and align_to_common_dates:
            summary = self.get_coverage_summary(
                clean_target,
                target_interval,
                ref_symbols,
                macro_symbols,
                prefer_target_interval=prefer_target_interval,
                ref_interval=ref_interval,
            )
            common_td = summary.get("common_trading_days", 0)
            if common_td > 0:
                # 날짜 범위(min~max) 필터가 아닌 실제 교집합 날짜 집합 기반 필터 (비연속 갭 날짜 제외)
                common_dates_set = summary.get("_common_dates_set")
                before_cnt = len(base_df)
                if common_dates_set:
                    base_df = base_df[
                        base_df["timestamp"].dt.date.isin(common_dates_set)
                    ].reset_index(drop=True)
                else:
                    # fallback: 집합이 없는 경우 기존 범위 필터 사용
                    s_date = pd.Timestamp(summary["common_start_date"]).date()
                    e_date = pd.Timestamp(summary["common_end_date"]).date()
                    base_df = base_df[
                        (base_df["timestamp"].dt.date >= s_date) & (base_df["timestamp"].dt.date <= e_date)
                    ].reset_index(drop=True)
                s_date = summary.get("common_start_date", "?")
                e_date = summary.get("common_end_date", "?")
                self.log(f"  🛡️ [Coverage Guard] 공통 교집합({s_date} ~ {e_date}, {common_td}거래일) 클램핑: {before_cnt:,}행 ➔ {len(base_df):,}행")

            else:
                self.log("  ⚠️ [Coverage Guard] 공통 거래일이 0일입니다. 원본 Base 구간을 유지합니다.")

        # 3. 다중 타임프레임(MTF) 결합 (상위 분봉 캔들)
        if mtf_intervals:
            for mtf_intv in mtf_intervals:
                if mtf_intv == target_interval:
                    continue
                try:
                    mtf_df = self.load_normalized(clean_target, mtf_intv)
                    mtf_cols = {c: f"mtf_{mtf_intv}_{c}" for c in mtf_df.columns if c != "timestamp"}
                    mtf_df = mtf_df.rename(columns=mtf_cols)
                    base_df = pd.merge_asof(base_df, mtf_df, on="timestamp", direction="backward")
                    self.log(f"  🔗 [MTF Join] 상위 타임프레임 {mtf_intv} 결합 완료 ({len(mtf_cols)}개 컬럼)")
                except Exception as e:
                    self.log(f"  ⚠️ MTF 자산 {clean_target}({mtf_intv}) 결합 건너뜀: {e}")

        # 4. 참조 자산 결합 (대장주 — 공통 거래일 교집합에 참여한 종목)
        # 5. 시리즈 자산 결합 (매크로 선물/CFD/환율 등 — 자르지 않고 붙이기만 함)
        #    컬럼 이름은 분류와 무관하게 동일하다: config_manager.ref_prefix (별칭 없음, 이름 조립 지점은 한 곳).
        attached_keys = set()
        self._compose_target_symbol = clean_target
        for ref_sym in list(ref_symbols or []) + list(macro_symbols or []):
            clean_ref = self._split_ref_spec(ref_sym, target_interval)[0]
            if ref_key(clean_ref) in attached_keys:      # 같은 종목이 참조와 시리즈에 함께 들어와도 한 번만 붙인다
                continue
            attached_keys.add(ref_key(clean_ref))
            base_df = self._attach_reference(base_df, clean_ref, target_interval, relations_meta)

        # 6. 교차 자산 팩터 자동 합성 (Cross-Asset Features)
        if synthesize_cross_features:
            before_cols = len(base_df.columns)
            base_df = self.synthesize_cross_asset_features(base_df, clean_target, ref_symbols, macro_symbols)
            added_cross = len(base_df.columns) - before_cols
            if added_cross > 0:
                self.log(f"  ⚡ [Cross-Asset Synthesis] {added_cross}개 교차 팩터(스프레드/Z-Score/상관계수/베이시스) 합성 완료")

        # 7. 결합 후 정제 — 결측은 채우지 않는다 (무한대만 NaN)
        base_df = self.sanitize_post_join(base_df)

        # 불변식: 컬럼 이름 충돌(중복)은 학습/서빙 계약을 조용히 깨뜨린다(df["x"] 가 DataFrame 이 되거나 CSV 저장 후 x.1 로 변형).
        # 이름을 만드는 곳이 하나여야 한다는 원칙의 마지막 방어선 — 발견 즉시 실패시킨다.
        dup_cols = sorted(set(base_df.columns[base_df.columns.duplicated()]))
        if dup_cols:
            raise ValueError(f"[FeatureComposer] 중복 컬럼 {len(dup_cols)}개 — 컬럼 이름 조립이 충돌했습니다: {dup_cols[:10]}")

        # 8. Gold 캐시 저장 (옵션)
        if use_cache:
            os.makedirs(os.path.dirname(os.path.abspath(cached_gold_path)), exist_ok=True)
            base_df.to_parquet(cached_gold_path, index=False, engine="pyarrow", compression="snappy")
            ManifestManager.create_manifest(
                data_path=cached_gold_path,
                provider="feature_composer",
                data_tier="features",
                asset_type=f"gold_cache_{target_interval}",
                symbol=clean_target,
                notes=f"Gold Cache {cache_key} ({len(base_df)} rows)"
            )
            self.log(f"  💾 [Gold Feature Cache 저장] {cached_gold_path}")

        # 9. 데이터셋/세션 저장 처리 (선택적)
        if dataset_name:
            ds_file = dpaths.features_dataset_path(clean_target, f"{dataset_name}.parquet")
            os.makedirs(os.path.dirname(os.path.abspath(ds_file)), exist_ok=True)
            base_df.to_parquet(ds_file, index=False, engine="pyarrow", compression="snappy")
            self.log(f"  💾 [Dataset Features 저장] {ds_file} ({len(base_df):,}행)")
            ManifestManager.create_manifest(
                data_path=ds_file,
                provider="feature_composer",
                data_tier="features",
                asset_type=f"dataset_{target_interval}",
                symbol=clean_target,
                notes=f"Dataset {dataset_name} ({len(base_df)} rows)"
            )

        if session_id:
            session_out_dir = dpaths.features_path(clean_target, "sessions", session_id)
            os.makedirs(session_out_dir, exist_ok=True)
            session_file = os.path.join(session_out_dir, f"features_{target_interval}.parquet")
            base_df.to_parquet(session_file, index=False, engine="pyarrow", compression="snappy")
            self.log(f"  💾 [Session Features 저장] {session_file} ({len(base_df):,}행)")

            ManifestManager.create_manifest(
                data_path=session_file,
                provider="feature_composer",
                data_tier="features",
                asset_type=f"features_{target_interval}",
                symbol=clean_target,
                notes=f"Session {session_id} Features ({len(base_df)} rows)"
            )

        if save_preview:
            preview_file = dpaths.data_vault_preview_path(clean_target, target_interval)
            os.makedirs(os.path.dirname(os.path.abspath(preview_file)), exist_ok=True)
            base_df.to_csv(preview_file, index=False)
            self.log(f"  💾 [Preview CSV 동기화] {preview_file} ({len(base_df):,}행)")

            ManifestManager.create_manifest(
                data_path=preview_file,
                provider="feature_composer",
                data_tier="features",
                asset_type="preview_csv",
                symbol=clean_target,
                notes=f"Preview CSV for {clean_target} {target_interval} ({len(base_df)} rows)"
            )

        self.log(f"✅ [Feature Composer] 완성: 총 {len(base_df):,}행, {len(base_df.columns)}컬럼")
        return base_df

    def list_gold_inventory(self) -> List[Dict[str, Any]]:
        """
        DataVault/features에 저장된 Gold Feature 데이터셋 및 세션 아티팩트 목록 조회
        """
        feat_root = dpaths.features_root()
        inventory = []
        if not os.path.exists(feat_root):
            return inventory

        for root, dirs, files in os.walk(feat_root):
            for f in files:
                if f.endswith(".parquet") and not f.endswith(".tmp.parquet"):
                    p_path = os.path.join(root, f)
                    rel_p = os.path.relpath(p_path, feat_root)
                    size_mb = round(os.path.getsize(p_path) / (1024 * 1024), 2)
                    mtime = os.path.getmtime(p_path)
                    updated_at = pd.Timestamp(mtime, unit="s", tz="Asia/Seoul").strftime("%Y-%m-%d %H:%M:%S")

                    # 매니페스트 확인
                    manifest_data = ManifestManager.load_manifest(p_path)
                    total_rows = 0
                    null_ratio = 0.0
                    col_count = 0
                    if manifest_data and "health_summary" in manifest_data:
                        hs = manifest_data["health_summary"]
                        total_rows = hs.get("total_bars", 0)
                        null_ratio = hs.get("null_ratio", 0.0)
                    elif manifest_data and "row_count" in manifest_data:
                        total_rows = manifest_data.get("row_count", 0)

                    # 간단 메타데이터 파싱
                    parts = rel_p.split(os.sep)
                    sym = parts[0] if len(parts) > 0 else "UNKNOWN"
                    inv_item = {
                        "symbol": sym,
                        "relative_path": rel_p,
                        "file_name": f,
                        "size_mb": size_mb,
                        "total_rows": total_rows,
                        "null_ratio": null_ratio,
                        "updated_at": updated_at,
                        "has_manifest": manifest_data is not None,
                    }
                    inventory.append(inv_item)

        inventory.sort(key=lambda x: x["updated_at"], reverse=True)
        return inventory

