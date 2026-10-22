"""원장 규약 — 고정 변수 목록과 발표 시차. 변수를 더하거나 바꾸면 PLAN.md 기록에 날짜와 이유를 남긴다.

lag_days: 기간 끝(일봉=그날, 주=주말일, 월=그 달 말일)부터 값이 공개돼 쓸 수 있게 되기까지 걸리는 날 수(보수적).
          None = 실시간으로 알 수 없는 값(예: NBER 경기침체 판정) → 정답 라벨로만 쓰고 신호로 쓰지 않는다.
source:   fred(id) · yahoo(심볼, 수정 종가) · ecos(통계표/주기/항목1/항목2) · creon_index(kr_equity 원천의 지수 일봉)
"""
from typing import Dict, List

BLOCKS = {"WORLD": "세계", "US": "미국", "CN": "중국", "EU": "유럽", "JP": "일본", "KR": "한국"}

SERIES: List[dict] = [
    # ---------------------------------------------------------------- 세계
    dict(key="w_cli_g7", block="WORLD", name="G7 경기선행지수(OECD)", source="fred", sid="G7LOLITOAASTSAM", freq="M", lag_days=20, kind="cli"),
    dict(key="w_dxy", block="WORLD", name="달러 인덱스", source="yahoo", sid="DX-Y.NYB", freq="D", lag_days=0, kind="fx"),
    dict(key="w_vix", block="WORLD", name="VIX 변동성", source="yahoo", sid="^VIX", freq="D", lag_days=0, kind="vol"),
    dict(key="w_copper", block="WORLD", name="구리 가격(IMF 월평균)", source="fred", sid="PCOPPUSDM", freq="M", lag_days=5, kind="commodity"),
    dict(key="w_copper_f", block="WORLD", name="구리 선물(일봉)", source="yahoo", sid="HG=F", freq="D", lag_days=0, kind="commodity"),
    dict(key="w_oil", block="WORLD", name="WTI 유가", source="fred", sid="DCOILWTICO", freq="D", lag_days=1, kind="commodity"),
    dict(key="w_gold", block="WORLD", name="금 선물", source="yahoo", sid="GC=F", freq="D", lag_days=0, kind="commodity"),
    # ---------------------------------------------------------------- 미국
    dict(key="us_spx", block="US", name="S&P500", source="yahoo", sid="^GSPC", freq="D", lag_days=0, kind="equity"),
    dict(key="us_ndx", block="US", name="나스닥100", source="yahoo", sid="^NDX", freq="D", lag_days=0, kind="equity"),
    dict(key="us_10y", block="US", name="미 국채 10년", source="fred", sid="DGS10", freq="D", lag_days=1, kind="rate"),
    dict(key="us_curve", block="US", name="장단기 금리차(10년−3개월)", source="fred", sid="T10Y3M", freq="D", lag_days=1, kind="rate"),
    dict(key="us_baa", block="US", name="신용 스프레드(BAA−10년)", source="fred", sid="BAA10Y", freq="D", lag_days=1, kind="credit"),
    dict(key="us_hy", block="US", name="하이일드 스프레드(FRED 최근 3년만 제공)", source="fred", sid="BAMLH0A0HYM2", freq="D", lag_days=1, kind="credit"),
    dict(key="us_ffr", block="US", name="연방기금금리(월평균)", source="fred", sid="FEDFUNDS", freq="M", lag_days=1, kind="rate"),
    dict(key="us_claims", block="US", name="신규 실업수당 청구", source="fred", sid="ICSA", freq="W", lag_days=5, kind="labor"),
    dict(key="us_sahm", block="US", name="샴 규칙(실시간)", source="fred", sid="SAHMREALTIME", freq="M", lag_days=10, kind="labor"),
    dict(key="us_indpro", block="US", name="산업생산", source="fred", sid="INDPRO", freq="M", lag_days=20, kind="activity"),
    dict(key="us_orders", block="US", name="제조업 신규주문", source="fred", sid="AMTMNO", freq="M", lag_days=40, kind="activity"),
    dict(key="us_cpi", block="US", name="소비자물가", source="fred", sid="CPIAUCSL", freq="M", lag_days=20, kind="inflation"),
    dict(key="us_cli", block="US", name="미국 경기선행지수(OECD)", source="fred", sid="USALOLITOAASTSAM", freq="M", lag_days=20, kind="cli"),
    dict(key="us_gs10", block="US", name="미 국채 10년(월평균, 1953~)", source="fred", sid="GS10", freq="M", lag_days=1, kind="rate"),
    dict(key="us_tb3m", block="US", name="미 국채 3개월(월평균, 1934~)", source="fred", sid="TB3MS", freq="M", lag_days=1, kind="rate"),
    dict(key="us_baa_m", block="US", name="BAA 회사채 수익률(월평균, 1919~)", source="fred", sid="BAA", freq="M", lag_days=1, kind="credit"),
    dict(key="us_cape", block="US", name="S&P500 CAPE(쉴러 PER)", source="multpl", sid="shiller-pe", freq="M", lag_days=90, kind="valuation"),
    dict(key="us_rec", block="US", name="NBER 경기침체(사후 판정)", source="fred", sid="USREC", freq="M", lag_days=None, kind="label"),
    # ---------------------------------------------------------------- 중국
    dict(key="cn_sse", block="CN", name="상하이종합", source="yahoo", sid="000001.SS", freq="D", lag_days=0, kind="equity"),
    dict(key="cn_usdcny", block="CN", name="달러/위안", source="fred", sid="DEXCHUS", freq="D", lag_days=1, kind="fx"),
    dict(key="cn_cli", block="CN", name="중국 경기선행지수(OECD)", source="fred", sid="CHNLOLITOAASTSAM", freq="M", lag_days=20, kind="cli"),
    dict(key="cn_exports", block="CN", name="중국 수출(달러)", source="fred", sid="XTEXVA01CNM667S", freq="M", lag_days=15, kind="trade"),
    # ---------------------------------------------------------------- 유럽(독일 대표)
    dict(key="eu_dax", block="EU", name="독일 DAX", source="yahoo", sid="^GDAXI", freq="D", lag_days=0, kind="equity"),
    dict(key="eu_stoxx", block="EU", name="유로스톡스50", source="yahoo", sid="^STOXX50E", freq="D", lag_days=0, kind="equity"),
    dict(key="eu_eurusd", block="EU", name="유로/달러", source="fred", sid="DEXUSEU", freq="D", lag_days=1, kind="fx"),
    dict(key="eu_de10y", block="EU", name="독일 국채 10년(월평균)", source="fred", sid="IRLTLT01DEM156N", freq="M", lag_days=5, kind="rate"),
    dict(key="eu_cli", block="EU", name="독일 경기선행지수(OECD)", source="fred", sid="DEULOLITOAASTSAM", freq="M", lag_days=20, kind="cli"),
    # ---------------------------------------------------------------- 일본
    dict(key="jp_n225", block="JP", name="닛케이225", source="yahoo", sid="^N225", freq="D", lag_days=0, kind="equity"),
    dict(key="jp_usdjpy", block="JP", name="달러/엔", source="fred", sid="DEXJPUS", freq="D", lag_days=1, kind="fx"),
    dict(key="jp_10y", block="JP", name="일본 국채 10년(월평균)", source="fred", sid="IRLTLT01JPM156N", freq="M", lag_days=5, kind="rate"),
    dict(key="jp_cli", block="JP", name="일본 경기선행지수(OECD)", source="fred", sid="JPNLOLITOAASTSAM", freq="M", lag_days=20, kind="cli"),
    # ---------------------------------------------------------------- 한국
    dict(key="kr_kospi", block="KR", name="코스피", source="creon_index", sid="U001", freq="D", lag_days=0, kind="equity", extend_with="^KS11"),
    dict(key="kr_k200", block="KR", name="코스피200", source="creon_index", sid="U180", freq="D", lag_days=0, kind="equity", extend_with="^KS200"),
    dict(key="kr_usdkrw", block="KR", name="원/달러(매매기준율)", source="ecos", sid="731Y001/D/0000001", freq="D", lag_days=0, kind="fx"),
    dict(key="kr_10y", block="KR", name="국고채 10년(월)", source="ecos", sid="721Y001/M/5050000", freq="M", lag_days=5, kind="rate"),
    dict(key="kr_3y", block="KR", name="국고채 3년(월)", source="ecos", sid="721Y001/M/5020000", freq="M", lag_days=5, kind="rate"),
    dict(key="kr_call", block="KR", name="콜금리(월)", source="ecos", sid="721Y001/M/1010000", freq="M", lag_days=5, kind="rate"),
    dict(key="kr_cli", block="KR", name="한국 선행지수 순환변동치(통계청)", source="ecos", sid="901Y067/M/I16E", freq="M", lag_days=35, kind="cli"),
    dict(key="kr_cli_oecd", block="KR", name="한국 경기선행지수(OECD)", source="fred", sid="KORLOLITOAASTSAM", freq="M", lag_days=20, kind="cli"),
    dict(key="kr_exp_total", block="KR", name="수출 총액(통관)", source="fred", sid="XTEXVA01KRM667S", freq="M", lag_days=15, kind="trade"),
    dict(key="kr_exp_cn", block="KR", name="대중국 수출", source="ecos", sid="901Y121/M/T002/CN", freq="M", lag_days=15, kind="trade"),
    dict(key="kr_exp_hk", block="KR", name="대홍콩 수출", source="ecos", sid="901Y121/M/T002/HK", freq="M", lag_days=15, kind="trade"),
    dict(key="kr_exp_us", block="KR", name="대미국 수출", source="ecos", sid="901Y121/M/T002/US", freq="M", lag_days=15, kind="trade"),
    dict(key="kr_exp_jp", block="KR", name="대일본 수출", source="ecos", sid="901Y121/M/T002/JP", freq="M", lag_days=15, kind="trade"),
    dict(key="kr_exp_vn", block="KR", name="대베트남 수출", source="ecos", sid="901Y121/M/T002/VN", freq="M", lag_days=15, kind="trade"),
    dict(key="kr_exp_tw", block="KR", name="대대만 수출", source="ecos", sid="901Y121/M/T002/TW", freq="M", lag_days=15, kind="trade"),
]

