"""staging 기업행위 원장(1층) 생성·갱신 — 수정/무수정 일봉 비교 → DataVault/staging/vol_spike_universe/reference/corporate_actions/

  .venv/bin/python docs/research/20261011_vol_spike/build_ca_ledger.py
"""
import glob
import os
import sys
from collections import Counter

import pandas as pd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
from shared.data_library import corporate_actions as ca  # noqa: E402

STAGE = os.path.join(REPO, "DataVault", "staging", "vol_spike_universe")
LEDGER = os.path.join(STAGE, "reference", "corporate_actions")


def main():
    tot = Counter()
    kinds = Counter()
    for p in sorted(glob.glob(os.path.join(STAGE, "raw", "creon", "candle_1d_unadj", "*_1d.csv"))):
        code = os.path.basename(p)[:-len("_1d.csv")]
        adj = pd.read_csv(os.path.join(STAGE, "raw", "creon", "candle_1d", f"{code}_1d.csv"))
        raw = pd.read_csv(p)
        ev = ca.detect(adj, raw)
        ts = pd.to_datetime(raw["timestamp"])
        rep = ca.update(LEDGER, code, ev, (ts.min().strftime("%Y-%m-%d"), ts.max().strftime("%Y-%m-%d")))
        for k, v in rep.items():
            tot[k] += len(v)
        kinds.update(e["kind_hint"] for e in ev)
        err = ca.reconstruction_error(adj, raw, ca.events(LEDGER, code))
        if len(err) and err.max() > 2.0:
            tot["reconstruction_fail"] += 1
            print(f"재구성 실패 {code}: 최대 {err.max():.2f}× 반올림")
    print(f"원장 {LEDGER}: {dict(tot)} 종류 {dict(kinds)}")


if __name__ == "__main__":
    main()
