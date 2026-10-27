"""공용: 패널 읽기·OLS(Newey-West)."""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, ROOT)
from shared.data_library.paths import data_vault_path  # noqa: E402

DERIVED = data_vault_path("staging", "us_valuation", "derived")
HERE = os.path.dirname(os.path.abspath(__file__))


def panel() -> pd.DataFrame:
    p = pd.read_csv(os.path.join(DERIVED, "panel_monthly.csv"), index_col="ym")
    p.index = pd.PeriodIndex(p.index, freq="M")
    return p


def ols(y, X, nw_lags=0):
    """y ~ 1 + X. 반환 dict(b, t(NW), r2, n). X 는 DataFrame."""
    d = pd.concat([y.rename("_y"), X], axis=1).dropna()
    Y = d["_y"].values
    Z = np.column_stack([np.ones(len(d)), d.drop(columns="_y").values])
    b, *_ = np.linalg.lstsq(Z, Y, rcond=None)
    e = Y - Z @ b
    n, k = Z.shape
    ZtZi = np.linalg.inv(Z.T @ Z)
    S = (Z * e[:, None]).T @ (Z * e[:, None])
    for L in range(1, nw_lags + 1):
        w = 1 - L / (nw_lags + 1)
        G = (Z[L:] * e[L:, None]).T @ (Z[:-L] * e[:-L, None])
        S += w * (G + G.T)
    V = ZtZi @ S @ ZtZi
    r2 = 1 - (e @ e) / (((Y - Y.mean()) ** 2).sum())
    names = ["const"] + list(X.columns)
    return {"b": dict(zip(names, b)), "t": dict(zip(names, b / np.sqrt(np.diag(V)))), "r2": r2, "n": n}