BY_KEY: Dict[str, dict] = {s["key"]: s for s in SERIES}
EQUITY = {"US": "us_spx", "EU": "eu_dax", "JP": "jp_n225", "CN": "cn_sse", "KR": "kr_kospi"}   # 국가 대표 지수(연결도·사건표)


def validate() -> List[str]:
    """규약 자체 점검 — 키 중복·필수 필드·블록·주기."""
    errs, seen = [], set()
    for s in SERIES:
        for f in ("key", "block", "name", "source", "sid", "freq", "kind"):
            if not s.get(f):
                errs.append(f"{s.get('key')}: {f} 없음")
        if s["key"] in seen:
            errs.append(f"{s['key']}: 중복")
        seen.add(s["key"])
        if s["block"] not in BLOCKS:
            errs.append(f"{s['key']}: 블록 {s['block']}")
        if s["freq"] not in ("D", "W", "M"):
            errs.append(f"{s['key']}: 주기 {s['freq']}")
        if s["lag_days"] is None and s["kind"] != "label":
            errs.append(f"{s['key']}: 시차 없음은 label 만")
        if not s["key"].startswith(s["block"].lower()[:2] if s["block"] != "WORLD" else "w_"):
            errs.append(f"{s['key']}: 접두사가 블록과 다름")
    for k in EQUITY.values():
        if k not in BY_KEY:
            errs.append(f"대표 지수 {k} 없음")
    return errs
