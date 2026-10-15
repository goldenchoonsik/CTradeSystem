# -*- coding: utf-8 -*-
"""
CTradeSystem - Raw Store Health Matrix Engine
==============================================================================
Raw Store에 등록된 전체 자산의 결측일을 자산×날짜 매트릭스로 계산한다.

ManifestManager의 매니페스트(SHA-256 무결성 감사 기록)는 불변 박제 원칙이라
대시보드 편의용 결측 계산을 얹지 않는다. 이 모듈은 그와 별개의 파생 캐시로,
원본 CSV의 mtime이 바뀌면 자동으로 무효화되고 언제든 재계산 가능하다.
"""

import os
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from shared.data_library.paths import data_vault_path
from shared.data_library import tick_store
from shared.data_library import candle_store
from shared.core.market_calendar import get_market_holidays

_STOCK_CATEGORIES_US = {"US_STOCK", "US_ETF", "OVERSEAS_FUTURES", "GLOBAL_INDEX_CFD_PROXY"}
_CRYPTO_CATEGORIES = {"CRYPTO_SPOT", "CRYPTO_FUTURES"}

_CACHE_PATH = data_vault_path("temp", "cache", "raw_store_health_cache.json")

# 우선순위: 사람이 읽을 수 있는 일자 컬럼이 있으면 그걸 신뢰한다 (epoch → TZ 변환 오차 방지).
_DATE_COLUMN_PRIORITY = ("datetime", "date", "timestamp")


