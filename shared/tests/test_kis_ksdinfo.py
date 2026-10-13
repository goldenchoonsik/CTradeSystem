"""KIS 예탁원정보 원본 축적 — 가짜 HTTP 로만 검사(실제 KIS 호출·토큰 발급 없음)."""
import json
import os
from datetime import datetime, timedelta

import pytest

from shared.data_library import kis_ksdinfo as kk

CRED = {"token": "t", "app_key": "k", "app_secret": "s", "url": "https://kis.example"}


class Resp:
    def __init__(self, body, tr_cont):
        self._b, self.headers = body, {"tr_cont": tr_cont}

    def json(self):
        return self._b


def _rows(n, start=0, date="20260930"):
    return [{"record_date": date, "sht_cd": f"{i:06d}", "per_sto_divi_amt": str(i)} for i in range(start, start + n)]


def fake_server(total=250, page=100, honor_cts=True):
    rows = sorted(_rows(total), key=lambda r: r["record_date"] + r["sht_cd"])
    calls = []

    def get(url, headers, params, timeout):
        calls.append({"url": url, "headers": headers, "params": params})
        assert "oauth2" not in url, "토큰 발급 경로 호출 금지"
        cts = params.get("CTS", "")
        i = 0
        if cts and honor_cts:
            i = next(k for k, r in enumerate(rows) if r["record_date"] + r["sht_cd"] == cts) + 1
        chunk = rows[i:i + page]
        more = i + page < len(rows)
        return Resp({"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "ok", "output1": chunk}, "F" if more else "D")
    return get, calls


def test_fetch_follows_cts_cursor():
    get, calls = fake_server(250)
    res = kk.fetch("dividend", "20260901", "20261011", CRED, http_get=get, sleep=lambda s: None)
    assert res["complete"] and len(res["rows"]) == 250 and len(res["pages"]) == 3
    assert calls[1]["params"]["CTS"] == "20260930000099" and calls[1]["headers"]["tr_cont"] == "N"
    assert calls[0]["headers"]["authorization"] == "Bearer t" and calls[0]["params"]["SHT_CD"] == ""


def test_fetch_stops_on_repeating_pages():
    get, _ = fake_server(250, honor_cts=False)
    res = kk.fetch("dividend", "20260901", "20261011", CRED, http_get=get, sleep=lambda s: None)
    assert not res["complete"] and "반복" in res["error"] and len(res["rows"]) == 100


def test_fetch_reports_api_error():
    def get(url, headers, params, timeout):
        return Resp({"rt_cd": "2", "msg_cd": "OPSQ", "msg1": "ERROR INPUT FIELD NOT FOUND [CTS]"}, "")
    res = kk.fetch("rev_split", "20260101", "20261011", CRED, http_get=get, sleep=lambda s: None)
    assert not res["complete"] and "CTS" in res["error"]


def test_save_snapshot_never_overwrites_and_locks(tmp_path):
    get, _ = fake_server(5)
    res = kk.fetch("dividend", "20260901", "20261011", CRED, http_get=get, sleep=lambda s: None)
    at = datetime(2026, 10, 11, 20, 0, 0)
    p1 = kk.save_snapshot(res, str(tmp_path), at)
    p2 = kk.save_snapshot(res, str(tmp_path), at)
    assert p1 != p2 and os.path.exists(p1) and os.path.exists(p2)
    doc = json.load(open(p1, encoding="utf-8"))
    assert doc["row_count"] == 5 and doc["pages"][0]["body"]["output1"][0]["sht_cd"] == "000000"   # 원본 그대로
    if os.name != "nt":
        assert not (os.stat(p1).st_mode & 0o222)


def test_credentials_refuse_stale_or_missing_token(tmp_path):
    tf = tmp_path / "token_info.json"
    env = {"KIS_APP_KEY": "k", "KIS_APP_SECRET": "s"}
    with pytest.raises(kk.KisUnavailable):
        kk.load_credentials(str(tf), env)
    tf.write_text(json.dumps({"access_token": "abc"}))
    assert kk.load_credentials(str(tf), env)["token"] == "abc"
    later = datetime.now() + timedelta(hours=21)
    with pytest.raises(kk.KisUnavailable):
        kk.load_credentials(str(tf), env, now=later)
    with pytest.raises(kk.KisUnavailable):
        kk.load_credentials(str(tf), {})


def test_backfill_windows_and_snapshots(tmp_path):
    get, calls = fake_server(3)
    out = kk.backfill(datetime(2026, 1, 15), datetime(2026, 3, 10), kinds=["dividend", "rev_split"], months=1,
                      root=str(tmp_path), log=lambda *_: None, cred=CRED, http_get=get, sleep=lambda s: None)
    assert [(o["kind"], o["f_dt"], o["t_dt"]) for o in out] == [
        ("dividend", "20260115", "20260131"), ("dividend", "20260201", "20260228"), ("dividend", "20260301", "20260310"),
        ("rev_split", "20260115", "20260131"), ("rev_split", "20260201", "20260228"), ("rev_split", "20260301", "20260310")]
    assert len(os.listdir(tmp_path / "dividend")) == 3 and all(o["complete"] for o in out)


def test_backfill_resume_skips_completed_windows(tmp_path):
    get, calls = fake_server(3)
    kw = dict(kinds=["dividend"], months=1, root=str(tmp_path), log=lambda *_: None, cred=CRED, http_get=get, sleep=lambda s: None)
    kk.backfill(datetime(2026, 1, 1), datetime(2026, 1, 31), **kw)
    n = len(calls)
    out = kk.backfill(datetime(2026, 1, 1), datetime(2026, 2, 28), skip_done=True, **kw)
    assert [(o["f_dt"], o["t_dt"]) for o in out] == [("20260201", "20260228")] and len(calls) == n + 1
