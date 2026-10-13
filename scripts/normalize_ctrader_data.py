#!/usr/bin/env python3
"""
cTrader 원시 데이터 정규화 및 Silver 계층 적재기 (Silver Normalizer)
- 6대 대용 지표 및 나스닥 범용 정규화 지원
- 변환:
  1. UTC 타임스탬프 -> KST (Asia/Seoul) datetime64[us] 통일
  2. 캔들 기하학 방역 (High >= max(O,H,L,C), Low <= min(O,H,L,C))
  3. Canonical Columns 생성: [timestamp, open, high, low, close, volume, trade_amount, vwap] (float32)
- 산출물: DataVault/normalized/candles_1m/{symbol}_1m.parquet 및 manifest.json
- Data Vault UI 대장(source_mappings.json) 자동 연동 훅(ensure_asset_registered) 내장
"""

import os
import sys
import json
import hashlib
import argparse
from datetime import datetime, timezone
import pandas as pd
import numpy as np

# scripts/fetch_ctrader_history.py에서 CTRADER_SYMBOLS 임포트 (또는 로컬 정의)
try:
    from scripts.fetch_ctrader_history import CTRADER_SYMBOLS
except ImportError:
    # 절대 경로 또는 직접 임포트 시도
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.fetch_ctrader_history import CTRADER_SYMBOLS