def _parse_date_token(val: str) -> Optional[str]:
    """셀 값 하나에서 YYYY-MM-DD를 뽑는다. ISO 문자열 또는 epoch(초/밀리초) 숫자를 지원."""
    val = val.strip().strip('"')
    if len(val) >= 10 and val[4] == "-" and val[7] == "-":
        return val[:10]
    try:
        num = float(val)
    except (TypeError, ValueError):
        return None
    # 밀리초(13자리대) vs 초(10자리대) epoch 판별
    if num > 1e11:
        num /= 1000.0
    if num <= 0:
        return None
    try:
        return datetime.fromtimestamp(num, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _extract_dates_from_csv(file_path: str) -> set:
    """CSV에서 일자(YYYY-MM-DD) 집합을 추출한다.
    성능 최적화: C 기반 pandas/pyarrow 엔진으로 필요한 컬럼만 고속 추출하며, 실패 시 폴백.
    """
    dates = set()
    if not file_path or not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return dates

    # 1. 고속 C 엔진 (Pandas) 우선 시도 (350만 행 기준 15초 -> 0.3초)
    try:
        import pandas as pd
        header = pd.read_csv(file_path, nrows=0)
        cols = [c.strip().replace('"', "") for c in header.columns]
        target_col = None
        for cand in _DATE_COLUMN_PRIORITY:
            if cand in cols:
                target_col = cand
                break
        if target_col:
            # 타겟 컬럼만 문자열로 고속 로드
            series = pd.read_csv(file_path, usecols=[target_col], dtype=str)[target_col]
            clean = series.dropna().str.strip().str.strip('"')
            # ISO 날짜 포맷 (YYYY-MM-DD...)인 경우 초고속 슬라이스
            if not clean.empty and len(clean.iloc[0]) >= 10 and clean.iloc[0][4] == "-" and clean.iloc[0][7] == "-":
                return set(clean.str.slice(0, 10).unique())
            # epoch 숫자 포맷인 경우 벡터화 파싱
            nums = pd.to_numeric(clean, errors="coerce").dropna()
            if not nums.empty:
                # 밀리초 -> 초 정규화
                nums = nums.apply(lambda n: n / 1000.0 if n > 1e11 else n)
                return set(pd.to_datetime(nums, unit="s", utc=True).dt.strftime("%Y-%m-%d").unique())
    except Exception:
        pass

    # 2. 폴백: 순수 파이썬 스트리밍 파서
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            header = f.readline()
            cols = [c.strip().replace('"', "") for c in header.split(",")]
            ts_idx = 0
            for cand in _DATE_COLUMN_PRIORITY:
                if cand in cols:
                    ts_idx = cols.index(cand)
                    break
            for line in f:
                if not line.strip():
                    continue
                if ts_idx == 0:
                    val = line.split(",", 1)[0]
                else:
                    parts = line.split(",", ts_idx + 1)
                    val = parts[ts_idx] if len(parts) > ts_idx else ""
                d_str = _parse_date_token(val)
                if d_str:
                    dates.add(d_str)
    except Exception:
        pass
    return dates


def _extract_dates_from_parquet(file_path: str) -> set:
    """Parquet 파일(정규화 계층 폴백 등)에서 일자 집합을 추출한다."""
    dates = set()
    if not file_path or not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return dates
    try:
        import pandas as pd
        df = pd.read_parquet(file_path, columns=None)
        col = None
        for cand in ("timestamp", "datetime", "date"):
            if cand in df.columns:
                col = cand
                break
        if col is None:
            return dates
        series = df[col]
        if not str(series.dtype).startswith(("datetime", "object")):
            series = series.astype(str)
        dates = set(pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d").dropna().unique().tolist())
    except Exception:
        pass
    return dates


def _extract_dates_from_file(file_path: str) -> set:
    ext = os.path.splitext(file_path or "")[1].lower()
    if ext in (".parquet", ".pq"):
        return _extract_dates_from_parquet(file_path)
    return _extract_dates_from_csv(file_path)


def _stamp(file_path: str):
    """캐시 무효화 키 — 일반 파일은 mtime, 틱은 구성 파일 전체의 지문(일자 파일이 덮어써져도 바뀐다)."""
    sym = tick_store.symbol_from_path(file_path)
    if sym:
        return tick_store.signature(sym)
    csym = candle_store.symbol_from_path(file_path)
    if csym and candle_store.is_day_layout(csym):          # 1분봉 일자 저장소(통합 CSV 가 없다)
        return candle_store.signature(csym)
    try:
        return os.path.getmtime(file_path)
    except OSError:
        return 0


def _ledger_unrecoverable_ranges(file_path: str) -> List[tuple]:
    """원천 CSV 옆 원장(<파일>.days.json)에 기록된 영구 결손 구간 [(after_day, before_day)] — 양 끝 날짜는 제외 구간"""
    try:
        with open(file_path + ".days.json", "r", encoding="utf-8") as f:
            led = json.load(f)
        return [(str(r["after"])[:10], str(r["before"])[:10]) for r in led.get("unrecoverable", [])]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _group_ranges(date_strings: List[str]) -> List[Dict[str, Any]]:
    """연속된(3일 이내 간격) 결측일들을 구간으로 묶는다."""
    if not date_strings:
        return []
    ranges = []
    start = prev = date_strings[0]
    count = 1
    for d_str in date_strings[1:]:
        d_curr = datetime.strptime(d_str, "%Y-%m-%d")
        d_prev = datetime.strptime(prev, "%Y-%m-%d")
        if (d_curr - d_prev).days <= 3:
            prev = d_str
            count += 1
        else:
            ranges.append({"start": start, "end": prev, "count": count})
            start = prev = d_str
            count = 1
    ranges.append({"start": start, "end": prev, "count": count})
    return ranges


# 품질 판정 -> 매트릭스 슬롯 상태. 파일에 날짜가 있어도(found) 데이터가 믿을 수 없으면 정상으로 세지 않는다.
_QUALITY_SLOT_STATUS = {"bad": "degraded", "warn": "suspect"}


def _overlay_quality(slots: List[Dict[str, Any]], quality_days: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """found 슬롯에 품질 판정을 덧입힌 사본을 돌려준다 (원본/캐시는 건드리지 않는다). missing/holiday 등은 그대로."""
    out = []
    for slot in slots:
        v = quality_days.get(slot["date"])
        status = _QUALITY_SLOT_STATUS.get(v["status"]) if v and slot["status"] == "found" else None
        if status:
            out.append({**slot, "status": status, "quality": v["status"], "reasons": v.get("reasons", [])[:3]})
        else:
            out.append(slot)
    return out


def _overlay_quality_calendar(
    calendar_by_year: Optional[Dict[str, List[Dict[str, Any]]]],
    quality_days: Dict[str, Dict[str, Any]]
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """2D 잔디 캘린더의 각 셀에 품질 판정(degraded, suspect, reasons)을 덧입힌 사본을 반환한다."""
    if not calendar_by_year or not quality_days:
        return calendar_by_year
    out = {}
    for y_str, cell_list in calendar_by_year.items():
        new_cells = []
        for cell in cell_list:
            d_str = cell.get("date")
            v = quality_days.get(d_str)
            if v and cell.get("status") == "found":
                status = _QUALITY_SLOT_STATUS.get(v.get("status"))
                if status:
                    new_cells.append({
                        **cell,
                        "status": status,
                        "quality": v.get("status"),
                        "reasons": v.get("reasons", [])[:3]
                    })
                    continue
            new_cells.append(cell)
        out[y_str] = new_cells
    return out


def _quality_view(
    entry: Optional[Dict[str, Any]],
    slots: Optional[List[Dict[str, Any]]],
    calendar_by_year: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """감사 결과(엔트리)를 화면용 필드로 변환한다. 감사가 아직 없으면 checked=False (정상이라는 뜻이 아니다)."""
    if not entry:
        return {"quality_checked": False, "quality_bad_count": None, "quality_warn_count": None}
    days = entry.get("days", {})
    shown = _overlay_quality(slots, days) if slots else slots
    shown_cal = _overlay_quality_calendar(calendar_by_year, days) if calendar_by_year else calendar_by_year
    summary = entry.get("summary", {})
    window_dates = {sl["date"] for sl in (slots or [])}
    bad = [d for d in summary.get("bad_days", []) if not window_dates or d in window_dates]
    warn = [d for d in summary.get("warn_days", []) if not window_dates or d in window_dates]
    all_bad = summary.get("bad_days", [])
    all_warn = summary.get("warn_days", [])

    import re
    # 사유별 건수 동적 집계 (뒤쪽의 변동 수치/bps 숫자를 정규화하여 공통 패턴으로 그룹핑)
    reason_counts: Dict[str, int] = {}
    for d in (all_bad + all_warn):
        if d in days:
            for r in days[d].get("reasons", []):
                norm_r = re.sub(r'\s+[\d\.]+$', '', str(r)).strip()
                reason_counts[norm_r] = reason_counts.get(norm_r, 0) + 1

    res = {
        "quality_checked": True,
        "quality_bad_count": len(bad),
        "quality_warn_count": len(warn),
        "quality_bad_days": bad,
        "quality_warn_days": warn,
        "all_quality_bad_count": len(all_bad),
        "all_quality_warn_count": len(all_warn),
        "all_quality_bad_days": all_bad,
        "all_quality_warn_days": all_warn,
        "quality_reasons": {d: days[d].get("reasons", [])[:3] for d in (bad + warn) if d in days},
        "all_quality_reasons": {d: days[d].get("reasons", [])[:3] for d in (all_bad[:30] + all_warn[:30]) if d in days},
        "quality_reason_summary": sorted(
            [{"reason": k, "count": v} for k, v in reason_counts.items()],
            key=lambda x: x["count"],
            reverse=True
        ),
        "slots": shown,
    }
    if shown_cal is not None:
        res["calendar_by_year"] = shown_cal
    return res


_KST_TZ = __import__("zoneinfo").ZoneInfo("Asia/Seoul")


class RawStoreHealthEngine:
    """자산별 결측일 계산 결과를 원본 파일 mtime 기준으로 캐싱한다 (메모리 + 디스크)."""

    _mem_cache: Dict[str, Dict[str, Any]] = {}
    _file_dates_cache: Dict[str, Dict[str, Any]] = {}
    _loaded_from_disk = False
    _saved_at: Optional[str] = None

    @classmethod
    def _load_disk_cache(cls):
        if cls._loaded_from_disk:
            return
        cls._loaded_from_disk = True
        try:
            if os.path.exists(_CACHE_PATH):
                with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                    disk_data = json.load(f)
                    cls._mem_cache = disk_data.get("matrix", {})
                    cls._file_dates_cache = disk_data.get("file_dates", {})
                    cls._saved_at = disk_data.get("saved_at")
        except Exception:
            cls._mem_cache = {}
            cls._file_dates_cache = {}
            cls._saved_at = None

    @classmethod
    def _save_disk_cache(cls):
        """원자적(Atomic) 파일 저장으로 쓰기 도중 프로세스 중단 시 파일 손상 방지"""
        try:
            os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
            cls._saved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            temp_path = f"{_CACHE_PATH}.tmp.{os.getpid()}"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump({
                    "saved_at": cls._saved_at,
                    "matrix": cls._mem_cache,
                    "file_dates": cls._file_dates_cache
                }, f, ensure_ascii=False)
            os.replace(temp_path, _CACHE_PATH)
        except Exception:
            pass

    @classmethod
    def clear_cache(cls):
        """메모리 캐시를 비워 다음 요청 시 파일 재스캔 및 최신 품질 결과가 오버레이되도록 한다."""
        cls._mem_cache.clear()
        cls._file_dates_cache.clear()
        cls._loaded_from_disk = False

    @classmethod
    def get_cached_dates_for_file(cls, file_path: str) -> set:
        """파일 mtime/지문 기준 고유 날짜 집합 캐시 (불변 시 0.0001초 반환)"""
        cls._load_disk_cache()
        if not file_path:
            return set()

        tick_sym = tick_store.symbol_from_path(file_path)
        candle_sym = candle_store.symbol_from_path(file_path)

        if tick_sym:
            mtime = tick_store.signature(tick_sym) if tick_store.has_data(tick_sym) else 0
        elif candle_sym and candle_store.is_day_layout(candle_sym):
            mtime = candle_store.signature(candle_sym)
        elif os.path.exists(file_path):
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                mtime = 0
        else:
            return set()

        cached = cls._file_dates_cache.get(file_path)
        if cached and cached.get("mtime") == mtime and "dates" in cached:
            return set(cached["dates"])

        # 캐시 미스 시 추출 후 캐싱
        if tick_sym:
            extracted = tick_store.dates(tick_sym) if tick_store.has_data(tick_sym) else set()
        elif candle_sym and candle_store.is_day_layout(candle_sym):
            extracted = candle_store.dates(candle_sym)
        else:
            extracted = _extract_dates_from_file(file_path)

        cls._file_dates_cache[file_path] = {
            "mtime": mtime,
            "dates": sorted(extracted)
        }
        return extracted

    @classmethod
    def _holiday_calendar_for(cls, category: str) -> str:
        return "US" if (category or "").upper() in _STOCK_CATEGORIES_US else "KRX"

    @classmethod
    def _is_continuous_market(cls, category: str) -> bool:
        return (category or "").upper() in _CRYPTO_CATEGORIES

    @classmethod
    def compute_asset_health(cls, file_path: str, category: str, days: int = 120) -> Dict[str, Any]:
        """단일 자산의 최근 N영업일(암호화폐는 N일) 결측 현황 및 전체 스팬/월별/연도별 캘린더를 계산한다."""
        is_continuous = cls._is_continuous_market(category)
        holidays = set() if is_continuous else get_market_holidays(cls._holiday_calendar_for(category))

        today = datetime.now().date()
        effective_days = 120 if days in ("all", 0, None) else int(days)
        candidates = [today - timedelta(days=i) for i in range(effective_days * 2, 0, -1)]
        if is_continuous:
            date_range = candidates[-effective_days:]
        else:
            date_range = [d for d in candidates if d.weekday() < 5][-effective_days:]

        dates_set = cls.get_cached_dates_for_file(file_path)
        lost_ranges = _ledger_unrecoverable_ranges(file_path)
        tick_sym = tick_store.symbol_from_path(file_path)
        lost_days = {d for d, e in tick_store.days(tick_sym).items() if e["status"] == "LOST"} if tick_sym else set()
        candle_sym = candle_store.symbol_from_path(file_path)
        if candle_sym and candle_store.is_day_layout(candle_sym):
            lost_days |= {d for d, e in candle_store.days(candle_sym).items() if e["status"] == "LOST"}
        # 일자 저장소(틱·1분봉)는 D+1 06:00(KST)이 지나야 그날을 완결로 저장한다 — 그 전의 날은 결측이 아니라 '대기'
        pending_after = None
        if tick_sym or (candle_sym and candle_store.is_day_layout(candle_sym)):
            from CAiModelBaker.core.tick_day_store import complete_through
            pending_after = complete_through(datetime.now(_KST_TZ))
        sorted_found = sorted(dates_set)
        min_found = sorted_found[0] if sorted_found else None
        max_found = sorted_found[-1] if sorted_found else None
        total_found_days = len(dates_set)

        slots = []
        missing_dates = []
        lost_dates = []
        found_count = 0
        for d in date_range:
            d_str = d.strftime("%Y-%m-%d")
            if d_str in dates_set:
                status = "found"
                found_count += 1
            elif not is_continuous and d_str in holidays:
                status = "holiday"
            elif min_found and d_str < min_found:
                status = "out_of_range"
            elif pending_after and d_str > pending_after:
                status = "pending"       # 아직 완결 시각 전(다음 수집에서 저장)
            elif d_str in lost_days or any(a < d_str < b for a, b in lost_ranges):
                status = "lost"          # 원장에 '영구 결손'으로 기록된 날
                lost_dates.append(d_str)
            else:
                status = "missing"
                missing_dates.append(d_str)
            slots.append({"date": d_str, "status": status})

        in_range_days = found_count + len(missing_dates)
        rate = round((found_count / in_range_days * 100), 1) if in_range_days > 0 else 100.0

        # 전체 기간(span_start ~ today)의 전체 결측/스팬 통계 및 월별 슬롯(monthly_slots), 연도별 잔디 캘린더
        monthly_slots = []
        all_missing_dates = []
        all_lost_dates = []
        calendar_by_year = {}

        if min_found:
            d_start = datetime.strptime(min_found, "%Y-%m-%d").date()
            cur = d_start
            months_map = {}

            while cur <= today:
                cur_str = cur.strftime("%Y-%m-%d")
                m_str = cur_str[:7]
                y_str = str(cur.year)

                is_biz = is_continuous or (cur.weekday() < 5)
                is_holi = (not is_continuous) and (cur_str in holidays)
                is_lost = cur_str in lost_days or any(a < cur_str < b for a, b in lost_ranges)

                if y_str not in calendar_by_year:
                    calendar_by_year[y_str] = []

                day_status = "found" if cur_str in dates_set else (
                    "holiday" if is_holi else (
                        "lost" if is_lost else (
                            "missing" if is_biz else "weekend"
                        )
                    )
                )

                if is_continuous or cur.weekday() < 5:
                    calendar_by_year[y_str].append({
                        "date": cur_str,
                        "status": day_status,
                        "weekday": cur.weekday(),
                        "month": cur.month
                    })

                if m_str not in months_map:
                    months_map[m_str] = {"total_biz": 0, "found": 0, "missing": [], "lost": 0}

                if is_biz and not is_holi:
                    months_map[m_str]["total_biz"] += 1
                    if cur_str in dates_set:
                        months_map[m_str]["found"] += 1
                    elif is_lost:
                        months_map[m_str]["lost"] += 1
                        all_lost_dates.append(cur_str)
                    else:
                        months_map[m_str]["missing"].append(cur_str)
                        all_missing_dates.append(cur_str)

                cur += timedelta(days=1)

            for m_key in sorted(months_map.keys()):
                m_info = months_map[m_key]
                m_miss = len(m_info["missing"])
                m_total = m_info["total_biz"]
                m_status = "found"
                if m_miss > 0:
                    m_status = "missing"
                elif m_total == 0:
                    m_status = "out_of_range"
                monthly_slots.append({
                    "month": m_key,
                    "status": m_status,
                    "total": m_total,
                    "found": m_info["found"],
                    "missing": m_miss,
                    "missing_dates": m_info["missing"][:10],
                    "lost": m_info["lost"]
                })

        all_in_range_days = total_found_days + len(all_missing_dates)
        all_rate = round((total_found_days / all_in_range_days * 100), 1) if all_in_range_days > 0 else 100.0

        return {
            "slots": slots,
            "missing_count": len(missing_dates),
            "missing_ranges": _group_ranges(missing_dates),
            "lost_count": len(lost_dates),
            "lost_ranges": _group_ranges(lost_dates),
            "rate": rate,
            "last_missing_date": missing_dates[-1] if missing_dates else None,
            "span_start": min_found,
            "span_end": max_found,
            "total_collected_days": total_found_days,
            "all_missing_count": len(all_missing_dates),
            "all_missing_ranges": _group_ranges(all_missing_dates),
            "all_rate": all_rate,
            "monthly_slots": monthly_slots,
            "calendar_by_year": calendar_by_year,
            "computed_at": datetime.now().isoformat(timespec="seconds"),
        }

    @classmethod
    def get_matrix(
        cls,
        days: int = 120,
        force_refresh: bool = False,
        cache_only: bool = False,
        symbols: Optional[List[str]] = None,
        return_meta: bool = False,
    ) -> Any:
        """Raw Store 전체(또는 지정된) 등록 자산의 결측 매트릭스를 반환한다.

        cache_only=True 이면 mtime이 바뀌었더라도 무거운 재계산(compute_asset_health)을 돌리지 않고,
        기존 캐시된 데이터를 즉시 반환하며 변경이 감지된 자산 목록을 메타데이터로 함께 보고한다.
        """
        from shared.data_library.source_mapping_manager import SourceMappingManager

        cls._load_disk_cache()
        registry = SourceMappingManager.get_feed_registry_status()
        if symbols:
            wanted = {s.upper() for s in symbols}
            wanted |= {SourceMappingManager.clean_symbol(s) for s in symbols}
            registry = [r for r in registry if r["symbol"].upper() in wanted or SourceMappingManager.clean_symbol(r["symbol"]) in wanted]

        results = []
        dirty = False
        updated_symbols = []

        for item in registry:
            symbol = item["symbol"]
            file_path = item.get("file_path")
            base = {
                "symbol": symbol,
                "name": item.get("name", symbol),
                "role": item.get("role", ""),
                "category": item.get("category", ""),
                "provider": item.get("primary_source", ""),
                "is_active": item.get("is_active", True),
                "archived": bool(item.get("archived", False)),
            }

            if not item.get("file_exists") or not file_path:
                base.update({
                    "slots": None,
                    "missing_count": None,
                    "missing_ranges": [],
                    "rate": None,
                    "last_missing_date": None,
                    "file_exists": False,
                })
                results.append(base)
                continue

            mtime = _stamp(file_path)

            cache_key = f"{symbol}|{file_path}|{days}"
            cached = cls._mem_cache.get(cache_key)
            is_mtime_match = bool(cached and cached.get("_mtime") == mtime)

            if cached and not is_mtime_match:
                updated_symbols.append(symbol)

            if not force_refresh and (is_mtime_match or (cache_only and cached)):
                health = cached
            else:
                health = cls.compute_asset_health(file_path, item.get("category", ""), days=days)
                health["_mtime"] = mtime
                cls._mem_cache[cache_key] = health
                dirty = True

            base.update({k: v for k, v in health.items() if not k.startswith("_")})
            base["file_exists"] = True
            # 품질 감사 결과가 이미 있으면 덧입힌다 (계산은 하지 않는다 — 목록 조회는 가벼워야 하므로 상세 화면/학습에서 계산)
            try:
                from shared.data_library.data_quality import DataQualityAuditor
                audits = DataQualityAuditor.peek_symbol(symbol)
                if audits:
                    entry = next((a for lbl, a in audits.items()
                                  if a.get("sig") == _sig(file_path) or
                                  DataQualityAuditor.granularity_paths(symbol).get(lbl) == file_path), None)
                    if not entry:
                        entry = next(iter(audits.values()), None)
                    if entry:
                        base.update(_quality_view(entry, base.get("slots"), base.get("calendar_by_year")))
            except Exception:
                base.update({"quality_checked": False})
            try:
                base["intervals"] = cls.get_intervals_for_symbol(
                    symbol, days=days, force_refresh=force_refresh, cache_only=cache_only,
                    with_quality=False, peek_quality=True, item=item
                )
            except Exception:
                base["intervals"] = []
            results.append(base)

        if dirty:
            cls._save_disk_cache()

        meta = {
            "saved_at": cls._saved_at,
            "has_updates": len(updated_symbols) > 0,
            "updated_count": len(updated_symbols),
            "updated_symbols": sorted(list(set(updated_symbols))),
            "cache_only": cache_only,
        }

        if return_meta:
            return results, meta
        return results

    @classmethod
    def get_intervals_for_symbol(
        cls, symbol: str, days: int = 120, force_refresh: bool = False, cache_only: bool = False,
        with_quality: bool = True, peek_quality: bool = False,
        item: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """단일 자산에 등록된 모든 원천 그래뉼러리티(틱/1분봉/일봉 등)의 결측 현황을 개별 반환한다.

        Raw Store 대장(get_feed_registry_status)은 심볼당 "대표 파일 1개"만 추적하므로,
        결측 매트릭스 행 하나가 보여주는 건 그 대표 파일의 결측일 뿐이다. 실제로는 같은
        심볼이 TICK/1M/1D 등 여러 그래뉼러리티로 동시에 원장에 쌓이는 경우가 많아, 상세
        패널을 펼치면 이 함수로 존재하는 그래뉼러리티를 모두 찾아 각각 따로 검증한다.
        CREON 외 제공처는 대장이 애초에 단일 파일만 추적하므로 그 1건만 반환한다.

        with_quality=True 면 각 그래뉼러리티를 상위 원천과 대조한 품질 감사(shared.data_quality)를 함께 돌려준다
        (날짜가 있어도 데이터가 부실한 날은 슬롯 상태 degraded/suspect). 첫 계산은 수 초~수십 초 걸리고 이후는 캐시.
        """
        from shared.data_library.source_mapping_manager import SourceMappingManager

        cls._load_disk_cache()
        if not item:
            registry = {r["symbol"]: r for r in SourceMappingManager.get_feed_registry_status()}
            item = registry.get(symbol)
        if not item:
            return []

        provider = (item.get("primary_source") or "").upper()
        category = item.get("category", "")
        results = []
        dirty = False

        audits = None
        if with_quality:
            from shared.data_library.data_quality import DataQualityAuditor
            audits = DataQualityAuditor.audit_symbol(symbol, force=force_refresh)
        elif peek_quality:
            from shared.data_library.data_quality import DataQualityAuditor
            audits = DataQualityAuditor.peek_symbol(symbol)   # 계산 없이 이미 있는 감사 결과만 (목록 조회는 가벼워야 한다)
        groups = SourceMappingManager.get_raw_interval_candidate_groups(symbol, provider, category)
        declared_sources = set(str(s).strip().upper() for s in (item.get("ingestion_sources") or []))

        for label, candidates in groups.items():
            found_path = next((p for p in candidates if tick_store.path_has_data(p) or candle_store.path_exists(p) or (os.path.exists(p) and os.path.getsize(p) > 0)), None)
            if not found_path:
                # 파일이 아직 없더라도 대장에 선언된 원천 스케일(ingestion_sources)이면 결측(0% 미수집) 행으로 명확히 표출
                if label.upper() in declared_sources or (label.upper() == "TICK" and "TICK" in declared_sources):
                    fallback_p = candidates[0] if candidates else ""
                    empty_health = cls.compute_asset_health(fallback_p, category, days=days)
                    empty_health.update({
                        "label": label,
                        "file_path": fallback_p,
                        "file_exists": False,
                        "rate": 0.0,
                    })
                    results.append(empty_health)
                continue
            entry, was_dirty = cls._health_for_interval(symbol, label, found_path, category, days, force_refresh, cache_only=cache_only)
            if audits is not None:
                q_entry = audits.get(label)
                if not q_entry and label in ("1M", "1m"):
                    q_entry = next(iter(audits.values()), None)
                if q_entry:
                    entry.update(_quality_view(q_entry, entry.get("slots"), entry.get("calendar_by_year")))
            results.append(entry)
            dirty = dirty or was_dirty

        # 만약 groups에서 못 찾았는데 item['file_path']가 실제로 존재하는 경우 안전 폴백
        if not results:
            file_path = item.get("file_path")
            if file_path and os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                label = item.get("ingestion_resolution", "1M")
                entry, was_dirty = cls._health_for_interval(symbol, label, file_path, category, days, force_refresh, cache_only=cache_only)
                if audits is not None:
                    q_entry = audits.get(label) or next(iter(audits.values()), None)
                    if q_entry:
                        entry.update(_quality_view(q_entry, entry.get("slots"), entry.get("calendar_by_year")))
                results.append(entry)
                dirty = dirty or was_dirty

        if dirty:
            cls._save_disk_cache()

        return results

    @classmethod
    def _health_for_interval(cls, symbol, label, file_path, category, days, force_refresh, cache_only=False):
        mtime = _stamp(file_path)
        cache_key = f"{symbol}|{file_path}|{days}"
        cached = cls._mem_cache.get(cache_key)
        dirty = False
        is_mtime_match = bool(cached and cached.get("_mtime") == mtime)
        if not force_refresh and (is_mtime_match or (cache_only and cached)):
            health = cached
        else:
            health = cls.compute_asset_health(file_path, category, days=days)
            health["_mtime"] = mtime
            cls._mem_cache[cache_key] = health
            dirty = True
        out = {"label": label, "file_path": file_path}
        out.update({k: v for k, v in health.items() if not k.startswith("_")})
        return out, dirty
