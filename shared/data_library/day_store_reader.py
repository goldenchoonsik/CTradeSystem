# -*- coding: utf-8 -*-
"""일자별 저장소(`{root}/A{sym}/YYYY-MM-DD.csv` + `.meta.json`)의 읽기 구현 — 틱(tick_store)과 1분봉(candle_store)이 공유한다.

쓰는 쪽은 CAiModelBaker/core/tick_day_store.py(엔진)이고, 여기는 읽기만 한다. 소비자는 두 창구 모듈(tick_store / candle_store)만 거치고
데이터가 디스크에 어떻게 놓였는지 알 필요가 없다.

경로로 원천을 다루는 소비자(헬스 매트릭스·품질 감사·원천 탐색)를 위해 종목마다 '논리 경로' `{root}/{stem}{suffix}`(틱: A069500_tick.csv,
1분봉: A069500_1m.csv · 10100_1m.csv)를 둔다. 디스크에 그 파일은 없다 — symbol_from_path / path_has_data 가 그 경로를 종목으로 바꿔 연결한다.
"""
import hashlib
import json
import os
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd


def core_symbol(symbol: str) -> str:
    s = str(symbol).strip()
    if s.upper().startswith("A") and len(s) >= 7:
        s = s[1:]
    return s


class DayStoreReader:
    def __init__(self, root_fn: Callable[[], str], file_suffix: str, volume_key: str):
        self._root_fn = root_fn            # 호출 때마다 조회한다(테스트·설정 변경이 반영되도록)
        self.file_suffix = file_suffix
        self.volume_key = volume_key

    # ── 경로
    def root(self) -> str:
        return self._root_fn()

    def day_dir(self, symbol: str) -> str:
        return os.path.join(self.root(), f"A{core_symbol(symbol)}")

    def symbol_from_path(self, path: Optional[str]) -> Optional[str]:
        """논리 경로 `<root>/<이름><suffix>` 이면 종목 코드, 아니면 None"""
        if not path:
            return None
        base = os.path.basename(path)
        if not (base.endswith(self.file_suffix) and os.path.abspath(os.path.dirname(path)) == os.path.abspath(self.root())):
            return None
        stem = base[: -len(self.file_suffix)]
        return core_symbol(stem) if stem else None

    # ── 메타
    @staticmethod
    def _read_json(path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def day_metas(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        d = self.day_dir(symbol)
        out: Dict[str, Dict[str, Any]] = {}
        if not os.path.isdir(d):
            return out
        for fn in os.listdir(d):
            if fn.endswith(".meta.json"):
                m = self._read_json(os.path.join(d, fn))
                if m and m.get("date"):
                    out[m["date"]] = m
        return dict(sorted(out.items()))

    def has_data(self, symbol: str) -> bool:
        d = self.day_dir(symbol)
        return os.path.isdir(d) and any(f.endswith(".csv") for f in os.listdir(d))

    def path_has_data(self, path: Optional[str]) -> bool:
        """경로로 존재를 묻는 소비자용 — 일자 저장소가 있으면 True, 없으면 실제 파일의 존재·크기로 답한다."""
        sym = self.symbol_from_path(path)
        if sym and self.has_data(sym):
            return True
        return bool(path and os.path.exists(path) and os.path.getsize(path) > 0)      # 일자 폴더가 없으면 실제 파일(전환 전 통합 CSV)이 답한다

    def days(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """{날짜: {status, rows, volume, sha256, first, last, ratio, reason}} — 날짜 오름차순(LOST 포함)."""
        return {d: {"status": m.get("status", "OPEN"), "rows": m.get("rows"), "volume": m.get(self.volume_key),
                    "sha256": m.get("sha256"), "first": m.get("first"), "last": m.get("last"),
                    "ratio": m.get("match_ratio"), "reason": m.get("deviation_reason") or m.get("reason")}
                for d, m in self.day_metas(symbol).items()}

    def dates(self, symbol: str, include_lost: bool = False) -> set:
        """데이터가 실제로 있는 날짜 집합"""
        return {d for d, e in self.days(symbol).items() if e["status"] != "LOST" or include_lost}

    def last_timestamp(self, symbol: str) -> Optional[str]:
        lasts = [m.get("last") for m in self.day_metas(symbol).values() if m.get("status") != "LOST" and m.get("last")]
        return max(lasts) if lasts else None

    def pending_conflicts(self, symbol: str) -> List[Dict[str, Any]]:
        """'확인 필요' 항목(YYYY-MM-DD.csv.conflicts.json, status=pending)"""
        d = self.day_dir(symbol)
        out: List[Dict[str, Any]] = []
        if not os.path.isdir(d):
            return out
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".csv.conflicts.json"):
                j = self._read_json(os.path.join(d, fn)) or {}
                out.extend(c for c in j.get("items", []) if c.get("status") == "pending")
        return out

    def signature(self, symbol: str) -> str:
        """캐시 키 — 일자 폴더 안 파일들의 (이름, 크기, mtime) 해시. 디렉터리 mtime 은 덮어쓰기에 변하지 않아 쓰지 않는다."""
        parts: List[str] = []
        d = self.day_dir(symbol)
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                st = os.stat(os.path.join(d, fn))
                parts.append(f"{fn}|{st.st_size}|{st.st_mtime_ns}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    # ── 읽기
    def read(self, symbol: str, start: Optional[str] = None, end: Optional[str] = None,
             columns: Optional[Iterable[str]] = None, **read_csv_kw: Any) -> pd.DataFrame:
        """DataFrame 으로. start/end 는 'YYYY-MM-DD'(양 끝 포함). 날짜 오름차순, 날 안은 파일 순서. read_csv_kw 는 일자 파일마다 pd.read_csv 에 전달한다."""
        cols = list(columns) if columns else None
        d = self.day_dir(symbol)
        want = [day for day, m in self.day_metas(symbol).items()
                if (start is None or day >= start) and (end is None or day <= end)
                and m.get("status") != "LOST" and os.path.exists(os.path.join(d, f"{day}.csv"))]
        frames = [pd.read_csv(os.path.join(d, f"{day}.csv"), usecols=cols, **read_csv_kw) for day in want]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols or [])

    def read_csv(self, path: str, **kw: Any) -> pd.DataFrame:
        """`pd.read_csv(path, ...)` 의 대체 — 일자 저장소가 있으면 그 전체를, 없으면 그 파일을 읽는다(usecols 등 인자는 그대로)."""
        sym = self.symbol_from_path(path)
        if sym is None or not self.has_data(sym):          # 일자 폴더가 있으면 그것이 우선, 없으면 실제 파일(전환 전 통합 CSV)
            return pd.read_csv(path, **kw)
        usecols = kw.pop("usecols", None)
        return self.read(sym, columns=usecols, **kw)
