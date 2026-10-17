"""signal_test.csv / signal_test_lm.csv / metrics_lm.csv → 결과 페이지 report.html (artifact 원본)"""
import pandas as pd, numpy as np, os, html
D = os.path.dirname(os.path.abspath(__file__))
A = pd.read_csv(f"{D}/signal_test.csv"); B = pd.read_csv(f"{D}/signal_test_lm.csv"); L = pd.read_csv(f"{D}/metrics_lm.csv")
NAME = {"neg":"부정어","pos":"긍정어","unc":"불확실성어","lit":"소송어","con":"제약어","tone":"순감성"}
def fname(f):
    for k, v in (("prev_","직전 대비 "),("yoy_","작년 대비 "),("dev_","과거평균 대비 ")):
        if f.startswith(k): s = f[len(k):]; a, b = s.split("_"); return v + ("MD&A " if a=="mda" else "위험요인 ") + NAME[b]
    return f
def tbl(R, n, lm):
    t = R.sort_values("p").head(n)
    rows = "".join(f"<tr><td>{r.form}</td><td>{html.escape(fname(r.feature) if lm else r.feature)}</td><td class=n>{r.h}일</td><td class=n>{r.n}</td><td class=n>{r.rho:+.2f}</td><td class=n>{r.p:.4f}</td></tr>" for r in t.itertuples())
    return f"<div class=sc><table><thead><tr><th>양식</th><th>지표</th><th class=n>보유</th><th class=n>표본</th><th class=n>순위상관</th><th class=n>p값</th></tr></thead><tbody>{rows}</tbody></table></div>"