def normalize_ctrader_data(
    symbol_key: str = "US_TECH_100",
    raw_csv: str = None,
    out_parquet: str = None,
):
    if symbol_key not in CTRADER_SYMBOLS:
        # 심볼 키가 직접 전달되지 않은 경우 파일명에서 추론
        base = os.path.basename(raw_csv or "").replace(".csv", "").replace("_1m", "")
        if base in CTRADER_SYMBOLS:
            symbol_key = base
        else:
            symbol_key = "US_TECH_100"

    meta = CTRADER_SYMBOLS.get(symbol_key, {
        "display_name": f"{symbol_key} CFD (대용)",
        "category": "GLOBAL_INDEX_CFD_PROXY",
        "notes": f"cTrader {symbol_key} 대용 CFD",
        "digits": 2,
    })

    if raw_csv is None:
        raw_csv = f"DataVault/raw/ctrader/{symbol_key}_1m.csv"
    if out_parquet is None:
        out_parquet = f"DataVault/normalized/candles_1m/{symbol_key}_1m.parquet"

    if not os.path.exists(raw_csv):
        print(f"⚠️ [Silver Normalizer] 원시 CSV 파일이 존재하지 않습니다: {raw_csv}")
        return False

    digits = meta.get("digits", 2)
    print("=" * 65)
    print(f"🧹 [Silver Normalizer] [{symbol_key}] {meta['display_name']} 정규화 시작")
    print(f"• 입력 파일: {raw_csv}")
    print(f"• 출력 파일: {out_parquet}")
    print("=" * 65)

    # 1. 원시 CSV 로드
    df = pd.read_csv(raw_csv)
    raw_rows = len(df)
    print(f"📥 원시 데이터 로드 완료: {raw_rows:,d} 행")

    if raw_rows == 0:
        print(f"⚠️ [{symbol_key}] 데이터가 비어 있습니다.")
        return False

    # 2. 타임스탬프 변환: UTC -> KST (Asia/Seoul)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    df["timestamp"] = df["timestamp"].astype("datetime64[us]")

    # 3. 캔들 기하학 방역 및 이상치 제거 (Gatekeeper)
    initial_cnt = len(df)
    valid_mask = (
        (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
        & (df["volume"] >= 0)
    )
    df = df[valid_mask].copy()

    # 기하학 무결성 강제 보정 (High >= max(O,H,L,C), Low <= min(O,H,L,C))
    df["high"] = np.maximum(df["high"], np.maximum(df["open"], df["close"]))
    df["low"] = np.minimum(df["low"], np.minimum(df["open"], df["close"]))

    # 중복 타임스탬프 제거 및 정렬
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    clean_rows = len(df)
    dropped_rows = initial_cnt - clean_rows
    if dropped_rows > 0:
        print(f"🛡️ [Gatekeeper 방역] 비정상/중복 캔들 {dropped_rows:,d}개 정제 완료")

    # 4. Canonical 파생 컬럼 생성
    # trade_amount = close * volume
    df["trade_amount"] = (df["close"] * df["volume"]).astype(np.float32)
    # vwap = (high + low + close) / 3.0
    df["vwap"] = ((df["high"] + df["low"] + df["close"]) / 3.0).astype(np.float32)

    # float32 타입 캐스팅
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(np.float32)

    # 최종 컬럼 순서 정렬
    canonical_cols = ["timestamp", "open", "high", "low", "close", "volume", "trade_amount", "vwap"]
    df = df[canonical_cols]

    # 5. Parquet 파일 저장
    os.makedirs(os.path.dirname(out_parquet), exist_ok=True)
    df.to_parquet(out_parquet, engine="pyarrow", compression="snappy", index=False)
    file_bytes = os.path.getsize(out_parquet)

    # SHA256 계산
    with open(out_parquet, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    first_ts = str(df["timestamp"].iloc[0])
    last_ts = str(df["timestamp"].iloc[-1])
    min_p = float(df["low"].min())
    max_p = float(df["high"].max())

    # 6. Manifest JSON 생성
    manifest_path = f"{out_parquet}.manifest.json"
    manifest = {
        "schema_version": "1.0",
        "data_file": os.path.basename(out_parquet),
        "data_tier": "normalized",
        "provider": "ctrader",
        "symbol": symbol_key,
        "asset_type": "candles_1m",
        "market": meta.get("category", "GLOBAL_INDEX_CFD_PROXY"),
        "timezone": "Asia/Seoul",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "integrity": {
            "byte_size": file_bytes,
            "row_count": clean_rows,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "null_ratio": 0.0,
            "price_range": [round(min_p, digits), round(max_p, digits)],
            "has_microstructure": False,
            "sha256": sha256,
        },
        "health_summary": {
            "total_bars": clean_rows,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "null_ratio": 0.0,
            "price_range": [round(min_p, digits), round(max_p, digits)],
            "has_microstructure": False,
        },
        "source_meta": {
            "notes": f"cTrader Open API Normalized 1m Candles ({meta['display_name']} Canonical Schema)",
            "sampling_mode": "BAR_NATIVE",
            "sampling_desc": f"🔵 NATIVE 1m ({symbol_key} CFD 대용치 정규화)",
            "source_interval": "1m",
            "target_interval": "1m",
            "indicator_type": "proxy",
            "proxy_target": meta.get("proxy_target", ""),
        },
    }

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # 7. UI 대장 자동 등록 훅 (Auto-Register Hook)
    try:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        fallback_src = "CME" if "INDEX" in meta["category"] else ("COMEX" if "GOLD" in symbol_key else ("NYMEX" if "XTI" in symbol_key else "CME"))
        SourceMappingManager.ensure_asset_registered(
            symbol=symbol_key,
            name=meta["display_name"],
            category=meta["category"],
            role="macro_proxy",
            primary_source="CTRADER",
            primary_type="1M",
            fallback_source=fallback_src,
            fallback_type="1M",
            description=meta["notes"],
        )
        print(f"🔗 [UI 연동] Data Vault 대장에 {symbol_key} 등록 동기화 완료")
    except Exception as e:
        print(f"⚠️ [UI 연동 경고] 대장 자동 등록 중 예외: {e}")

    print("\n" + "=" * 65)
    print(f"🎉 [{symbol_key}] Silver 정규화 완료")
    print(f"• 산출물: {out_parquet}")
    print(f"• 크기: {file_bytes / (1024 * 1024):.2f} MB")
    print(f"• 총 캔들 수: {clean_rows:,d} 행")
    print(f"• 기간 (KST): {first_ts} ~ {last_ts}")
    print(f"• 가격 범위: {min_p:,.{digits}f} ~ {max_p:,.{digits}f}")
    print(f"• 컬럼: {list(df.columns)}")
    print("=" * 65)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="cTrader 과거 데이터 범용 Silver 정규화기")
    parser.add_argument(
        "--symbol",
        type=str,
        default="US_TECH_100",
        help=f"정규화할 심볼 키 (선택: {list(CTRADER_SYMBOLS.keys())} 또는 'all')",
    )
    args = parser.parse_args()

    if args.symbol.lower() == "all":
        for sym in CTRADER_SYMBOLS.keys():
            normalize_ctrader_data(symbol_key=sym)
    else:
        normalize_ctrader_data(symbol_key=args.symbol)
