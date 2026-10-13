"""1분봉 이벤트 추출 (H1~H4, PREREGISTRATION.md) — 종목별 날짜×분 행렬로 신호·진입·청산을 계산해 이벤트 테이블을 만든다.

성과 판정은 별도(evaluate_events.py). 이 단계는 이벤트와 특징값만 기록한다(이후 상황 모델 학습 데이터 겸용).

가격 기준: 1분봉은 무수정이므로 기업행위 원장(shared.data_library.corporate_actions, build_ca_ledger.py 로 생성)의
누적 계수를 곱해 수정주가 기준으로 맞춘다(액면병합·분할일의 가짜 갭·가짜 고가 돌파 방지).
배수 기준: 거래대금(분할·병합 불변). 직전 20거래일에 거래정지·1분봉 누락이 있으면 그날은 판정하지 않는다.

  .venv/bin/python docs/research/20261011_vol_spike/event_engine.py [--workers 4] [--limit N]
출력: DataVault/staging/vol_spike_universe/events/events.parquet
"""
import argparse
import glob
import os
import sys
from dataclasses import dataclass
from multiprocessing import Pool

import numpy as np
import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
M1_DIR = os.path.join(STAGE, "raw", "creon", "candle_1m")
D_ADJ_DIR = os.path.join(STAGE, "raw", "creon", "candle_1d")
D_RAW_DIR = os.path.join(STAGE, "raw", "creon", "candle_1d_unadj")
OUT_DIR = os.path.join(STAGE, "events")
sys.path.insert(0, REPO)
from shared.core.market_utils import krx_session  # noqa: E402  장 일정 원장(수능·새해 첫 거래일)
from shared.data_library import corporate_actions as ca  # noqa: E402  기업행위 원장 — 수정 계산은 여기서만
CA_LEDGER = os.path.join(STAGE, "reference", "corporate_actions")

# 정규장 연속매매 격자: 09:01 ~ 15:20 라벨(380분). 15:30 봉(종가 동시호가)은 따로.
GRID = pd.timedelta_range("09:01:00", "15:20:00", freq="1min")
NM = len(GRID)
MIN_IDX = {f"{t.components.hours:02d}:{t.components.minutes:02d}": i for i, t in enumerate(GRID)}
HORIZONS = [5, 15, 30, 60]
EXIT_1515 = MIN_IDX["15:15"]
MIN_BAR_VALUE = 1e8          # 신호 봉 거래대금 하한(원)
ENTRY_WINDOW = 3             # 신호 후 이 분 안의 첫 체결 봉 시가에 진입
BASE_DAYS, BASE_MIN = 20, 15


def m(hhmm):
    return MIN_IDX[hhmm]


CONFIGS = (
    [("H1", {"k": k}) for k in (5, 10, 20)]
    + [("H2", {"x": x, "k": k}) for x in (0.03, 0.05) for k in (5, 10)]
    + [("H3", {"k": k}) for k in (5, 10)]
    + [("H4", {})]
    # H6 — 흔히 쓰는 '거래량 급증' 조건검색식 (사용자 요청, 2026-10-11 중간 판정 후 추가)
    + [("H6a", {"k": k, "x": x}) for k in (1, 2) for x in (0.05, 0.10)]   # 당일 누적 거래대금 ≥ k×평소 하루 + 상승률 ≥ x
    + [("H6b", {"k": k}) for k in (5, 10)]                                # 1분 거래대금 ≥ k×직전 5분 평균 + 양봉 + 당일 상승 중
)


@dataclass
class Day:
    """한 종목의 거래일 달력(수정 일봉 기준)과 날짜×분 행렬(수정주가 기준)."""
    dates: pd.DatetimeIndex
    mats: dict              # o,h,l,c,val: (nd, NM) / traded: bool
    day_close: np.ndarray   # 15:30 종가(수정 기준), 1분봉 없는 날 NaN
    usable: np.ndarray      # 그날 1분봉 있음 & 거래정지 아님
    factor: np.ndarray = None  # 수정 계수 f_D (실제가 = 수정가 / f_D) — 호가 단위·가격 필터는 실제가로