def hist(p, thr_note):
    bins = np.linspace(0, 1, 11); c, _ = np.histogram(p, bins); exp = len(p) / 10; W, H, m = 560, 190, 34
    ymax = max(c.max(), exp) * 1.15; bw = (W - m) / 10
    bars = "".join(f'<rect x="{m+i*bw+2:.1f}" y="{H-24-(v/ymax)*(H-40):.1f}" width="{bw-4:.1f}" height="{(v/ymax)*(H-40):.1f}" fill="var(--bar)"/><text x="{m+i*bw+bw/2:.1f}" y="{H-8}" text-anchor="middle" class=t>{i/10:.1f}</text><text x="{m+i*bw+bw/2:.1f}" y="{H-28-(v/ymax)*(H-40):.1f}" text-anchor="middle" class=t>{v}</text>' for i, v in enumerate(c))
    ey = H - 24 - (exp / ymax) * (H - 40)
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="p값 분포"><line x1="{m}" x2="{W}" y1="{ey:.1f}" y2="{ey:.1f}" stroke="var(--ref)" stroke-dasharray="4 3"/><text x="{m-4}" y="{ey+3:.1f}" text-anchor="end" class=t>{exp:.0f}</text>{bars}</svg>'
nlm = len(B); lo = B[B.p < .05]
tick = L.groupby("ticker")[["mda_neg","mda_unc","rf_neg","rf_unc"]].mean().round(1)
trows = "".join(f"<tr><td>{t}</td><td class=n>{r.mda_neg}</td><td class=n>{r.mda_unc}</td><td class=n>{r.rf_neg}</td><td class=n>{r.rf_unc}</td></tr>" for t, r in tick.iterrows())
page = f'''<title>SEC 공시 글 신호 검증</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
:root{{--bg:#f6f7f9;--fg:#1c2330;--mut:#5d6779;--line:#d9dee7;--card:#fff;--acc:#1f5fbf;--bad:#b3261e;--bar:#7f95b8;--ref:#b3261e;--chip:#eaf0fa;--font:"IBM Plex Sans KR","Noto Sans KR",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,monospace}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#12161d;--fg:#e4e8ef;--mut:#9aa5b8;--line:#2a3140;--card:#181d27;--acc:#7aa7ee;--bad:#ff8a80;--bar:#5c7399;--ref:#ff8a80;--chip:#1e2a40;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#12161d;--fg:#e4e8ef;--mut:#9aa5b8;--line:#2a3140;--card:#181d27;--acc:#7aa7ee;--bad:#ff8a80;--bar:#5c7399;--ref:#ff8a80;--chip:#1e2a40;color-scheme:dark}}
body{{background:var(--bg);color:var(--fg);font-family:var(--font);line-height:1.6;padding-inline:max(16px,env(safe-area-inset-left,0px)) max(16px,env(safe-area-inset-right,0px));padding-block:28px 48px}}
main{{max-width:820px;margin:0 auto;display:flex;flex-direction:column;gap:28px}}
h1{{font-size:1.7rem;line-height:1.25;margin:0 0 6px;text-wrap:balance}} h2{{font-size:1.1rem;margin:0 0 10px}}
p{{margin:0 0 10px;max-width:68ch}} .mut{{color:var(--mut);font-size:.9rem}}
.verdict{{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:16px 18px}}
.verdict b.v{{color:var(--bad)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:12px}}
.kpi{{border-top:2px solid var(--line);padding-top:6px}} .kpi .x{{font:600 1.5rem var(--mono);font-variant-numeric:tabular-nums}} .kpi .l{{font-size:.8rem;color:var(--mut)}}
.sc{{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--card)}}
table{{border-collapse:collapse;width:100%;font-size:.88rem}} th,td{{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}} th{{font-size:.75rem;letter-spacing:.04em;color:var(--mut);font-weight:600}}
.n{{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}} tr:last-child td{{border-bottom:0}}
svg{{width:100%;height:auto;max-width:620px;display:block}} svg .t{{font:10px var(--mono);fill:var(--mut)}}
ul{{margin:0;padding-left:1.2em}} li{{margin-bottom:4px;max-width:68ch}}
</style>
<main>
<header><h1>SEC 공시 글 신호 검증</h1><p class=mut>M7(AAPL·MSFT·GOOGL·AMZN·META·NVDA·TSLA) 10-K 76건·10-Q 225건, 2016-01~2026-08 · 코드로만 지표화, 본문은 읽지 않음</p></header>
<section class=verdict><p><b class=v>결론: 신호 없음.</b> 공시 문장의 어조·변화량은 공시 후 1~63거래일 QQQ 대비 초과수익을 예측하지 못했습니다. 사전에 정한 합격선을 넘은 지표가 0개입니다.</p>
<div class=kpis><div class=kpi><div class=x>{nlm}</div><div class=l>LM 사전 검정 수</div></div><div class=kpi><div class=x>0</div><div class=l>합격 (p&lt;{0.05/nlm:.5f})</div></div><div class=kpi><div class=x>{len(lo)} / {nlm*.05:.1f}</div><div class=l>p&lt;0.05 관측 / 우연 기대</div></div><div class=kpi><div class=x>{B.p.min():.4f}</div><div class=l>최소 p값</div></div></div></section>
<section><h2>방법</h2><ul>
<li>진입은 접수일 다음 거래일 종가, 청산은 1·5·21·63거래일 뒤. 수익은 QQQ를 뺀 초과수익, 분할은 환산했습니다.</li>
<li>사전: Loughran-McDonald 금융 감성 사전(부정·긍정·불확실성·소송·제약어), 원형 일치. 구간은 MD&amp;A와 위험요인.</li>
<li>변화량 3종: 직전 같은 양식 대비, 작년 같은 시기 대비, 종목별 과거평균 대비(미래참조 없음). 지표와 초과수익의 스피어만 순위상관을 10-K·10-Q 따로 계산했습니다.</li>
<li>합격선은 결과를 보기 전에 정했습니다: p &lt; 0.05 ÷ 검정 수, 그리고 10-K·10-Q의 부호 일치.</li></ul></section>
<section><h2>p값 분포 (LM 사전, {nlm}개)</h2>{hist(B.p, "")}
<p class=mut>효과가 없으면 p값은 0~1에 고르게 퍼집니다(점선 = 구간당 {nlm/10:.0f}개). 왼쪽(작은 p)이 눈에 띄게 높지 않으면 우연의 분포입니다.</p></section>
<section><h2>LM 사전 상위 12개</h2>{tbl(B, 12, True)}
<p class=mut>1위(위험요인 불확실성어 변화 → 63일, 표본 68, rho −0.36)도 p=0.0023으로 합격선 {0.05/nlm:.5f}의 13배입니다. 10-K·10-Q가 같은 부호로 둘 다 p&lt;0.1인 조합은 1개뿐(직전 대비 위험요인 제약어, 5일)이었습니다.</p></section>
<section><h2>1차(임시 단어목록·키워드) 상위 6개</h2>{tbl(A, 6, False)}
<p class=mut>96개 검정 중 p&lt;0.05가 2개(기대 4.8), Bonferroni 0.001 통과 0개.</p></section>
<section><h2>종목별 LM 수준 (단어 1,000개당 평균)</h2><div class=sc><table><thead><tr><th>종목</th><th class=n>MD&amp;A 부정</th><th class=n>MD&amp;A 불확실</th><th class=n>위험요인 부정</th><th class=n>위험요인 불확실</th></tr></thead><tbody>{trows}</tbody></table></div>
<p class=mut>추출이 정상이라는 확인용입니다. 10-K MD&amp;A 부정어 평균은 0.95%로 LM 논문의 일반 범위입니다.</p></section>
<section><h2>한계와 판단</h2><ul>
<li>종목이 7개뿐이고 10-K는 종목당 약 11건이라 약한 효과는 걸러내지 못합니다. 신호가 없다는 말은 &quot;이 표본에서 잡히지 않는다&quot;입니다.</li>
<li>대형 기술주의 공시 문체는 법무팀이 다듬은 정형 문장이 많아 변화량이 작은 편입니다.</li>
<li>결과가 한 방향으로 기울지도 않았고, 1차 검증과도 일치합니다. 전 종목 확장(원본 100GB+, 추정)을 정당화할 근거는 아직 없습니다.</li>
<li>남은 선택지: FinBERT 문장 분류(설치·연산 비용 큼), 또는 종결.</li></ul></section>
<p class=mut>재현: docs/research/20261016_sec_text/ — metrics_all.py, metrics_lm.py, signal_test.py, signal_test_lm.py, make_report.py</p>
</main>
'''
open(f"{D}/report.html", "w").write(page); print(len(page), nlm, len(lo))
