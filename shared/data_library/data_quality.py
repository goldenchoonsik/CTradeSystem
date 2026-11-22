# -*- coding: utf-8 -*-
"""원천 데이터 품질 감사 (Data Quality Audit) — "파일에 그 날짜가 있는가"가 아니라 "그 날의 데이터가 믿을 만한가".

배경(2026-09-24): Raw Store 결측 화면은 날짜가 한 줄이라도 있으면 정상으로 셌다. 069500 틱 파일은 46거래일 중 23일이
실제 틱의 ~10%만 기록돼 있었는데(09-14는 틱 91개) 화면은 '결손 없음 100%'였고, 그 날들이 학습 데이터의 절반을 차지해
거래수·거래량 계열 피처가 시장이 아니라 수집 상태에 따라 요동쳤다.

원리: 같은 시장 활동을 서로 다른 해상도로 기록한 원천(틱 ⊂ 1분봉 ⊂ 일봉)은 **서로 대조할 수 있다.**
세밀한 원천의 하루치를 상위 원천으로 재집계해 맞춰 보면, 밀도 저하·시간 공백·가격 왜곡·중복이 종목 이름이나
임계 개수 하드코딩 없이 드러난다. 상위 원천이 없는 날은 '검증 불가'로 따로 표시한다(정상으로 치지 않는다).

검사 항목 (하루 단위, 각 항목은 ok/warn/bad):
  volume_ratio        하루 거래량 합 / 상위 원천 거래량   — 밀도 저하(누락 틱)
  minute_mismatch     상위 봉 중 거래량이 5% 넘게 어긋난 봉의 비율 — 부분 누락·시간대 공백·이중 계상
  minute_coverage     거래가 있었던 상위 봉 중 하위 기록이 존재하는 비율 — 시간 공백
  high_diff/low_diff/close_diff (bps)  가격 범위·종가 일치 — 가격 왜곡/전일 종가로 채워진 날
  duplicate_share     완전 중복 행 비율
  backward_share      시간이 거꾸로 가는 행 비율
  bad_price           0 이하/결측 가격 존재
  out_of_session      상위 원천의 거래 시간대 밖 기록 비율
  (상위 원천 없음) → unverifiable 로 표시, 하위 원천 자체의 이상치 검사만 수행

판정: bad 가 하나라도 있으면 그 날은 `bad`(학습 제외 대상), warn 만 있으면 `warn`, 상위 원천이 없으면 `unverifiable`.
"""
import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional, Set

import numpy as np
import pandas as pd

from shared.data_library.paths import data_vault_path
from shared.data_library import tick_store
from shared.data_library import candle_store

logger = logging.getLogger(__name__)

_CACHE_PATH = data_vault_path("temp", "cache", "data_quality_cache.json")
POLICY_VERSION = 7   # 정책/알고리즘이 바뀌면 올려서 옛 캐시를 무효화한다


@dataclass(frozen=True)
class QualityPolicy:
    """임계값 단일 정의 — 코드 곳곳에 숫자를 두지 않는다. (warn, bad) 쌍."""
    # ── 검사 항목 활성화 토글 (UI 옵션 연동, 기본: 모두 True) ──
    check_time_reversal: bool = True       # 시간 역행 (Time Reversal: ts_i < ts_{i-1}) 감지
    check_duplicate_time: bool = True      # 중복 타임스탬프 (Duplicate timestamps) 감지
    check_broken_geometry: bool = True     # 캔들 기하학 파손 (High < Low 등) 감지
    check_bad_price: bool = True           # 0 이하 / 결측 / 음수 가격 감지
    check_missing_bars: bool = True        # 세션 분봉 결손율 감지
    check_flash_spike: bool = True         # 비정상 가격 플래시 스파이크 감지
    check_cross_reconciliation: bool = True # 상위 원천(일봉/분봉) 거래량 및 가격 교차 대조

    # ── 임계치 설정 ──
    volume_ratio_dev: tuple = (0.02, 0.10)        # |비율-1|
    minute_mismatch: tuple = (0.05, 0.20)         # 어긋난 봉 비율
    minute_mismatch_tol: float = 0.05             # 봉 하나를 '어긋남'으로 보는 거래량 오차
    minute_coverage_min: tuple = (0.97, 0.90)     # 이 값 미만이면 warn / bad
    price_diff_bps: tuple = (5.0, 30.0)
    duplicate_share: tuple = (0.001, 0.01)
    duplicate_share_always_bad: float = 0.20      # 이보다 많은 완전 중복은 실제 체결일 수 없다(이력 이중 기록)
    backward_share: tuple = (0.001, 0.01)
    out_of_session_share: tuple = (0.001, 0.01)
    min_rows_for_trust: int = 1                   # 하루 기록이 이보다 적으면 무조건 bad
    flash_spike_threshold: float = 0.30           # 직전 봉 대비 30% 이상 순간 급변 시 경고
    missing_bars_threshold: float = 0.10          # 세션 기대 봉 수 대비 10% 이상 결손 시 경고

    def fingerprint(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:10]

    @classmethod
    def from_dict(cls, d: Optional[dict] = None) -> "QualityPolicy":
        """딕셔너리로부터 QualityPolicy 인스턴스 안전 생성"""
        if not d:
            return cls()
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


DEFAULT_POLICY = QualityPolicy()

# 시장/제공처별 상위 봉 라벨 규약 — 'end': 봉 시각 = 봉 종료 시각(크레온 1분봉 09:01 = 09:00~09:01)
BAR_LABEL_BY_PROVIDER = {"CREON": "end"}
_SEV = {"ok": 0, "warn": 1, "bad": 2}


# ─────────────────────────────────────────────────────────────────────────────
# 로딩
# ─────────────────────────────────────────────────────────────────────────────
_COLS = {"close": ("close", "upbit_close"), "volume": ("volume", "upbit_volume"),
         "open": ("open", "upbit_open"), "high": ("high", "upbit_high"), "low": ("low", "upbit_low")}