def _read_daily(path):
    d = pd.read_csv(path, parse_dates=["timestamp"]).drop_duplicates("timestamp").set_index("timestamp").sort_index()
    return d


def load_day(code):
    files = sorted(glob.glob(os.path.join(M1_DIR, code, "*.csv")))
    adj_p, raw_p = os.path.join(D_ADJ_DIR, f"{code}_1d.csv"), os.path.join(D_RAW_DIR, f"{code}_1d.csv")
    if not files or not os.path.exists(adj_p) or not os.path.exists(raw_p):
        return None
    raw = _read_daily(raw_p)
    first = pd.Timestamp(os.path.basename(files[0])[:10])
    cal = raw.index[raw.index >= first - pd.Timedelta(days=45)]       # 기준선 계산용으로 조금 앞까지
    factor = ca.cumulative_factor(ca.events(CA_LEDGER, code), cal)     # 원장 계수(반올림 잡음 없음)
    halted = (raw["upbit_volume"].reindex(cal) <= 0).to_numpy()

    df = pd.concat((pd.read_csv(f) for f in files), ignore_index=True)
    df.columns = ["ts", "o", "h", "l", "c", "v", "val", "cvd"][: len(df.columns)]
    ts = pd.to_datetime(df["ts"])
    df["date"] = ts.dt.normalize()
    df = df[df["date"].isin(cal)].copy()
    # 특수 거래일(수능 10:00~16:30, 새해 첫날 10:00 개장)은 개장 시각 차이만큼 당겨 평일 기준 시각으로 맞춘다
    shift = {d: pd.Timedelta(hours=krx_session(d).open.hour - 9, minutes=krx_session(d).open.minute)
             for d in df["date"].unique()}
    df["tod"] = (ts.loc[df.index] - df["date"]) - df["date"].map(shift)
    f_row = factor.reindex(df["date"]).to_numpy()
    for col in ("o", "h", "l", "c"):
        df[col] = df[col].to_numpy(dtype=float) * f_row
    close_auction = df[df["tod"] == pd.Timedelta("15:30:00")].drop_duplicates("date").set_index("date")["c"]
    cont = df[(df["tod"] >= GRID[0]) & (df["tod"] <= GRID[-1])].drop_duplicates(["date", "tod"])
    di = cal.get_indexer(cont["date"])
    mi = ((cont["tod"] - GRID[0]) // pd.Timedelta("1min")).astype(int).to_numpy()
    shape = (len(cal), NM)
    mats = {}
    for col in ("o", "h", "l", "c", "val"):
        a = np.full(shape, np.nan)
        a[di, mi] = cont[col].to_numpy(dtype=float)
        mats[col] = a
    traded = ~np.isnan(mats["c"])
    mats["val"][~traded] = 0.0
    c = pd.DataFrame(mats["c"]).ffill(axis=1).to_numpy()            # 무체결 분: 같은 날 직전 체결가 유지
    for col in ("o", "h", "l"):
        mats[col] = np.where(traded, mats[col], c)
    mats["c"] = c
    mats["traded"] = traded
    has1m = traded.any(axis=1)
    day_close = close_auction.reindex(cal).to_numpy(dtype=float)
    last_cont = c[:, -1]
    day_close = np.where(np.isnan(day_close), last_cont, day_close)
    day_close[~has1m] = np.nan
    usable = has1m & ~halted & np.isfinite(factor.to_numpy())
    return Day(cal, mats, day_close, usable, factor.to_numpy(dtype=float))


def entry_price(mats, d, sig, offset=1):
    """신호 봉 sig 이후 offset 분부터 ENTRY_WINDOW 분 안의 첫 체결 봉 시가. 없으면 (None, None)."""
    for j in range(sig + offset, min(sig + offset + ENTRY_WINDOW, NM)):
        if mats["traded"][d, j]:
            return j, mats["o"][d, j]
    return None, None


def exits(mats, d, j, px):
    out = {}
    for h in HORIZONS:
        k = min(j + h - 1, NM - 1)
        out[f"r{h}"] = mats["c"][d, k] / px - 1
    out["r1515"] = mats["c"][d, EXIT_1515] / px - 1 if j <= EXIT_1515 else np.nan
    return out


def compute_events(code, day: Day):
    """Day → 이벤트 DataFrame. 날 D·분 t 의 신호는 D 의 t 이하 봉과 D 이전 날만 본다(테스트로 고정)."""
    dates, mats, day_close, usable = day.dates, day.mats, day.day_close, day.usable
    nd = len(dates)
    fac = day.factor if day.factor is not None else np.ones(nd)
    val, c, tr = mats["val"], mats["c"], mats["traded"]
    # 같은 분 직전 20거래일 평균 거래대금 — 정지·누락일이 섞인 창은 쓰지 않는다
    base = pd.DataFrame(np.where(usable[:, None], val, np.nan)).rolling(BASE_DAYS, min_periods=BASE_MIN).mean().shift(1).to_numpy()
    clean_hist = pd.Series(~usable).rolling(BASE_DAYS, min_periods=BASE_DAYS).sum().shift(1).to_numpy() == 0
    with np.errstate(divide="ignore", invalid="ignore"):
        vr = np.where(base > 0, val / base, np.nan)
    prev_close = np.r_[np.nan, day_close[:-1]]
    has = tr.any(axis=1)
    first_open = np.full(nd, np.nan)
    fidx = tr.argmax(axis=1)
    first_open[has] = mats["o"][np.arange(nd)[has], fidx[has]]
    hi = np.where(tr, mats["h"], np.nan)
    lo = np.where(tr, mats["l"], np.nan)
    with np.errstate(all="ignore"):
        day_high = np.where(has, np.nanmax(np.where(tr, hi, -np.inf), axis=1), np.nan)
        day_low = np.where(has, np.nanmin(np.where(tr, lo, np.inf), axis=1), np.nan)
    prev_high20 = pd.Series(day_high).rolling(20, min_periods=20).max().shift(1).to_numpy()
    prev_range20 = ((pd.Series(day_high) - pd.Series(day_low)) / pd.Series(prev_close)).rolling(20, min_periods=20).mean().shift(1).to_numpy()
    ret15 = np.full_like(c, np.nan)
    ret15[:, 15:] = c[:, 15:] / c[:, :-15] - 1
    run_high = np.fmax.accumulate(np.where(tr, mats["h"], -np.inf), axis=1)
    liquid = val >= MIN_BAR_VALUE
    # H6: 평소 하루 거래대금(직전 20 사용 가능일, 당일 제외)·당일 누적·직전 5분 평균(당일 안, t 제외)
    day_val = np.where(usable, val.sum(axis=1), np.nan)
    base_day = pd.Series(day_val).rolling(BASE_DAYS, min_periods=BASE_MIN).mean().shift(1).to_numpy()
    cum_val = np.cumsum(val, axis=1)
    prev5 = np.full_like(val, np.nan)
    cs = np.c_[np.zeros(nd), cum_val]
    prev5[:, 5:] = (cs[:, 5:-1] - cs[:, :-6]) / 5.0

    rows = []
    for d in range(1, nd):
        if not (usable[d] and usable[d - 1] and clean_hist[d]) or not np.isfinite(prev_close[d]):
            continue
        pc, fo = prev_close[d], first_open[d]
        gap = fo / pc - 1
        for hyp, prm in CONFIGS:
            k = prm.get("k")
            if hyp == "H1":
                if gap < 0.03:
                    continue
                cand = (vr[d] >= k) & liquid[d] & (c[d] > mats["o"][d])
                cand[m("09:30") + 1:] = False
            elif hyp == "H2":
                cand = (ret15[d] <= -prm["x"]) & (vr[d] >= k) & liquid[d]
                cand[: m("09:30")] = False
                cand[m("15:00") + 1:] = False
            elif hyp == "H3":
                if not np.isfinite(prev_high20[d]):
                    continue
                above = c[d] > prev_high20[d]
                before = np.r_[False, np.logical_or.accumulate(above)[:-1]]
                cand = above & ~before & (vr[d] >= k) & liquid[d]       # 그날 처음 올라선 봉에서만
                cand[: m("09:05")] = False
                cand[m("15:00") + 1:] = False
            elif hyp == "H6a":
                if not np.isfinite(base_day[d]) or base_day[d] <= 0:
                    continue
                cand = (cum_val[d] >= k * base_day[d]) & (c[d] / pc - 1 >= prm["x"]) & liquid[d]
                cand[: m("09:05")] = False
                cand[m("14:30") + 1:] = False
            elif hyp == "H6b":
                with np.errstate(divide="ignore", invalid="ignore"):
                    burst = np.where(prev5[d] > 0, val[d] / prev5[d], np.inf)
                cand = (burst >= k) & liquid[d] & (c[d] > mats["o"][d]) & (c[d] > pc)
                cand[: m("09:05")] = False
                cand[m("15:00") + 1:] = False
            else:  # H4 — 고가가 당일 시가 +10% 첫 도달
                touch = run_high[d] >= fo * 1.10
                cand = touch & ~np.r_[False, touch[:-1]] & liquid[d]
                cand[m("15:00") + 1:] = False
            idx = np.flatnonzero(cand)
            if len(idx) == 0:
                continue
            sig = idx[0]
            j, px = entry_price(mats, d, sig, offset=3 if hyp == "H4" else 1)
            if j is None or not np.isfinite(px) or px <= 0:
                continue
            row = {"code": code, "date": dates[d], "hyp": hyp, "cfg": ",".join(f"{a}={b}" for a, b in prm.items()) or "-",
                   "sig_min": sig, "entry_min": j, "entry_px": px,
                   "vr": vr[d, sig], "bar_value": val[d, sig], "gap": gap,
                   "ret_open_to_sig": c[d, sig] / fo - 1, "ret15": ret15[d, sig],
                   "ret_prevclose_to_sig": c[d, sig] / pc - 1, "prev_range20": prev_range20[d],
                   "day_value_to_sig": val[d, : sig + 1].sum(), "prev_close": pc,
                   "factor": fac[d], "entry_px_raw": px / fac[d], "prev_close_raw": pc / fac[d]}
            row.update(exits(mats, d, j, px))
            row["r_close"] = day_close[d] / px - 1          # 15:30 동시호가 청산(참고)
            rows.append(row)
    return pd.DataFrame(rows)


def process(code):
    day = load_day(code)
    return pd.DataFrame() if day is None else compute_events(code, day)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--codes-file", default=None, help="이 목록의 종목만 (수집 중 중간 판정용)")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "events.parquet"))
    a = ap.parse_args()
    codes = sorted(d for d in os.listdir(M1_DIR) if os.path.isdir(os.path.join(M1_DIR, d)))
    if a.codes_file:
        with open(a.codes_file) as f:
            keep = {ln.strip() for ln in f if ln.strip()}
        codes = [c for c in codes if c in keep]
    if a.limit:
        codes = codes[: a.limit]
    with Pool(a.workers) as pool:
        parts = pool.map(process, codes, chunksize=4)
    ev = pd.concat([p for p in parts if len(p)], ignore_index=True)
    uni = pd.read_csv(os.path.join(STAGE, "universe.csv")).set_index("code")
    ev["market"] = ev["code"].map(uni["market"])
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    ev.to_parquet(a.out, index=False)
    print(f"종목 {len(codes)} → 이벤트 {len(ev):,} 저장 {a.out}")
    print(ev.groupby(["hyp", "cfg"]).size().to_string())


if __name__ == "__main__":
    main()
