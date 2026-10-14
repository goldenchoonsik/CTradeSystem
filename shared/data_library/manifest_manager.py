# -*- coding: utf-8 -*-
"""
CTradeSystem - Manifest Manager (원천/정제 데이터 사이드카 매니페스트 거버넌스)
==============================================================================
규약:
1. 모든 Bronze/Silver/Gold 데이터 파일 본체 옆에 1:1로 `<filename>.manifest.json`을 보관한다.
2. 매니페스트에는 파일 SHA256 해시, 크기, 행수, 타임존, 기간, KRX 세션 완결성(381분) 등이 불변 박제된다.
3. 데이터 적재 시 0.01초 내 크기/해시 무결성을 검증하여 무단 변조 및 불완전 데이터 로드를 원천 차단한다.
"""

import hashlib
import json
import os
import re
import csv
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple

try:
    import pandas as pd
except ImportError:
    pd = None

KST = timezone(timedelta(hours=9))


class ManifestManager:
    """데이터 파일과 1:1 동반되는 사이드카 매니페스트 생성 및 검증 엔진."""

    @staticmethod
    def get_manifest_path(data_path: str) -> str:
        """데이터 파일에 대응하는 사이드카 매니페스트 경로를 반환한다."""
        return f"{os.path.abspath(data_path)}.manifest.json"

    @classmethod
    def calculate_sha256(cls, file_path: str, chunk_size: int = 1024 * 1024) -> str:
        """대용량 파일을 청크 단위로 읽어 SHA256 해시를 계산한다."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest()

    # KRX 정규장 분 단위 완결성(하루 380분 이상) 검사는 분봉·틱처럼 하루에 분이 많이 찍히는 데이터에만 의미가 있다.
    # 일봉(하루 1분)·5분봉(하루 76분)에 적용하면 모든 날이 '이상'으로 기록된다 — 2026-10-15 실측: 삼성전자 일봉 매니페스트
    # 324KB 중 236KB가 1980년부터 전 거래일 '1m' 이상 목록이었다. 하루 고유 분 수의 중앙값이 이 값 미만이면 검사 대상이 아니다.
    MINUTE_CHECK_MIN_MEDIAN = 300

    @classmethod
    def _krx_minute_metrics(cls, market: str, mins_per_day: Dict[str, int]) -> Dict[str, Any]:
        trading_days = len(mins_per_day)
        counts = sorted(mins_per_day.values())
        min_mins = counts[0] if counts else 0
        median = counts[len(counts) // 2] if counts else 0
        out = {"market": market.upper(), "trading_days": trading_days, "min_minutes_per_day": min_mins}
        if market.upper() != "KRX" or median < cls.MINUTE_CHECK_MIN_MEDIAN:
            out.update({"applicable": False, "fully_intact": None, "anomaly_dates": [],
                        "reason": "KRX 시장이 아님" if market.upper() != "KRX"
                        else f"분 단위 데이터가 아님(하루 고유 분 중앙값 {median}) — 380분 완결성 검사 대상 아님"})
            return out
        anomalies = [f"{d} ({c}m)" for d, c in mins_per_day.items() if c < 380]
        out.update({"applicable": True, "fully_intact": (min_mins >= 380 and not anomalies), "anomaly_dates": anomalies})
        return out

    @classmethod
    def inspect_dataset_metrics(
        cls, file_path: str, market: str = "KRX"
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """데이터셋의 행수, 시간대 범위, KRX 거래시간(381분) 완결성을 측정한다."""
        ext = os.path.splitext(file_path)[1].lower()
        
        # 1. Pandas 가용 시
        if pd is not None:
            if ext == ".csv":
                df = pd.read_csv(file_path)
            elif ext in [".parquet", ".pq"]:
                df = pd.read_parquet(file_path)
            else:
                size = os.path.getsize(file_path)
                return {
                    "byte_size": size,
                    "row_count": None,
                    "first_timestamp": None,
                    "last_timestamp": None,
                }, {"market": market, "fully_intact": None}

            row_count = len(df)
            time_col = None
            for cand in ["datetime", "date", "timestamp", "time", "일자"]:
                if cand in df.columns:
                    time_col = cand
                    break

            first_ts = None
            last_ts = None
            krx_metrics = {
                "market": market,
                "trading_days": 0,
                "min_minutes_per_day": 0,
                "fully_intact": False,
                "anomaly_dates": [],
            }

            if time_col is not None and not df.empty:
                ts_series = df[time_col].astype(str)
                first_ts = ts_series.iloc[0]
                last_ts = ts_series.iloc[-1]

                try:
                    dt_series = pd.to_datetime(ts_series)
                    unique_mins = dt_series.dt.floor("min").groupby(dt_series.dt.date).nunique()
                    krx_metrics = cls._krx_minute_metrics(market, {str(d): int(c) for d, c in unique_mins.items()})
                except Exception as e:
                    krx_metrics["error"] = str(e)

            # 헬스 메트릭 (Parquet 및 고도화 감사용)
            null_ratio = float(df.isnull().mean().mean()) if not df.empty else 0.0
            close_col = "close" if "close" in df.columns else ("upbit_close" if "upbit_close" in df.columns else None)
            price_range = [float(df[close_col].min()), float(df[close_col].max())] if close_col and not df.empty else None
            has_micro = all(c in df.columns for c in ["vwap", "ofi", "rv"])

            integrity_metrics = {
                "byte_size": os.path.getsize(file_path),
                "row_count": row_count,
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
                "null_ratio": round(null_ratio, 6),
                "price_range": price_range,
                "has_microstructure": has_micro,
            }
            return integrity_metrics, krx_metrics

        # 2. Pure Python CSV 폴백 (초경량 32-bit 지원)
        row_count = 0
        first_ts = None
        last_ts = None
        unique_minutes_by_day = {}

        if ext == ".csv":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.reader(f)
                headers = next(reader, [])
                time_idx = 0
                for cand in ["datetime", "date", "timestamp", "time", "일자"]:
                    if cand in headers:
                        time_idx = headers.index(cand)
                        break

                for row in reader:
                    if not row:
                        continue
                    row_count += 1
                    ts_val = row[time_idx].strip()
                    if first_ts is None:
                        first_ts = ts_val
                    last_ts = ts_val

                    if len(ts_val) >= 16:
                        # "YYYY-MM-DD HH:MM" 추출
                        day = ts_val[:10]
                        minute = ts_val[:16]
                        if day not in unique_minutes_by_day:
                            unique_minutes_by_day[day] = set()
                        unique_minutes_by_day[day].add(minute)

        krx_metrics = cls._krx_minute_metrics(market, {d: len(s) for d, s in unique_minutes_by_day.items()})

        integrity_metrics = {
            "byte_size": os.path.getsize(file_path),
            "row_count": row_count,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
        }
        return integrity_metrics, krx_metrics

    @classmethod
    def create_manifest(
        cls,
        data_path: str,
        provider: str,
        data_tier: str,
        asset_type: str,
        symbol: Optional[str] = None,
        market: str = "KRX",
        notes: str = "",
        extra_meta: Optional[Dict[str, Any]] = None,
        timezone_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """데이터 파일의 사이드카 매니페스트를 생성하고 저장한다.
        timezone_name: 타임스탬프 문자열의 시간대. 없으면 시장 기본값(KRX=Asia/Seoul, 그 외 UTC)."""
        abs_data_path = os.path.abspath(data_path)
        if not os.path.exists(abs_data_path):
            raise FileNotFoundError(f"데이터 파일이 존재하지 않습니다: {abs_data_path}")

        if symbol is None:
            # 파일명에서 심볼 추론 (예: A069500_tick.csv -> 069500, NQ_1m.csv -> NQ, US_TECH_100_1m.csv -> US_TECH_100, KRW-BTC_10m.csv -> KRW-BTC)
            base = os.path.basename(abs_data_path).replace(".csv", "").replace(".parquet", "")
            match = re.search(r"A?(\d{6})", base)
            if match:
                symbol = match.group(1)
            else:
                # 뒤에서 마지막 타임프레임/주기 접미사 제거 (예: _1m, _5m, _10s, _tick, _1d 등)
                parts = base.rsplit("_", 1)
                if len(parts) == 2 and re.match(r"^(\d+[smhdw]|tick|daily|weekly|monthly)$", parts[1].lower()):
                    symbol = parts[0]
                else:
                    symbol = base

        sha256 = cls.calculate_sha256(abs_data_path)
        integrity, krx_val = cls.inspect_dataset_metrics(abs_data_path, market=market)
        integrity["sha256"] = sha256

        now_kst = datetime.now(KST).isoformat()

        health_summary = {
            "total_bars": integrity.get("row_count"),
            "first_timestamp": integrity.get("first_timestamp"),
            "last_timestamp": integrity.get("last_timestamp"),
            "null_ratio": integrity.get("null_ratio", 0.0),
            "price_range": integrity.get("price_range"),
            "has_microstructure": integrity.get("has_microstructure", False),
        }

        manifest = {
            "schema_version": "1.0",
            "data_file": os.path.basename(abs_data_path),
            "data_tier": data_tier.lower(),
            "provider": provider.lower(),
            "symbol": symbol,
            "asset_type": asset_type.lower(),
            "market": market.upper(),
            "timezone": timezone_name or ("Asia/Seoul" if market.upper() == "KRX" else "UTC"),
            "created_at": now_kst,
            "integrity": integrity,
            "health_summary": health_summary,
            "krx_validation": krx_val,
            "source_meta": {
                "notes": notes,
                **(extra_meta or {}),
            },
        }

        manifest_path = cls.get_manifest_path(abs_data_path)
        tmp_path = f"{manifest_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, manifest_path)

        return manifest

    @classmethod
    def generate_and_save_manifest(
        cls,
        data_file_path: str,
        data_tier: str = "raw",
        provider: str = "creon",
        asset_type: str = "standard",
        symbol: Optional[str] = None,
        market: str = "KRX",
        notes: str = "",
        extra_meta: Optional[Dict[str, Any]] = None,
        timezone_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """하위 호환 및 일괄 동기화용 래퍼: create_manifest 호출"""
        return cls.create_manifest(
            data_path=data_file_path,
            provider=provider,
            data_tier=data_tier,
            asset_type=asset_type,
            symbol=symbol,
            market=market,
            notes=notes,
            extra_meta=extra_meta,
            timezone_name=timezone_name,
        )

    @classmethod
    def load_manifest(cls, data_path: str) -> Optional[Dict[str, Any]]:
        """사이드카 매니페스트 파일을 로드한다. 없으면 None 반환."""
        manifest_path = cls.get_manifest_path(data_path)
        if not os.path.exists(manifest_path):
            return None
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # 하위 호환성 별칭
    read_manifest = load_manifest

    @classmethod
    def dataset_fingerprint(cls, data_path: str, content: Optional[bytes] = None) -> Dict[str, Any]:
        """학습 실행이 **어떤 데이터로 돌았는지** 남기기 위한 지문.

        `content` 를 주면 그 바이트로 해시한다 — 호출자가 같은 바이트로 데이터를 읽으면 해시와 학습 데이터가
        정확히 같은 것임이 보장된다(해시 계산과 로드 사이에 파일이 바뀌는 경쟁을 없앤다).
        매니페스트 해시와 다르면 `matches_manifest=False` — 매니페스트 기록 뒤 파일이 바뀌었다는 뜻이다.
        (2026-10-01: 실행 기록에 파일 이름만 남아, 오염 스냅샷 재빌드 전후 실행 중 어느 것이 오염 데이터를
         썼는지 증명할 수 없었다)
        """
        abs_path = os.path.abspath(data_path)
        if content is not None:
            sha = hashlib.sha256(content).hexdigest()
            size = len(content)
        else:
            sha = cls.calculate_sha256(abs_path)
            size = os.path.getsize(abs_path)
        manifest = cls.load_manifest(abs_path) or {}
        integrity = manifest.get("integrity", {}) or {}
        m_sha = integrity.get("sha256")
        return {
            "path": abs_path,
            "sha256": sha,
            "byte_size": size,
            "manifest_sha256": m_sha,
            "manifest_created_at": manifest.get("created_at"),
            "matches_manifest": (m_sha.lower() == sha.lower()) if m_sha else None,
            "row_count": integrity.get("row_count"),
            "first_timestamp": integrity.get("first_timestamp"),
            "last_timestamp": integrity.get("last_timestamp"),
        }

    @classmethod
    def verify_manifest(
        cls, data_path: str, strict_hash: bool = True
    ) -> Tuple[bool, str]:
        """
        데이터 파일의 사이드카 매니페스트 무결성을 검증한다.
        - 1단계: 파일 및 매니페스트 존재 확인
        - 2단계: 파일 크기(byte_size) 일치 확인 (0.001초 소요)
        - 3단계: strict_hash=True 일 경우 SHA256 해시 재계산 일치 확인
        """
        abs_data_path = os.path.abspath(data_path)
        if not os.path.exists(abs_data_path):
            return False, f"데이터 파일 누락: {abs_data_path}"

        manifest = cls.load_manifest(abs_data_path)
        if manifest is None:
            return False, f"사이드카 매니페스트 누락: {cls.get_manifest_path(abs_data_path)}"

        integrity = manifest.get("integrity", {})
        expected_size = integrity.get("byte_size")
        actual_size = os.path.getsize(abs_data_path)

        if expected_size is not None and expected_size != actual_size:
            return (
                False,
                f"파일 크기 불일치 (예상: {expected_size} bytes, 실제: {actual_size} bytes)",
            )

        if strict_hash:
            expected_hash = integrity.get("sha256")
            if not expected_hash:
                return False, "매니페스트에 sha256 해시가 기록되어 있지 않습니다."
            actual_hash = cls.calculate_sha256(abs_data_path)
            if actual_hash.lower() != expected_hash.lower():
                return (
                    False,
                    f"SHA256 해시 불일치 (예상: {expected_hash}, 실제: {actual_hash})",
                )

        return True, "OK"