def _load_series(path: str, start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
    """CSV -> [ts, open, high, low, close, volume] (있는 컬럼만). 시간순 정렬은 하지 않는다(역행 검사를 위해 원본 순서 유지)."""
    tick_sym = tick_store.symbol_from_path(path)
    raw = tick_store.read(tick_sym, start=start, end=end) if tick_sym else None            # 틱은 창구로 읽는다 (일자 파일 + 통합 CSV)
    candle_sym = None if tick_sym else candle_store.symbol_from_path(path)
    if candle_sym and candle_store.is_day_layout(candle_sym):         # 1분봉 일자 저장소(통합 CSV 가 없다)
        raw = candle_store.read(candle_sym, start=start, end=end)
    header = raw.columns.tolist() if raw is not None else pd.read_csv(path, nrows=0).columns.tolist()
    ts_col = next((c for c in ("timestamp", "datetime", "date") if c in header), header[0])
    pick = {}
    for std, cands in _COLS.items():
        col = next((c for c in cands if c in header), None)
        if col:
            pick[std] = col
    extra = [c for c in ("cvd", "upbit_trade_price") if c in header]
    use = [ts_col] + list(pick.values()) + extra
    df = raw[[c for c in raw.columns if c in use]].copy() if raw is not None else pd.read_csv(path, usecols=use)   # usecols 와 같게 파일 컬럼 순서 유지
    df = df.rename(columns={v: k for k, v in pick.items()})
    df["ts"] = pd.to_datetime(df[ts_col], errors="coerce")
    df = df.drop(columns=[ts_col]) if ts_col != "ts" else df
    df = df.dropna(subset=["ts"]).reset_index(drop=True)
    if start and not df.empty:
        df = df[df["ts"].dt.strftime("%Y-%m-%d") >= start]
    if end and not df.empty:
        df = df[df["ts"].dt.strftime("%Y-%m-%d") <= end]
    return df.reset_index(drop=True)


def _bar_index_of_records(ts: pd.Series, ref_ts: pd.DatetimeIndex, bar_label: str) -> pd.Series:
    """하위 기록(틱)이 속하는 상위 봉의 라벨.

    크레온 규약 실측(2026-07-22, 분봉 381개 중 어긋남 0.3%): 1분봉 라벨 t 는 [t-1분, t) 구간의 체결을 담는다
    (초 단위 시각이 HH:MM:SS 인 틱은 floor(분)+1분 봉). 시가 동시호가(09:00:00) 체결도 09:01 봉에 포함된다.
    상위 봉이 없는 시각(종가 동시호가 15:30:00 → 15:31 봉은 없음)은 직전 봉으로 붙인다."""
    if bar_label == "end":
        label = ts.dt.floor("min") + pd.Timedelta(minutes=1)
    else:
        label = ts.dt.floor("min")
    frame = pd.DataFrame({"label": label.values, "i": np.arange(len(label))}).sort_values("label")
    ref = pd.DataFrame({"bar": pd.DatetimeIndex(ref_ts).sort_values().values})
    exact = pd.merge_asof(frame, ref, left_on="label", right_on="bar", direction="forward", tolerance=pd.Timedelta(0))
    back = pd.merge_asof(frame, ref, left_on="label", right_on="bar", direction="backward", tolerance=pd.Timedelta(minutes=2))
    bar = exact["bar"].fillna(back["bar"])
    return pd.Series(bar.values[np.argsort(exact["i"].values)], index=ts.index)


# ─────────────────────────────────────────────────────────────────────────────
# 감사 본체
# ─────────────────────────────────────────────────────────────────────────────
def _grade(value: Optional[float], warn: float, bad: float, higher_is_worse: bool = True) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "ok"
    if higher_is_worse:
        return "bad" if value > bad else ("warn" if value > warn else "ok")
    return "bad" if value < bad else ("warn" if value < warn else "ok")


def _bps(a: float, b: float) -> Optional[float]:
    if b is None or a is None or not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return None
    return abs(a - b) / abs(b) * 1e4


def audit_frames(fine: pd.DataFrame, ref: Optional[pd.DataFrame], bar_label: str = "end",
                 ref_kind: str = "minute", policy: QualityPolicy = DEFAULT_POLICY,
                 dates_to_audit: Optional[Set[str]] = None) -> Dict[str, Dict[str, Any]]:
    """세밀한 원천(fine) 하루하루를 상위 원천(ref)과 대조해 {날짜: 판정} 을 만든다.

    dates_to_audit 이 주어지면 해당 날짜만 선별하여 증분 대조한다 (고속 연산).
    ref_kind='minute': 분 단위 재집계 대조(minute_mismatch/coverage)와 하루 거래량·고저·종가 대조까지 수행한다.
    ref_kind='daily' : 종가만 대조한다. 일봉의 거래량·고저에는 정규장 밖(시간외/대체거래소) 체결이 섞여 있어
                       (실측: 1분봉 합이 일봉의 47%인 날이 있음) 정규장 자료와 직접 비교하면 오탐이 난다.
    """
    out: Dict[str, Dict[str, Any]] = {}
    fine = fine.copy()
    fine["d"] = fine["ts"].dt.strftime("%Y-%m-%d")
    if dates_to_audit is not None:
        fine = fine[fine["d"].isin(dates_to_audit)].copy()
        if fine.empty:
            return out
        if ref is not None:
            ref = ref.copy()
            ref_d = ref["ts"].dt.strftime("%Y-%m-%d")
            ref = ref[ref_d.isin(dates_to_audit)].copy()

    # ── 구조 검사 (상위 원천과 무관) ──
    fine["_dup"] = fine.duplicated(keep="first")
    fine["_back"] = fine["ts"].diff().dt.total_seconds() < 0
    price_col = "close" if "close" in fine.columns else None
    fine["_badpx"] = ((fine[price_col] <= 0) | fine[price_col].isna()) if price_col else False
    per_day = fine.groupby("d").agg(rows=("ts", "size"), dup=("_dup", "mean"), back=("_back", "mean"),
                                    badpx=("_badpx", "sum"))

    vol_ok = "volume" in fine.columns
    if ref is not None and vol_ok and "volume" in ref.columns:
        # 상위 원천 자체의 결함(이중 기록/순서 뒤섞임)은 그 원천의 감사에서 따로 보고한다. 여기서 그대로 대조하면
        # 이중 기록된 1분봉(거래량 2배) 때문에 멀쩡한 틱 파일이 "거래량 0.5"로 잘못 불량 판정된다(실측: 122630/229200/0167A0).
        ref = ref.drop_duplicates().sort_values("ts", kind="stable").reset_index(drop=True)
        ref["d"] = ref["ts"].dt.strftime("%Y-%m-%d")
        ref_day = ref.groupby("d").agg(
            vol=("volume", "sum"), hi=("high", "max") if "high" in ref else ("close", "max"),
            lo=("low", "min") if "low" in ref else ("close", "min"),
            cl=("close", "last"))
        fine_day = fine.groupby("d").agg(vol=("volume", "sum"), hi=(price_col, "max"), lo=(price_col, "min"),
                                         cl=(price_col, "last"))
        # 분 단위 재집계 대조 및 일봉 수익률 정합성 대조
        minute_stats = {}
        daily_ret_stats = {}
        if ref_kind == "minute":
            ref_ts = pd.DatetimeIndex(ref["ts"])
            fine["bar"] = _bar_index_of_records(fine["ts"], ref_ts, bar_label).values
            agg = fine.dropna(subset=["bar"]).groupby("bar")["volume"].sum().rename("fv")
            m = ref.set_index("ts")[["volume", "d"]].join(agg, how="left")
            m["fv"] = m["fv"].fillna(0.0)
            active = m[m["volume"] > 0]
            rel = (active["fv"] - active["volume"]).abs() / active["volume"]
            active = active.assign(bad=rel > policy.minute_mismatch_tol, has=active["fv"] > 0)
            for d, g in active.groupby("d"):
                minute_stats[d] = (float(g["bad"].mean()), float(g["has"].mean()))
            # 상위 봉 시간대 밖 기록
            sess = ref.groupby("d")["ts"].agg(["min", "max"])
            lo_b = fine["d"].map(sess["min"]) - pd.Timedelta(minutes=1)
            hi_b = fine["d"].map(sess["max"])
            fine["_oos"] = (fine["ts"] < lo_b) | (fine["ts"] > hi_b)
            oos = fine.groupby("d")["_oos"].mean()
        elif ref_kind == "daily" and ref_day is not None and not ref_day.empty and not fine_day.empty:
            oos = pd.Series(dtype=float)
            # 일간 등락률 및 수정주가 계단 단차 사전 분석 (ETF 분배락/배당락 오탐 방지)
            common_days = sorted(list(set(ref_day.index) & set(fine_day.index)))
            if len(common_days) >= 1:
                c_df = pd.DataFrame({
                    "f_cl": [fine_day.loc[cd, "cl"] for cd in common_days],
                    "r_cl": [ref_day.loc[cd, "cl"] for cd in common_days],
                }, index=common_days)
                c_df["f_ret"] = c_df["f_cl"].pct_change()
                c_df["r_ret"] = c_df["r_cl"].pct_change()
                c_df["ret_diff_bps"] = (c_df["f_ret"] - c_df["r_ret"]).abs() * 10000.0
                c_df["ratio"] = c_df["f_cl"] / c_df["r_cl"]
                c_df["ratio_step"] = c_df["ratio"].diff().abs()
                for cd in common_days:
                    daily_ret_stats[cd] = {
                        "ret_diff_bps": c_df.loc[cd, "ret_diff_bps"],
                        "ratio": c_df.loc[cd, "ratio"],
                        "ratio_step": c_df.loc[cd, "ratio_step"],
                        "is_first": (cd == common_days[0]),
                    }
        else:
            oos = pd.Series(dtype=float)
    else:
        ref_day = None
        minute_stats, daily_ret_stats, oos = {}, {}, pd.Series(dtype=float)

    for d, row in per_day.iterrows():
        checks: Dict[str, Dict[str, Any]] = {}

        def add(name: str, value, grade: str, label: str):
            checks[name] = {"value": None if value is None else round(float(value), 6), "grade": grade, "label": label}

        # 완전히 같은 행이 반복되는 것만으로는 결함이 아니다 — 같은 초에 같은 가격·수량으로 체결된 실제 체결일 수 있다
        # (실측: 000660 은 중복 행이 2%인데 거래량이 상위 원천과 정확히 일치). 그래서 판정은 거래량 정합 여부에 따른다:
        #   · 상위 원천과 거래량이 맞으면(정합) 실제 체결로 보고 결함으로 세지 않는다.
        #   · 대조할 수 없거나 거래량이 부풀었으면 중복 비율로 판정한다.
        #   · 비율이 매우 크면(전체 이력 이중 기록 등) 어떤 시장에서도 정상일 수 없으므로 언제나 불량이다.
        dup_grade = _grade(row["dup"], *policy.duplicate_share)
        dup_label = "완전 중복 행 비율"
        if row["dup"] <= policy.duplicate_share_always_bad and ref_day is not None and d in ref_day.index \
                and ref_day.loc[d, "vol"] > 0 and ref_kind == "minute":
            vr = fine_day.loc[d, "vol"] / ref_day.loc[d, "vol"]
            if abs(vr - 1.0) <= policy.volume_ratio_dev[0]:
                dup_grade, dup_label = "ok", "동일 체결 반복 비율(거래량 정합 — 실제 체결로 판단)"
        if row["dup"] > policy.duplicate_share_always_bad:
            dup_grade = "bad"
        if policy.check_duplicate_time:
            add("duplicate_share", row["dup"], dup_grade, dup_label)
        if policy.check_time_reversal:
            add("backward_share", row["back"], _grade(row["back"], *policy.backward_share), "시간 역행 행 비율")
        if policy.check_bad_price:
            add("bad_price", row["badpx"], "bad" if row["badpx"] > 0 else "ok", "0 이하/결측 가격 행 수")
        if row["rows"] < policy.min_rows_for_trust:
            add("rows", row["rows"], "bad", "기록 수")

        verifiable = policy.check_cross_reconciliation and ref_day is not None and d in ref_day.index and ref_day.loc[d, "vol"] > 0
        if verifiable:
            r, f = ref_day.loc[d], fine_day.loc[d]
            price_keys = (("cl", "close_diff"),)
            if ref_kind == "minute":
                ratio = f["vol"] / r["vol"] if r["vol"] else np.nan
                add("volume_ratio", ratio, _grade(abs(ratio - 1.0), *policy.volume_ratio_dev), "거래량 합 / 상위 원천")
                # 시가는 비교하지 않는다: 시가 동시호가 체결가와 1분봉 시가의 정의가 달라 정상일에도 수십 bps 어긋난다(실측).
                price_keys = (("hi", "high_diff"), ("lo", "low_diff"), ("cl", "close_diff"))
            for key, lbl in price_keys:
                v = _bps(f[key], r[key])
                grade = _grade(v, *policy.price_diff_bps)
                name = {"high_diff": "고가", "low_diff": "저가", "close_diff": "종가"}[lbl] + " 차이(bps)"
                if ref_kind == "daily" and lbl == "close_diff" and grade in ("warn", "bad"):
                    # 일봉 종가와 분봉 종가 차이는 수정주가/시간외 반영 가능성이 있으므로 결함(bad)이 아니라 경고(warn)로 완화
                    grade = "warn"
                    ret_info = daily_ret_stats.get(d)
                    if ret_info and pd.notna(ret_info.get("ret_diff_bps")) and ret_info.get("ret_diff_bps") <= 5.0:
                        name = f"{name} ({v:.1f}bps, 일간 등락률 일치/수정주가 기준)"
                    else:
                        name = f"{name} — 일봉이 수정주가(분봉과 기준 다름)일 수 있음"
                add(lbl, v, grade, name)
            if d in minute_stats:
                mm, cov = minute_stats[d]
                add("minute_mismatch", mm, _grade(mm, *policy.minute_mismatch), "거래량이 어긋난 분봉 비율")
                add("minute_coverage", cov, _grade(cov, *policy.minute_coverage_min, higher_is_worse=False),
                    "거래 있던 분봉 중 기록 존재 비율")
            if d in oos.index:
                add("out_of_session", oos[d], _grade(oos[d], *policy.out_of_session_share), "거래시간 밖 기록 비율")
        worst = max((_SEV[c["grade"]] for c in checks.values()), default=0)
        status = {0: "ok", 1: "warn", 2: "bad"}[worst]
        if not verifiable and status == "ok":
            status = "unverifiable"
        out[d] = {"status": status, "rows": int(row["rows"]), "checks": checks,
                  "reasons": [f"{c['label']} {c['value']}" for c in checks.values() if c["grade"] != "ok"]}

    # 상위 원천에는 있는데 하위 원천에 아예 없는 날 (파일 결측이 아니라 '한 줄도 없는 날')
    if ref_day is not None and len(per_day):
        first_d, last_d = per_day.index.min(), per_day.index.max()
        for d in ref_day.index:      # 하위 원천의 수집 범위 밖(시작 이전/끝 이후)은 '수집 안 함'이지 불량이 아니다
            if first_d <= d <= last_d and d not in out and ref_day.loc[d, "vol"] > 0:
                out[d] = {"status": "bad", "rows": 0, "checks": {"rows": {"value": 0, "grade": "bad", "label": "기록 수"}},
                          "reasons": ["하위 원천에 이 날짜의 기록이 없음"]}
    return out


def _apply_ledger_check(days: Dict[str, Dict[str, Any]], path: str) -> None:
    """수집 엔진의 날짜 원장(<파일>.days.json)이 있으면, 원장에 기록된 이후 내용이 바뀐 날짜를 불량으로 표시한다.
    (엔진을 거치지 않은 수정 = 봉인된 과거가 바뀐 것. 원장이 없으면 검사하지 않는다.)"""
    tick_sym = tick_store.symbol_from_path(path)          # 틱은 일자 저장소 — 원장 대신 날짜 메타로 검사한다
    candle_sym = candle_store.symbol_from_path(path)
    candle_day = bool(candle_sym) and candle_store.is_day_layout(candle_sym)   # 1분봉도 일자 저장소로 옮겨진 종목이면 같은 방식
    try:
        try:
            from CAiModelBaker.core import raw_day_store as store
            from CAiModelBaker.core import tick_day_store as tds
            from CAiModelBaker.core import candle_day_store as cds
        except ImportError:
            from core import raw_day_store as store
            from core import tick_day_store as tds
            from core import candle_day_store as cds
        _bad = lambda probs: any("불일치" in p or "거꾸로" in p or "다른 날짜" in p for p in probs)
        if tick_sym:        # 일자 저장소: 메타(해시·행 수)와 내용이 다른 날 = 엔진을 거치지 않은 수정
            mism = {d: "changed" for d, probs in tds.verify(tick_store.tick_root(), tick_sym).items() if _bad(probs)}
        elif candle_day:
            mism = {d: "changed" for d, probs in cds.verify(candle_store.candle_root(), candle_sym).items() if _bad(probs)}
        else:
            mism = store.ledger_mismatches(path)
    except Exception as e:                       # 원장 검사 실패가 감사를 막지는 않는다
        logger.warning(f"[DataQuality] 원장 대조 건너뜀({os.path.basename(path)}): {e}")
        return
    for d, kind in mism.items():
        label = "원장에 기록된 이후 내용이 바뀜(엔진을 거치지 않은 수정)" if kind == "changed" else "원장에는 있는데 파일에서 사라진 날짜"
        chk = {"value": 1.0, "grade": "bad", "label": label}
        v = days.setdefault(d, {"status": "bad", "rows": 0, "checks": {}, "reasons": []})
        v["checks"]["ledger_digest"] = chk
        v["status"] = "bad"
        v["reasons"].append(f"{label} 1.0")
    # 같은 시각에 기존과 다른 데이터가 들어와 보류된 곳 — 사용자가 확인할 때까지 불량으로 표시
    try:
        if tick_sym:
            pend = tick_store.pending_conflicts(tick_sym)
        elif candle_day:
            pend = candle_store.pending_conflicts(candle_sym)
        else:
            pend = store.pending_conflicts(path)
    except Exception:
        pend = []
    per_day: Dict[str, int] = {}
    for c in pend:
        per_day[c["day"]] = per_day.get(c["day"], 0) + 1
    for d, n in per_day.items():
        label = "같은 시각에 다른 데이터가 들어옴 — 확인 필요 (scripts/raw_store_integrity.py conflicts)"
        v = days.setdefault(d, {"status": "bad", "rows": 0, "checks": {}, "reasons": []})
        v["checks"]["pending_conflicts"] = {"value": float(n), "grade": "bad", "label": label}
        v["status"] = "bad"
        v["reasons"].append(f"{label} {n}건")


def audit_single_frame(
    df: pd.DataFrame,
    category: str = "",
    interval: str = "1M",
    policy: QualityPolicy = DEFAULT_POLICY,
    dates_to_audit: Optional[Set[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """단일 시계열(가상자산, CFD, 해외선물 등)에 대한 4대 시계열 무결성 감사 (일자별)."""
    if df.empty:
        return {}

    df = df.copy()
    df["d"] = df["ts"].dt.strftime("%Y-%m-%d")
    if dates_to_audit is not None:
        df = df[df["d"].isin(dates_to_audit)]
        if df.empty:
            return {}

    # 1. 시간 역행 (Time Reversal): ts[i] < ts[i-1]
    df["diff_ts"] = df["ts"].diff()
    df["is_back"] = df["diff_ts"] < pd.Timedelta(0)

    # 2. 중복 타임스탬프 (Duplicate Time): ts[i] == ts[i-1]
    df["is_dup"] = df["ts"].duplicated()

    # 3. 0 이하 / 결측 가격 (Bad Price)
    has_close = "close" in df.columns
    df["is_badpx"] = (df["close"] <= 0) | df["close"].isna() if has_close else False

    # 4. 캔들 기하학 파손 (Broken Geometry: High < Low, Open/Close 이탈)
    has_ohlc = all(c in df.columns for c in ("open", "high", "low", "close"))
    if has_ohlc:
        df["is_broken"] = (
            (df["high"] < df["low"]) |
            (df["open"] > df["high"]) | (df["open"] < df["low"]) |
            (df["close"] > df["high"]) | (df["close"] < df["low"])
        )
    else:
        df["is_broken"] = False

    # 5. 이상치 가격 스파이크 (Flash Spike)
    if has_close:
        ret = df["close"].pct_change().abs()
        df["is_spike"] = ret >= policy.flash_spike_threshold
    else:
        df["is_spike"] = False

    grouped = df.groupby("d")
    counts = grouped.size()
    back_cnt = grouped["is_back"].sum()
    dup_cnt = grouped["is_dup"].sum()
    badpx_cnt = grouped["is_badpx"].sum()
    broken_cnt = grouped["is_broken"].sum()
    spike_cnt = grouped["is_spike"].sum()

    # 세션 기대 봉 수 산출 (실제 봉 간격 중앙값 또는 카테고리/간격 기반)
    cat_upper = category.upper()
    median_step_sec = None
    if len(df) > 1 and "diff_ts" in df.columns:
        valid_diffs = df["diff_ts"].dropna()
        valid_diffs = valid_diffs[valid_diffs > pd.Timedelta(0)]
        if not valid_diffs.empty:
            median_step_sec = valid_diffs.median().total_seconds()

    if median_step_sec and median_step_sec > 0:
        if "CRYPTO" in cat_upper:
            expected_bars = max(1, int(round(86400 / median_step_sec)))
        elif "KRX" in cat_upper:
            expected_bars = max(1, int(round(23400 / median_step_sec)))
        else:
            expected_bars = max(1, int(round(82800 / median_step_sec)))
    else:
        expected_bars = 1440
        if "CRYPTO" in cat_upper:
            if "10" in interval: expected_bars = 144
            elif "15" in interval: expected_bars = 96
            else: expected_bars = 1440
        elif "KRX" in cat_upper:
            expected_bars = 391
        elif "GLOBAL" in cat_upper or "CFD" in cat_upper or "FX" in cat_upper or "COMMODITY" in cat_upper:
            expected_bars = 1380

    out = {}
    for d, row_cnt in counts.items():
        checks = {}
        reasons = []

        if policy.check_time_reversal:
            nb = int(back_cnt.get(d, 0))
            grd = "bad" if nb > 0 else "ok"
            checks["backward_share"] = {"value": nb, "grade": grd, "label": "시간 역행 행 수"}
            if nb > 0:
                reasons.append(f"시간 역행 {nb}건")

        if policy.check_duplicate_time:
            nd = int(dup_cnt.get(d, 0))
            grd = "warn" if nd > 0 else "ok"
            checks["duplicate_share"] = {"value": nd, "grade": grd, "label": "중복 타임스탬프 수"}
            if nd > 0:
                reasons.append(f"중복 타임스탬프 {nd}건")

        if policy.check_broken_geometry:
            ng = int(broken_cnt.get(d, 0))
            grd = "bad" if ng > 0 else "ok"
            checks["broken_geometry"] = {"value": ng, "grade": grd, "label": "캔들 파손(High<Low 등) 수"}
            if ng > 0:
                reasons.append(f"캔들 기하학 파손 {ng}건")

        if policy.check_bad_price:
            np_bad = int(badpx_cnt.get(d, 0))
            grd = "bad" if np_bad > 0 else "ok"
            checks["bad_price"] = {"value": np_bad, "grade": grd, "label": "0 이하/결측 가격 수"}
            if np_bad > 0:
                reasons.append(f"0 이하/결측 가격 {np_bad}건")

        if policy.check_missing_bars and expected_bars > 0:
            missing_bars = max(0, expected_bars - row_cnt)
            miss_ratio = missing_bars / expected_bars
            grd = "warn" if miss_ratio > policy.missing_bars_threshold else "ok"
            checks["missing_bars"] = {"value": round(miss_ratio, 3), "grade": grd, "label": "세션 대비 분봉 결손율"}
            if grd != "ok":
                reasons.append(f"분봉 결손 {missing_bars}개 ({round(miss_ratio*100, 1)}%)")

        if policy.check_flash_spike:
            ns = int(spike_cnt.get(d, 0))
            grd = "warn" if ns > 0 else "ok"
            checks["flash_spike"] = {"value": ns, "grade": grd, "label": "비정상 플래시 스파이크 수"}
            if ns > 0:
                reasons.append(f"가격 급변 스파이크 {ns}건")

        worst = max((_SEV[c["grade"]] for c in checks.values()), default=0)
        status = {0: "ok", 1: "warn", 2: "bad"}[worst]
        out[d] = {
            "status": status,
            "rows": int(row_cnt),
            "checks": checks,
            "reasons": reasons
        }
    return out


def summarize(days: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"ok": 0, "warn": 0, "bad": 0, "unverifiable": 0}
    for v in days.values():
        counts[v["status"]] = counts.get(v["status"], 0) + 1
    return {**counts, "total": len(days),
            "bad_days": sorted(d for d, v in days.items() if v["status"] == "bad"),
            "warn_days": sorted(d for d, v in days.items() if v["status"] == "warn")}


# ─────────────────────────────────────────────────────────────────────────────
# 파일 단위 감사 + 캐시 (원본 mtime/크기 + 정책 지문이 같으면 재사용)
# ─────────────────────────────────────────────────────────────────────────────
def _sig(path: Optional[str]) -> Optional[list]:
    tick_sym = tick_store.symbol_from_path(path)
    if tick_sym and tick_store.has_data(tick_sym):     # 틱: 일자 파일이 덮어써져도 바뀌는 구성 파일 전체 지문(통합 CSV 가 없어도)
        return [tick_store.signature(tick_sym)]
    candle_sym = candle_store.symbol_from_path(path)
    if candle_sym and candle_store.is_day_layout(candle_sym):   # 1분봉 일자 저장소: 구성 파일 전체 지문
        return [candle_store.signature(candle_sym)]
    if not path or not os.path.exists(path):
        return None
    st = os.stat(path)
    return [st.st_mtime_ns, st.st_size]


class DataQualityAuditor:
    _mem: Dict[str, Dict[str, Any]] = {}
    _loaded = False
    _lock = threading.Lock()

    @classmethod
    def _load(cls):
        with cls._lock:
            if cls._loaded:
                return
            cls._loaded = True
            try:
                if os.path.exists(_CACHE_PATH):
                    with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                        cls._mem = json.load(f)
                else:
                    cls._mem = {}
            except Exception:
                cls._mem = {}

    @classmethod
    def _save(cls):
        with cls._lock:
            try:
                os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
                import uuid
                temp_path = f"{_CACHE_PATH}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"
                snapshot = dict(cls._mem)
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(snapshot, f, ensure_ascii=False)
                os.replace(temp_path, _CACHE_PATH)
            except Exception as e:
                logger.warning(f"[DataQuality] 캐시 저장 실패: {e}")

    @classmethod
    def _key(cls, fine_path: str, ref_path: Optional[str]) -> str:
        return f"{fine_path}|{ref_path or '-'}"

    @classmethod
    def _valid(cls, entry: Optional[dict], fine_path: str, ref_path: Optional[str], policy: QualityPolicy) -> bool:
        return bool(entry and entry.get("policy") == f"{POLICY_VERSION}:{policy.fingerprint()}"
                    and entry.get("fine_sig") == _sig(fine_path) and entry.get("ref_sig") == _sig(ref_path)
                    and entry.get("ledger_sig") == _sig(fine_path + ".days.json")
                    and entry.get("conflicts_sig") == _sig(fine_path + ".conflicts.json"))

    @classmethod
    def peek(cls, fine_path: str, ref_path: Optional[str] = None,
             policy: QualityPolicy = DEFAULT_POLICY) -> Optional[Dict[str, Any]]:
        """계산하지 않고 유효한 캐시만 돌려준다 (없거나 낡았으면 None). 화면 목록처럼 가벼워야 하는 곳에서 쓴다."""
        cls._load()
        entry = cls._mem.get(cls._key(fine_path, ref_path))
        return entry if cls._valid(entry, fine_path, ref_path, policy) else None

    @classmethod
    def audit_pair(cls, fine_path: str, ref_path: Optional[str], provider: str = "CREON",
                   ref_kind: str = "minute", force: bool = False, full: bool = False,
                   policy: QualityPolicy = DEFAULT_POLICY,
                   log_fn: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        cls._load()
        key = cls._key(fine_path, ref_path)
        existing = cls._mem.get(key)
        policy_sig = f"{POLICY_VERSION}:{policy.fingerprint()}"

        # 1. 완전 캐시 적중 (강제 실행도 아니고, 파일 서명도 변경되지 않은 경우)
        if not force and not full:
            cached = cls.peek(fine_path, ref_path, policy)
            if cached:
                return cached

        log = log_fn or logger.info
        mode_str = "전수(Full)" if full else "증분(Incremental)"
        log(f"🔎 [데이터 품질] 감사 시작 ({mode_str}): {os.path.basename(fine_path)}"
            + (f" ↔ {os.path.basename(ref_path)}" if ref_path else " (상위 원천 없음 — 구조 검사만)"))

        # 2. 증분 검사 로직 (full=False): 기존 검증 완료 일자는 재사용하고 신규/미검증 일자만 대조
        dates_to_audit = None
        reusable_days = {}
        tick_sym = tick_store.symbol_from_path(fine_path)
        candle_sym = candle_store.symbol_from_path(fine_path) if not tick_sym else None

        if not full and existing and existing.get("policy") == policy_sig:
            old_days_map = existing.get("days", {})
            # 틱/1분봉 일자 저장소면 파일 파싱 없이 디렉토리/메타에서 전체 날짜 집합을 초고속 획득
            if tick_sym:
                all_fine_dates = tick_store.dates(tick_sym)
            elif candle_sym and candle_store.is_day_layout(candle_sym):
                all_fine_dates = candle_store.dates(candle_sym)
            else:
                all_fine_dates = None

            if all_fine_dates is not None:
                reusable_days = {d: v for d, v in old_days_map.items() if d in all_fine_dates}
                dates_to_audit = all_fine_dates - set(reusable_days.keys())
                if not dates_to_audit:
                    # 신규 일자가 전혀 없으면 파일 로드 자체를 건너뛰고 캐시 즉시 반환 (메모리 0 사용)
                    entry = {
                        "policy": policy_sig,
                        "fine_sig": _sig(fine_path),
                        "ref_sig": _sig(ref_path),
                        "ledger_sig": _sig(fine_path + ".days.json"),
                        "conflicts_sig": _sig(fine_path + ".conflicts.json"),
                        "days": reusable_days,
                        "summary": summarize(reusable_days)
                    }
                    cls._mem[key] = entry
                    cls._save()
                    return entry

        # 로드할 날짜 범위 산출 (증분 대상 일자가 특정되어 있으면 해당 기간만 로드하여 메모리 절약)
        # 틱 일자 저장소(ref_kind != "daily")는 수천만 행을 한 번에 올리면 OOM(5GB+)이 날 수 있으므로
        # 5거래일 단위 청크로 분할해 로드·대조하고 즉시 메모리를 해제한다.
        _TICK_AUDIT_CHUNK_DAYS = 5
        tick_target_dates = None
        if tick_sym and ref_kind != "daily" and tick_store.has_data(tick_sym):
            tick_target_dates = sorted(dates_to_audit) if dates_to_audit is not None else sorted(tick_store.dates(tick_sym))

        if tick_target_dates and len(tick_target_dates) > _TICK_AUDIT_CHUNK_DAYS:
            import gc
            new_days = {}
            bar_label = BAR_LABEL_BY_PROVIDER.get(provider.upper(), "end")
            for i in range(0, len(tick_target_dates), _TICK_AUDIT_CHUNK_DAYS):
                chunk = tick_target_dates[i:i + _TICK_AUDIT_CHUNK_DAYS]
                c_start, c_end = chunk[0], chunk[-1]
                fine_c = _load_series(fine_path, start=c_start, end=c_end)
                ref_c = _load_series(ref_path, start=c_start, end=c_end) if ref_path and candle_store.path_exists(ref_path) else None
                chunk_days = audit_frames(
                    fine_c, ref_c, bar_label, ref_kind, policy, dates_to_audit=set(chunk)
                )
                new_days.update(chunk_days)
                del fine_c, ref_c
                gc.collect()
        else:
            start_d = min(dates_to_audit) if dates_to_audit else None
            end_d = max(dates_to_audit) if dates_to_audit else None

            fine = _load_series(fine_path, start=start_d, end=end_d)
            ref = _load_series(ref_path, start=start_d, end=end_d) if ref_path and candle_store.path_exists(ref_path) else None

            # 일반 파일(일자 저장소가 아닌 경우)에 대한 증분 계산 (파일 지문이 같을 때만 기존 일자 재사용)
            if not full and existing and existing.get("policy") == policy_sig and existing.get("fine_sig") == _sig(fine_path) and dates_to_audit is None and not fine.empty:
                fine_temp = fine.copy()
                fine_temp["d"] = fine_temp["ts"].dt.strftime("%Y-%m-%d")
                all_fine_dates = set(fine_temp["d"].unique())
                old_days_map = existing.get("days", {})
                reusable_days = {d: v for d, v in old_days_map.items() if d in all_fine_dates}
                dates_to_audit = all_fine_dates - set(reusable_days.keys())
                del fine_temp

                if not dates_to_audit:
                    entry = {
                        "policy": policy_sig,
                        "fine_sig": _sig(fine_path),
                        "ref_sig": _sig(ref_path),
                        "ledger_sig": _sig(fine_path + ".days.json"),
                        "conflicts_sig": _sig(fine_path + ".conflicts.json"),
                        "days": reusable_days,
                        "summary": summarize(reusable_days)
                    }
                    cls._mem[key] = entry
                    cls._save()
                    return entry

            # 3. 프레임 감사 실행 (dates_to_audit이 있으면 해당 일자만 초고속 대조)
            new_days = audit_frames(
                fine, ref,
                BAR_LABEL_BY_PROVIDER.get(provider.upper(), "end"),
                ref_kind, policy,
                dates_to_audit=dates_to_audit
            )
        _apply_ledger_check(new_days, fine_path)

        # 4. 기존 감사 일자와 신규 감사 일자 병합
        combined_days = {**reusable_days, **new_days}
        entry = {
            "policy": policy_sig,
            "fine_sig": _sig(fine_path),
            "ref_sig": _sig(ref_path),
            "ledger_sig": _sig(fine_path + ".days.json"),
            "conflicts_sig": _sig(fine_path + ".conflicts.json"),
            "days": combined_days,
            "summary": summarize(combined_days)
        }
        cls._mem[key] = entry
        cls._save()
        s = entry["summary"]
        log(f"   ↳ 완료: 정상 {s['ok']} · 주의 {s['warn']} · 불량 {s['bad']} · 검증불가 {s['unverifiable']} (총 {s['total']}일)")
        return entry

    @classmethod
    def audit_file(
        cls,
        file_path: str,
        category: str = "",
        interval: str = "1M",
        force: bool = False,
        full: bool = False,
        policy: QualityPolicy = DEFAULT_POLICY,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """단일 파일 시계열(가상자산, CFD, 해외선물 등)에 대한 품질 감사 (증분/전수)."""
        cls._load()
        key = file_path
        existing = cls._mem.get(key)
        policy_sig = f"{POLICY_VERSION}:{policy.fingerprint()}"

        if not force and not full:
            if existing and existing.get("policy") == policy_sig and existing.get("sig") == _sig(file_path):
                return existing

        log = log_fn or logger.info
        mode_str = "전수(Full)" if full else "증분(Incremental)"
        log(f"🔎 [데이터 품질] 단일 시계열 감사 ({mode_str}): {os.path.basename(file_path)}")

        df = _load_series(file_path)
        if df.empty:
            entry = {"policy": policy_sig, "sig": _sig(file_path), "days": {}, "summary": summarize({})}
            cls._mem[key] = entry
            cls._save()
            return entry

        df["d"] = df["ts"].dt.strftime("%Y-%m-%d")
        all_dates = set(df["d"].unique())

        dates_to_audit = None
        reusable_days = {}
        if not full and existing and existing.get("policy") == policy_sig:
            old_days_map = existing.get("days", {})
            reusable_days = {d: v for d, v in old_days_map.items() if d in all_dates}
            dates_to_audit = all_dates - set(reusable_days.keys())
            if not dates_to_audit:
                entry = {
                    "policy": policy_sig,
                    "sig": _sig(file_path),
                    "days": reusable_days,
                    "summary": summarize(reusable_days),
                }
                cls._mem[key] = entry
                cls._save()
                return entry

        new_days = audit_single_frame(
            df,
            category=category,
            interval=interval,
            policy=policy,
            dates_to_audit=dates_to_audit
        )
        combined_days = {**reusable_days, **new_days}
        entry = {
            "policy": policy_sig,
            "sig": _sig(file_path),
            "days": combined_days,
            "summary": summarize(combined_days)
        }
        cls._mem[key] = entry
        cls._save()
        s = entry["summary"]
        log(f"   ↳ 완료: 정상 {s['ok']} · 주의 {s['warn']} · 불량 {s['bad']} (총 {s['total']}일)")
        return entry

    # ── 종목 단위: 틱 ↔ 1분봉 ↔ 일봉 사슬 및 단일 자산 감사 ──
    @classmethod
    def granularity_paths(cls, symbol: str) -> Dict[str, str]:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        clean = SourceMappingManager.clean_symbol(symbol)
        groups = SourceMappingManager._creon_interval_candidate_groups(clean)
        return {label: p for label, cands in groups.items()
                for p in [next((c for c in cands if tick_store.path_has_data(c) or candle_store.path_exists(c)), None)] if p}

    @classmethod
    def audit_symbol(cls, symbol: str, force: bool = False, full: bool = False,
                     log_fn: Optional[Callable[[str], None]] = None,
                     policy: QualityPolicy = DEFAULT_POLICY) -> Dict[str, Dict[str, Any]]:
        """존재하는 그래뉼러리티에 대해 감사 수행.
        CREON 자산은 틱/분봉/일봉 교차 감사, 그 외 단일 원천 자산은 audit_file 수행."""
        from shared.data_library.source_mapping_manager import SourceMappingManager
        paths = cls.granularity_paths(symbol)
        result: Dict[str, Dict[str, Any]] = {}
        if paths:
            if "TICK" in paths:
                result["TICK"] = cls.audit_pair(paths["TICK"], paths.get("1M"), ref_kind="minute",
                                                force=force, full=full, policy=policy, log_fn=log_fn)
            if "1M" in paths:
                result["1M"] = cls.audit_pair(paths["1M"], paths.get("1D"), ref_kind="daily",
                                              force=force, full=full, policy=policy, log_fn=log_fn)
            if "1D" in paths:
                result["1D"] = cls.audit_pair(paths["1D"], None, force=force, full=full, policy=policy, log_fn=log_fn)
            return result

        # CREON 외 단일 원천 자산 감사
        registry = {r["symbol"]: r for r in SourceMappingManager.get_feed_registry_status()}
        item = registry.get(symbol) or registry.get(SourceMappingManager.clean_symbol(symbol))
        if item and item.get("file_path") and item.get("file_exists"):
            fpath = item["file_path"]
            lbl = item.get("ingestion_resolution", "1M")
            fname_lower = os.path.basename(fpath).lower()
            if "10m" in fname_lower:
                lbl = "10M"
            elif "15m" in fname_lower:
                lbl = "15M"
            elif "1h" in fname_lower:
                lbl = "1H"
            elif "1d" in fname_lower:
                lbl = "1D"
            entry = cls.audit_file(fpath, category=item.get("category", ""), interval=lbl,
                                   force=force, full=full, policy=policy, log_fn=log_fn)
            result[lbl] = entry
        import gc
        gc.collect()
        return result

    @classmethod
    def audit_all_symbols(
        cls,
        symbols: Optional[List[str]] = None,
        full: bool = False,
        max_workers: int = 1,
        policy: QualityPolicy = DEFAULT_POLICY,
        log_fn: Optional[Callable[[str], None]] = None,
        progress_cb: Optional[Callable[[int, int, str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """등록된 전체 실존 자산의 품질 감사를 수행한다 (기본: max_workers=1 직렬 안전 실행으로 OOM 방지)."""
        import gc
        from shared.data_library.source_mapping_manager import SourceMappingManager
        from concurrent.futures import ThreadPoolExecutor, as_completed

        registry = SourceMappingManager.get_feed_registry_status()
        all_syms = [r["symbol"] for r in registry if r.get("file_exists")]
        if symbols:
            wanted = {SourceMappingManager.clean_symbol(s) for s in symbols} | {s.upper() for s in symbols}
            all_syms = [s for s in all_syms if s in wanted or SourceMappingManager.clean_symbol(s) in wanted]

        total = len(all_syms)
        results: Dict[str, Dict[str, Any]] = {}
        completed = 0

        log = log_fn or logger.info
        mode_str = "전수(Full)" if full else "증분(Incremental)"
        log(f"🚀 [DataQuality] 전 자산 품질 감사 시작 (총 {total}개 자산, 모드: {mode_str}, 워커: {max_workers})")

        def _worker(sym: str):
            res = cls.audit_symbol(sym, force=full, full=full, log_fn=log_fn, policy=policy)
            return sym, res

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_worker, s): s for s in all_syms}
            for future in as_completed(future_map):
                s = future_map[future]
                try:
                    sym, res = future.result()
                    results[sym] = res
                except Exception as e:
                    logger.error(f"[DataQuality] {s} 감사 실패: {e}")
                    results[s] = {"error": str(e)}
                completed += 1
                gc.collect()
                if progress_cb:
                    try:
                        progress_cb(completed, total, s, results.get(s, {}))
                    except Exception:
                        pass

        gc.collect()
        log(f"✅ [DataQuality] 전 자산 품질 감사 완료 (총 {total}개 완료)")
        return results

    @classmethod
    def peek_symbol(cls, symbol: str, policy: QualityPolicy = DEFAULT_POLICY) -> Dict[str, Dict[str, Any]]:
        from shared.data_library.source_mapping_manager import SourceMappingManager
        paths = cls.granularity_paths(symbol)
        out = {}
        if paths:
            if "TICK" in paths and (e := cls.peek(paths["TICK"], paths.get("1M"), policy)):
                out["TICK"] = e
            if "1M" in paths and (e := cls.peek(paths["1M"], paths.get("1D"), policy)):
                out["1M"] = e
            if "1D" in paths and (e := cls.peek(paths["1D"], None, policy)):
                out["1D"] = e
            return out

        # CREON 외 단일 원천 자산 peek
        cls._load()
        registry = {r["symbol"]: r for r in SourceMappingManager.get_feed_registry_status()}
        item = registry.get(symbol) or registry.get(SourceMappingManager.clean_symbol(symbol))
        if item and item.get("file_path"):
            fpath = item["file_path"]
            lbl = item.get("ingestion_resolution", "1M")
            entry = cls._mem.get(fpath)
            policy_sig = f"{POLICY_VERSION}:{policy.fingerprint()}"
            if entry and entry.get("policy") == policy_sig and entry.get("sig") == _sig(fpath):
                out[lbl] = entry
        return out


# ─────────────────────────────────────────────────────────────────────────────
# 소비자: 학습 데이터 게이트
# ─────────────────────────────────────────────────────────────────────────────
def source_labels_for_interval(interval_seconds: float) -> List[str]:
    """이 봉 길이의 데이터셋이 의존하는 원천 그래뉼러리티. 1분 미만 봉은 틱에서, 1분 이상은 1분봉에서 만들어진다.
    (틱이 불량이면 그 위 1분봉이 멀쩡해도 초봉은 못 믿고, 1분봉이 불량이면 틱 기반 초봉도 시각이 어긋난다.)"""
    if interval_seconds < 60:
        return ["TICK", "1M"]
    if interval_seconds < 86400:
        return ["1M"]
    return ["1D"]


def unusable_days(symbol: str, interval_seconds: float, statuses: Set[str] = frozenset({"bad"}),
                  force: bool = False, log_fn: Optional[Callable[[str], None]] = None) -> Dict[str, List[str]]:
    """{날짜: [사유…]} — 이 종목·봉 길이의 학습에 쓰면 안 되는 날. 원천이 없으면 빈 dict (게이트가 막지 않는다)."""
    audits = DataQualityAuditor.audit_symbol(symbol, force=force, log_fn=log_fn)
    bad: Dict[str, List[str]] = {}
    for label in source_labels_for_interval(interval_seconds):
        for d, v in (audits.get(label, {}).get("days") or {}).items():
            if v["status"] in statuses:
                bad.setdefault(d, []).extend(f"[{label}] {r}" for r in v["reasons"][:2])
    return bad


def apply_quality_gate(df: pd.DataFrame, symbol: str, interval_seconds: float,
                       log_fn: Callable[[str], None] = print, enabled: bool = True,
                       statuses: Set[str] = frozenset({"bad"})) -> tuple:
    """데이터프레임에서 품질 불량일의 행을 제거한다. (필터된 df, 보고서) 를 돌려준다.

    피처를 이미 계산한 뒤에 호출해야 한다 — 롤링 지표는 연속 데이터로 계산하고, 학습/검증/홀드아웃에서만 그 날들을 빼기 위함.
    WFA 창은 실제로 존재하는 거래일 기준(split_planner.build_wfa_windows)이라 빠진 날은 자연스럽게 건너뛴다.
    """
    report = {"enabled": enabled, "excluded_days": {}, "kept_rows": len(df), "dropped_rows": 0}
    if not enabled or df is None or df.empty:
        return df, report
    try:
        bad = unusable_days(symbol, interval_seconds, statuses)
    except Exception as e:      # 감사 실패가 학습을 막지는 않되, 조용히 넘어가지 않는다
        log_fn(f"⚠️ [데이터 품질] 감사 실패 — 게이트 없이 진행합니다: {e}")
        report["error"] = str(e)
        return df, report
    if not bad:
        log_fn("✅ [데이터 품질] 감사 결과 사용 불가한 날 없음 (또는 검증할 원천 없음)")
        return df, report
    day = df["timestamp"].dt.strftime("%Y-%m-%d")
    present = sorted(set(day.unique()) & set(bad))
    if not present:
        return df, report
    mask = day.isin(present)
    report.update(excluded_days={d: bad[d] for d in present}, dropped_rows=int(mask.sum()),
                  kept_rows=int((~mask).sum()))
    total_days = day.nunique()
    log_fn(f"🧹 [데이터 품질] 원천 대조에서 신뢰할 수 없는 {len(present)}/{total_days}거래일을 학습·검증·홀드아웃에서 제외합니다 "
           f"({mask.sum():,}행)")
    for d in present[:12]:
        log_fn(f"   - {d}: {'; '.join(bad[d][:2])}")
    if len(present) > 12:
        log_fn(f"   … 외 {len(present) - 12}일")
    if len(present) / max(1, total_days) >= 0.3:
        log_fn(f"⚠️ [데이터 품질] 전체의 {len(present) / total_days:.0%}가 제외됩니다 — 원천 수집 상태를 먼저 점검하세요")
    return df[~mask].reset_index(drop=True), report
