# -*- coding: utf-8 -*-
"""
CAiModelBaker - AutoLab Bridge
===============================
DataVault 3단계(Features Store)의 온디맨드 시점 결합 결과를,
AutoLab(WFA/홀드아웃 분할, 모델 학습/백테스트)이 읽는 Gold 스냅샷
(``data_vault/features/{SYMBOL}/data_snapshot_{INTERVAL}.csv``)으로
연결하는 단일 writer.

컬럼 이름은 `FeatureComposer.compose()` 가 원장 심볼에서 유도한 `ref_{key}_*` (config_manager.ref_prefix) 하나뿐이다.
이 모듈은 이름을 바꾸거나 별칭을 붙이지 않는다 — 결합할 종목은 symbol_relations 의 결합 계획(join_plan)이 정한다.
"""

import os
import json
import logging
import threading
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from typing import Optional, Callable, Dict, Any

from shared.data_library import paths as dpaths
from shared.data_library.manifest_manager import ManifestManager
from shared.data_library.symbol_relations_manager import SymbolRelationsManager
from shared.data_library.source_mapping_manager import SourceMappingManager
from shared.data_library.reference_alignment_audit import (
    assert_reference_alignment,
    expected_ref_close_columns,
)
from CAiModelBaker.core.feature_composer import FeatureComposer

def _atomic_replace_write(path: str, writer) -> None:
    """tmp 파일에 writer(tmp_path) 로 쓴 뒤 os.replace (실패 시 tmp 정리)"""
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
    os.close(fd)
    try:
        writer(tmp)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _assert_target_timeline_intact(clean_symbol: str, interval: str, log: Callable[[str], None]) -> None:
    """타겟 캔들의 시간축이 상위 봉과 일치하는지 확인한다 (대조할 상위 봉이 없으면 건너뛴다).

    행수·결측 검사로는 드러나지 않는 "값의 집합은 맞는데 순서가 틀어진" 결함을 잡는 유일한 방법이다.
    """
    import pandas as pd
    from shared.data_library.interval_consistency_audit import (
        assert_interval_consistency, _interval_to_timedelta,
    )
    from shared.data_library.source_mapping_manager import SourceMappingManager

    mapping = SourceMappingManager.load_all_mappings().get(clean_symbol) or {}
    scales = [str(s) for s in (mapping.get("target_scales") or mapping.get("canonical_intervals") or [])]
    fine = _interval_to_timedelta(interval)
    coarser = sorted(
        (s for s in scales if _interval_to_timedelta(s) > fine),
        key=_interval_to_timedelta,
    )
    if not coarser:
        log(f"  ⏭️ [시간축 감사] {clean_symbol}({interval}): 대조할 상위 봉이 원장에 없어 건너뜁니다")
        return

    # 정규화 산출물을 그대로 읽는다 — 캔들 금고는 없는 봉을 다른 봉에서 만들어 주므로
    # (상위→하위 리샘플은 금지되어 예외가 난다) 이 감사에는 맞지 않는다.
    def _load(intv: str):
        p = dpaths.normalized_path(f"candles_{intv}", f"{clean_symbol}_{intv}.parquet")
        return pd.read_parquet(p) if p and os.path.exists(p) else None

    fine_df = _load(interval)
    coarse_df = _load(coarser[0])
    if fine_df is None or coarse_df is None or fine_df.empty or coarse_df.empty:
        log(f"  ⏭️ [시간축 감사] {clean_symbol}({interval}↔{coarser[0]}): 대조 데이터 부재로 건너뜁니다")
        return
    assert_interval_consistency(fine_df, coarse_df, clean_symbol, interval, coarser[0], log_fn=log)


