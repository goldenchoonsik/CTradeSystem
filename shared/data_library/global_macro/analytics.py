"""지도 계산 — 판정 없는 기술 분석(검증은 docs/research 사전등록에서).

  bear_markets   약세장 판정(Lunde–Timmermann 20%/20% 규칙): 고점은 20% 하락으로, 저점은 20% 반등으로 확정
  spillover      Diebold–Yilmaz 연결도(일반화 분산분해): 누가 누구의 변동을 몇 % 설명하나
  lead_corr      두 월 시계열의 선후 상관(k>0 = x 가 k개월 먼저)
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------- 약세장
def bear_markets(close: pd.Series, down: float = 0.20, up: float = 0.20) -> List[dict]:
    """일봉 종가 → 약세장 목록. 확정일(confirm) = 고점 대비 −down 에 처음 닿은 날(그날까지는 아무도 모름)."""
    s = close.dropna()
    if s.empty:
        return []
    v, idx = s.values, s.index
    out = []
    state = "bull"
    ext_i = 0                       # bull 이면 최고점 위치, bear 면 최저점 위치
    peak_i = None
    confirm_i = None
    for i in range(1, len(v)):
        if state == "bull":
            if v[i] > v[ext_i]:
                ext_i = i
            elif v[i] <= v[ext_i] * (1 - down):
                state, peak_i, confirm_i, ext_i = "bear", ext_i, i, i
        else:
            if v[i] < v[ext_i]:
                ext_i = i
            elif v[i] >= v[ext_i] * (1 + up):
                out.append(_bear(s, peak_i, ext_i, confirm_i, done=True))
                state, ext_i = "bull", i
    if state == "bear":
        out.append(_bear(s, peak_i, ext_i, confirm_i, done=False))
    return out


def _bear(s: pd.Series, p: int, t: int, c: int, done: bool) -> dict:
    v, idx = s.values, s.index
    after = s.iloc[t:]
    rec = after[after >= v[p]]
    ten = s.iloc[p:t + 1]
    ten = ten[ten <= v[p] * 0.9]
    return {"peak": str(idx[p].date()), "trough": str(idx[t].date()), "depth": float(v[t] / v[p] - 1),
            "confirm": str(idx[c].date()), "minus10": str(ten.index[0].date()) if len(ten) else None,
            "days_to_trough": int((idx[t] - idx[p]).days), "recovered": str(rec.index[0].date()) if len(rec) else None,
            "trough_confirmed": bool(done)}


# ---------------------------------------------------------------- 연결도
def _var_ols(Y: np.ndarray, p: int):
    T, N = Y.shape
    X = np.hstack([np.ones((T - p, 1))] + [Y[p - i - 1:T - i - 1] for i in range(p)])
    B = np.linalg.lstsq(X, Y[p:], rcond=None)[0]
    E = Y[p:] - X @ B
    A = [B[1 + i * N:1 + (i + 1) * N].T for i in range(p)]
    S = E.T @ E / (len(E) - X.shape[1])
    return A, S


def spillover(R: pd.DataFrame, p: int = 2, H: int = 10) -> Optional[dict]:
    """일반화 예측오차 분산분해(Pesaran–Shin) 기반 연결도 표. R: 수익률(행=시점, 열=시장). 표[i][j] = j 가 i 의 변동에 기여한 %."""
    R = R.dropna()
    if len(R) < max(60, 10 * R.shape[1]):
        return None
    Y = R.values
    N = Y.shape[1]
    A, S = _var_ols(Y, p)
    Phi = [np.eye(N)]
    for h in range(1, H):
        Phi.append(sum(A[i] @ Phi[h - i - 1] for i in range(min(h, p))))
    num = np.zeros((N, N))
    den = np.zeros(N)
    for P in Phi:
        PS = P @ S
        num += PS ** 2 / np.diag(S)[None, :]
        den += np.diag(PS @ P.T)
    th = num / den[:, None]
    th = th / th.sum(axis=1, keepdims=True) * 100
    names = list(R.columns)
    off = th - np.diag(np.diag(th))
    return {"names": names, "table": th.round(2).tolist(), "from_others": off.sum(axis=1).round(2).tolist(),
            "to_others": off.sum(axis=0).round(2).tolist(), "net": (off.sum(axis=0) - off.sum(axis=1)).round(2).tolist(),
            "total": float(off.sum() / N), "n": int(len(R)), "start": str(R.index[0].date()), "end": str(R.index[-1].date())}


def rolling_spillover(R: pd.DataFrame, window: int = 104, step: int = 4, **kw) -> List[dict]:
    R = R.dropna()
    out = []
    for e in range(window, len(R) + 1, step):
        r = spillover(R.iloc[e - window:e], **kw)
        if r:
            out.append({"date": r["end"], "table": r["table"], "total": r["total"], "from_others": r["from_others"],
                        "to_others": r["to_others"], "net": r["net"]})
    return out


# ---------------------------------------------------------------- 선후 상관
def lead_corr(y: pd.Series, x: pd.Series, lags=range(0, 13)) -> Dict[int, float]:
    """corr(y_t, x_{t-k}); k>0 이면 x 가 먼저."""
    return {k: float(y.corr(x.shift(k))) for k in lags}


def best_lead(y: pd.Series, x: pd.Series, lags=range(0, 13)) -> dict:
    c = {k: v for k, v in lead_corr(y, x, lags).items() if not np.isnan(v)}
    if not c:
        return {"lag": None, "corr": None}
    k = max(c, key=lambda z: abs(c[z]))
    return {"lag": int(k), "corr": round(c[k], 3), "corr0": round(c.get(0, np.nan), 3)}