def column_coverage(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """컬럼별 {first_valid, last_valid, valid_ratio}. Gold 는 결측을 채우지 않으므로 이 값이 곧 데이터 존재 기간이다."""
    ts = pd.to_datetime(df["timestamp"])
    out = {}
    for col in df.columns:
        if col == "timestamp":
            continue
        valid = df[col].notna().to_numpy()
        if valid.any():
            idx = np.flatnonzero(valid)
            out[col] = {"first_valid": str(ts.iloc[idx[0]]), "last_valid": str(ts.iloc[idx[-1]]),
                        "valid_ratio": round(float(valid.mean()), 6)}
        else:
            out[col] = {"first_valid": None, "last_valid": None, "valid_ratio": 0.0}
    return out


def compose_gold_frame(target_symbol: str, interval: str, log: Callable[[str], None]):
    """Gold 스냅샷 한 장의 내용(결합 + 정합 감사)을 만든다 — 학습 Gold 와 실전 백필 이력의 단일 생성 경로.

    Returns: (df, ref_symbols, series_symbols)
    """
    clean_symbol = SourceMappingManager.clean_symbol(target_symbol)

    plan = SymbolRelationsManager.join_plan(clean_symbol)
    ref_symbols, series_symbols = plan["refs"], plan["series"]

    log(f"🏗️ [AutoLab Bridge] {clean_symbol}({interval}) Gold 스냅샷 빌드 시작 — "
        f"대장주 {ref_symbols or '없음'}, 시리즈(매크로/선물/CFD) {series_symbols or '없음'}")

    composer = FeatureComposer(local_log=log)
    df = composer.compose(
        target_symbol=clean_symbol,
        target_interval=interval,
        ref_symbols=ref_symbols or None,
        macro_symbols=series_symbols or None,
        align_to_common_dates=True,
        synthesize_cross_features=True,
        use_cache=False,
    )

    # ── [T-6] 참조 계열 타임스탬프 정합 감사 (저장 전 fail-closed) ──
    # 결합 코드가 옳은지를 묻지 않고, **결합 결과의 수치**가 미래를 담고 있는지 직접 잰다.
    # 종목/수집처/인터벌을 모르는 범용 검사이므로 신규 참조가 추가돼도 그물이 유지된다.
    # 위반 시 예외가 올라가 스냅샷이 디스크에 쓰이지 않는다 — 오염된 데이터가 학습에 도달할 수 없다.
    log("🔍 [정합 감사] 참조 계열 타임스탬프 미래참조 검사...")
    audited_cols = expected_ref_close_columns(list(ref_symbols or []) + list(series_symbols or []))
    assert_reference_alignment(df, ref_columns=audited_cols, log_fn=log, require_all=True)

    # ── [T-6] 타겟 자체의 시간축 정합 감사 ──
    # 참조만 검사하면 타겟이 망가진 경우를 놓친다. 실제로 2026-09-28 에 069500 10초봉의
    # 분 내부 순서가 뒤집힌 구간(2026-07-21~08-18)이 있었고, 그 역순이 참조의 미래참조를
    # 상쇄해 감사를 통과시켰다. 타겟은 상위 봉과 대조해 시간축이 성한지 따로 확인한다.
    _assert_target_timeline_intact(clean_symbol, interval, log)

    return df, ref_symbols, series_symbols


def build_gold_snapshot(target_symbol: str, interval: str = "10s",
                         local_log: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """DataVault Features Store에서 AutoLab Gold 스냅샷을 재빌드한다.

    `symbol_relations.json` 에 도킹된 모든 참조(대장주/매크로/선물/CFD 등)를 결합 계획(join_plan)대로
    결합하고, 원장 심볼에서 유도한 컬럼 이름(`ref_{key}_*`) 그대로
    `dpaths.gold_snapshot_path(symbol, interval)` 에 CSV로 저장한다.
    """
    log = local_log if local_log is not None else logging.info
    clean_symbol = SourceMappingManager.clean_symbol(target_symbol)
    df, ref_symbols, series_symbols = compose_gold_frame(clean_symbol, interval, log)

    out_path = dpaths.gold_snapshot_path(clean_symbol, interval)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    _atomic_replace_write(out_path, lambda t: df.to_csv(t, index=False))
    log(f"  💾 [Gold 스냅샷 저장] {out_path} ({len(df):,}행, {len(df.columns)}컬럼)")

    start_date = str(df["timestamp"].min())
    end_date = str(df["timestamp"].max())

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "datavault_features_store",
        "target_symbol": clean_symbol,
        "interval": interval,
        "ref_symbols": ref_symbols,
        "series_symbols": series_symbols,
        "rows": len(df),
        "columns": len(df.columns),
        "start_date": start_date,
        "end_date": end_date,
        # 컬럼별 데이터 존재 기간 — 하류(커버리지 게이트·학습 구간 결정)가 값을 보고 추측하지 않게 한다
        "column_coverage": column_coverage(df),
    }
    meta_path = dpaths.gold_snapshot_meta_path(clean_symbol, interval)
    os.makedirs(os.path.dirname(os.path.abspath(meta_path)), exist_ok=True)
    def _w_meta(t):
        with open(t, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    _atomic_replace_write(meta_path, _w_meta)

    ManifestManager.create_manifest(
        data_path=out_path,
        provider="autolab_bridge",
        data_tier="features",
        asset_type=f"gold_snapshot_{interval}",
        symbol=clean_symbol,
        notes=f"AutoLab Gold snapshot rebuilt from DataVault Features Store ({len(df)} rows)",
    )

    log(f"✅ [AutoLab Bridge] Gold 스냅샷 빌드 완료: {start_date} ~ {end_date}")

    return {
        "rows": len(df),
        "cols": len(df.columns),
        "start_date": start_date,
        "end_date": end_date,
        "ref_symbols_used": ref_symbols,
        "series_symbols_used": series_symbols,
        "path": out_path,
    }


_ENSURE_LOCK = threading.Lock()


def _stale_reason(snapshot_path: str, clean_symbol: str) -> Optional[str]:
    """이 모듈(브리지)이 만든 Gold 스냅샷이 현재 결합 계획(symbol_relations 의 join_plan)과 어긋나면 그 사유, 아니면 None.
    관계가 바뀌었거나 브리지 스키마가 바뀐 뒤에도 옛 스냅샷을 그대로 쓰면 피처가 조용히 죽는다.
    브리지가 만들지 않은 스냅샷(구 빌더)은 구성 정보가 없어 판단하지 않는다."""
    meta_path = os.path.splitext(snapshot_path)[0] + ".meta.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    if meta.get("source") != "datavault_features_store":
        return None
    if "series_symbols" not in meta:
        return "스냅샷 메타가 이전 스키마입니다(참조 이름 규칙 변경 이전에 빌드됨)"
    plan = SymbolRelationsManager.join_plan(clean_symbol)
    if meta.get("ref_symbols") != plan["refs"] or meta.get("series_symbols") != plan["series"]:
        return (f"결합한 참조 {meta.get('ref_symbols')}+{meta.get('series_symbols')} "
                f"!= 현재 계획 {plan['refs']}+{plan['series']}")
    return None


def ensure_gold_snapshot(target_symbol: str, interval: str, snapshot_path: str,
                         local_log: Optional[Callable[[str], None]] = None,
                         force_rebuild: bool = False) -> Dict[str, Any]:
    """ASRL 시작 전 Gold 스냅샷 준비 보장 (AutoLab Phase 0).

    force_rebuild=True 인 경우 최신 DataVault Features Store 기준으로 즉시 동기화 재빌드를 수행한다.
    이미 있고 force_rebuild=False 이며 stale 하지 않으면 그대로 재사용한다.
    Normalized 가 없어 진행할 수 없으면 조용히 넘어가지 않고 **어느 단계에서 무엇을 해야 하는지**를 돌려준다.

    Returns: {"ready": bool, "built": bool, "message": str}
    """
    log = local_log if local_log is not None else logging.info
    clean_symbol = SourceMappingManager.clean_symbol(target_symbol)
    stale_reason = _stale_reason(snapshot_path, clean_symbol)
    if not force_rebuild and os.path.exists(snapshot_path) and not stale_reason:
        return {"ready": True, "built": False, "message": "Gold 스냅샷이 이미 존재합니다."}

    with _ENSURE_LOCK:
        stale_reason = _stale_reason(snapshot_path, clean_symbol)
        if not force_rebuild and os.path.exists(snapshot_path) and not stale_reason:  # 대기 중 다른 요청이 이미 빌드함
            return {"ready": True, "built": False, "message": "Gold 스냅샷이 이미 존재합니다."}
        if force_rebuild:
            log(f"🏗️ [AutoLab Phase 0] {clean_symbol}({interval}) 최신 DataVault Features Store 기준 Gold 스냅샷 자동 빌드/동기화 진행...")
        elif stale_reason:
            log(f"♻️ [AutoLab Phase 0] {clean_symbol}({interval}) 기존 Gold 스냅샷이 현재 결합 계획과 달라 재빌드합니다: {stale_reason}")
        else:
            log(f"🔗 [AutoLab Phase 0] {clean_symbol}({interval}) Gold 스냅샷이 없어 Normalized -> Gold 자동 빌드를 진행합니다...")
        try:
            build_gold_snapshot(clean_symbol, interval, local_log=log)
        except Exception as e:
            scales = []
            try:
                asset = SourceMappingManager.load_all_mappings().get(clean_symbol) or {}
                scales = [str(x).lower() for x in (asset.get("target_scales") or asset.get("canonical_intervals") or [])]
            except Exception:
                pass
            if scales and interval.lower() not in scales:
                hint = (f"{clean_symbol} 의 정규화 대상 스케일({', '.join(scales)})에 '{interval}' 이 없습니다. "
                        f"Data Vault > 2. Normalized Store 에서 해당 종목의 '🎯 스케일'에 '{interval}' 을 체크하고 "
                        f"'저장 & 즉시 실버 정규화 빌드'를 먼저 실행하세요.")
            else:
                hint = (f"Normalized 데이터가 없거나 결합에 실패했습니다. Data Vault > 2. Normalized Store 에서 "
                        f"{clean_symbol}({interval}) 정규화 빌드를 먼저 실행하세요.")
            return {"ready": False, "built": False,
                    "message": f"Gold 스냅샷 자동 빌드 실패 — {hint} (원인: {e})"}

    if not os.path.exists(snapshot_path):
        return {"ready": False, "built": True,
                "message": f"Gold 스냅샷을 빌드했지만 ASRL 이 읽는 경로({snapshot_path})에서 찾을 수 없습니다."}
    return {"ready": True, "built": True, "message": "Gold 스냅샷을 자동 빌드했습니다."}
