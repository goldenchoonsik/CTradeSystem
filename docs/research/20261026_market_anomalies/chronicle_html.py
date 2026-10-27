"""인터랙티브 연대기 HTML (외부 라이브러리 없음, 단일 파일): chronicle_data.json + chronicle_regime_table.csv → chronicle.html
  .venv/bin/python docs/research/20261026_market_anomalies/chronicle_html.py"""
import os, json, numpy as np, pandas as pd
OUT = os.path.dirname(os.path.abspath(__file__)); J = json.load(open(f"{OUT}/chronicle_data.json"))
R = pd.read_csv(f"{OUT}/chronicle_regime_table.csv"); cyc = R.replace({np.nan: None}).to_dict("records")
D = pd.read_csv(f"{OUT}/regime_monthly.csv"); base9 = float(((1 + D.sp.pct_change().mean()) ** 9 - 1) * 100)
J["cycles"] = cyc; J["base9"] = base9
t_ = np.arange(len(D)) / 12; b_, a_ = np.polyfit(t_, np.log(D.sp), 1); J["months"]["trend"] = [float(v) for v in np.exp(a_ + b_ * t_)]; J["trend_cagr"] = float((np.exp(b_) - 1) * 100)
HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>미국 증시 연대기</title>
<style>
:root{--bg:#fff;--ink:#1f2937;--muted:#6b7280;--grid:#e5e7eb;--card:#f8fafc;--line:#d1d5db;--sp:#1f2937;--kr:#be185d;--c10:#0f766e;--c3:#d97706;--inf:#7c3aed;--cr:#b45309;--cape:#0369a1;--dem:#93c5fd;--rep:#fca5a5;--rec:#e5e7eb;--bear:#fecaca;--war:#b45309;--crash:#dc2626;--bar:#2563eb;--barneg:#94a3b8;--btn:#eef2ff}
@media (prefers-color-scheme:dark){:root{--bg:#0f172a;--ink:#e5e7eb;--muted:#94a3b8;--grid:#1e293b;--card:#111c33;--line:#334155;--sp:#e5e7eb;--kr:#f472b6;--c10:#2dd4bf;--c3:#fbbf24;--inf:#a78bfa;--cr:#f59e0b;--cape:#38bdf8;--dem:#1d4ed8;--rep:#b91c1c;--rec:#273449;--bear:#7f1d1d;--war:#f59e0b;--crash:#f87171;--bar:#60a5fa;--barneg:#64748b;--btn:#1e293b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:16px}h1{font-size:20px;margin:4px 0}h2{font-size:16px;margin:28px 0 4px}p.sub{color:var(--muted);margin:2px 0 12px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 12px}.chip{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px}.chip b{display:block;font-size:16px}.chip span{color:var(--muted);font-size:12px}
.ctl{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0}.ctl button,.ctl label{background:var(--btn);border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:4px 10px;font:inherit;cursor:pointer}.ctl button.on{outline:2px solid var(--bar)}.ctl label{display:flex;gap:4px;align-items:center}.ctl .sep{width:1px;height:20px;background:var(--line);margin:0 4px}
#chart{position:relative;user-select:none}#chart svg{display:block;width:100%}.tip{position:absolute;pointer-events:none;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12.5px;box-shadow:0 4px 14px #0003;min-width:190px;z-index:5;display:none}.tip b{font-size:13px}.tip table{border-collapse:collapse;width:100%}.tip td{padding:1px 0}.tip td:last-child{text-align:right;font-variant-numeric:tabular-nums;padding-left:12px}
.legend{display:flex;flex-wrap:wrap;gap:12px;color:var(--muted);font-size:12.5px;margin:6px 0}.sw{display:inline-block;width:14px;height:8px;margin-right:4px;border-radius:2px;vertical-align:middle}
details{margin-top:8px}summary{cursor:pointer;color:var(--muted)}table.t{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:6px}table.t th,table.t td{border-bottom:1px solid var(--grid);padding:3px 6px;text-align:right}table.t th:first-child,table.t td:first-child{text-align:left}
.note{color:var(--muted);font-size:12.5px;margin-top:6px}text{fill:var(--muted);font-size:11px}.t-ink{fill:var(--ink)}
</style></head><body><div class="wrap">
<h1>미국 증시 연대기 1928~2026</h1>
<p class="sub">집권 정당·금리·물가·신용·밸류에이션과 S&amp;P500을 한 시간축에 겹침. 드래그=구간 확대, 마우스 올리면 십자선·수치. 사건은 사전에 정한 객관 기준만 표시(전쟁 16건 지정 목록, 약세장=고점 대비 -20%, 폭락일=하루 -7% 이하, 침체=NBER).</p>
<div class="chips" id="chips"></div>
<div class="ctl" id="ctl"></div>
<div id="chart"><svg id="svg" role="img" aria-label="미국 증시 연대기 차트"></svg><div class="tip" id="tip"></div></div>
<div class="legend" id="legend"></div>
<h2>대선 주기 24개 — 중간선거 해 10월 말 → 3년차 6월 말(9개월) 수익</h2>
<p class="sub">막대 위로 마우스를 올리면 그때의 금리·물가·CAPE 등 매크로 환경이 표시됩니다. 점선=전체 평소 9개월 수익 평균(약 6.1%).</p>
<div id="chart2"><svg id="svg2" role="img" aria-label="대선 주기별 중간선거 후 9개월 수익 막대 차트"></svg></div>
<details><summary>표로 보기 (24주기)</summary><div id="tbl"></div></details>
<p class="note">가격지수(배당 제외), S&amp;P500 월말 종가. 금리·CPI·Baa 스프레드·CAPE 는 Shiller·FRED(로컬 사본). 구간별 24주기 중 한두 주기가 평균을 좌우하므로 막대를 하나씩 보세요.</p>
</div>
<script>
const DATA = __DATA__;
const M = DATA.months, N = M.ym.length, Y0 = 1928;
const $ = s => document.querySelector(s), css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const mi = s => { const y = +s.slice(0,4), m = +s.slice(5,7), d = s.length>7 ? +s.slice(8,10) : 15; return (y-Y0)*12 + (m-1) + (d-1)/30; };
const fmtYM = i => { const k = Math.max(0, Math.min(N-1, Math.round(i))); return M.ym[k]; };
const fmt = (v, d=1, suf='') => v==null||isNaN(v) ? '-' : v.toFixed(d)+suf;
const PAN = [
 {k:'sp', t:'S&P500 (로그, 월말)', h:300, log:true, ser:[['sp','--sp','S&P500']]},
 {k:'kospi', t:'코스피 (로그, 월말)', h:150, log:true, ser:[['kospi','--kr','코스피 (1980=100)']]},
 {k:'rate', t:'금리 (%)', h:120, ser:[['gs10','--c10','10년 국채'],['tbl','--c3','3개월 단기']]},
 {k:'infl', t:'물가 전년비 (%)', h:100, ser:[['infl','--inf','CPI'] ], clip:[-12,20], zero:true},
 {k:'cr', t:'신용 스프레드 (%p)', h:90, ser:[['credit','--cr','Baa-10년']]},
 {k:'cape', t:'CAPE', h:100, ser:[['cape','--cape','CAPE']]}
];
const layers = {rec:true, bear:true, war:true, elec:true, crash:true, trend:false, cmp:false, kospi:true}; let view = [0, N-1];
const ev = {}; // month index -> events
const addEv = (i, s) => { const k = Math.round(i); (ev[k] = ev[k] || []).push(s); };
DATA.wars.forEach(w => addEv(mi(w.date), '전쟁·위기: '+w.name)); DATA.crashes.forEach(c => addEv(mi(c.date), '하루 '+(c.ret*100).toFixed(1)+'% ('+c.date+')'));
DATA.elections.forEach(e => addEv(mi(e.date), e.kind+'일 '+e.date));
function chips(){ const L = N-1, g = k => M[k][L]; const c = [['집권', M.pres[L]+' ('+M.party[L]+')', '연차 '+M.term_year[L]+'년차'], ['S&P500', Math.round(M.sp[L]).toLocaleString(), '고점 대비 '+fmt(M.dd[L]*100,1,'%')], ['10년 국채', fmt(g('gs10'),2,'%'), '12개월 전 '+fmt(M.gs10[L-12],2,'%')], ['물가 전년비', fmt(g('infl'),1,'%'), '역대 중앙 '+fmt(median(M.infl),1,'%')], ['신용 스프레드', fmt(g('credit'),2,'%p'), '역대 중앙 '+fmt(median(M.credit),2,'%p')], ['CAPE', fmt(g('cape'),1), '역대 중앙 '+fmt(median(M.cape),1)+' · 최고 '+fmt(Math.max(...M.cape),1)]];
 $('#chips').innerHTML = c.map(x => `<div class="chip"><span>${x[0]} · ${M.ym[L]}</span><b>${x[1]}</b><span>${x[2]}</span></div>`).join(''); }
function median(a){ const s = a.filter(v=>v!=null).slice().sort((x,y)=>x-y); return s[Math.floor(s.length/2)]; }
function controls(){ const mk = (t, f, cls='') => { const b = document.createElement('button'); b.textContent = t; b.className = cls; b.onclick = f; return b; }; const el = $('#ctl'); el.innerHTML = '';
 [['전체',0,N-1],['1928~1960',0,mi('1960-12')],['1960~1990',mi('1960-01'),mi('1990-12')],['1990~2026',mi('1990-01'),N-1],['최근 10년',N-120,N-1],['1980~2026(코스피 구간)',mi('1980-01'),N-1],['2013~2026',mi('2013-01'),N-1]].forEach(([t,a,b]) => el.append(mk(t, () => {view=[Math.round(a),Math.round(b)]; draw();})));
 const sc = mk('S&P 눈금: 로그', () => { PAN[0].log = !PAN[0].log; PAN[1].log = PAN[0].log; sc.textContent = 'S&P 눈금: ' + (PAN[0].log ? '로그' : '선형'); draw(); wire(); }); el.append(sc);
 const sep = document.createElement('span'); sep.className = 'sep'; el.append(sep);
 [['rec','경기침체'],['bear','약세장'],['war','전쟁'],['elec','선거'],['crash','폭락일'],['trend','장기 추세선(연 '+DATA.trend_cagr.toFixed(1)+'%)'],['kospi','코스피 패널'],['cmp','코스피에 S&P 겹침(1980=100)']].forEach(([k,t]) => { const l = document.createElement('label'); const c = document.createElement('input'); c.type='checkbox'; c.checked = layers[k]; c.onchange = () => {layers[k] = c.checked; draw();}; l.append(c, t); el.append(l); }); }
let geo = null;
function draw(){ const svg = $('#svg'), W = $('#chart').clientWidth, mL = 64, mR = 52, top = 50, gap = 14; const [a, b] = view; const sx = i => mL + (i - a) / (b - a) * (W - mL - mR);
 let y0 = top, H = top + PAN.filter(p=>p.k!=='kospi'||layers.kospi).reduce((s,p)=>s+p.h+gap,0) + 24; svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.style.height = H+'px'; let h = '';
 // 집권 띠
 h += `<g>`; DATA.terms.forEach((t, k) => { const s = Math.max(mi(t.start), 0), e = k+1 < DATA.terms.length ? mi(DATA.terms[k+1].start) : N; if (e < a || s > b) return; const x1 = sx(Math.max(s,a)), x2 = sx(Math.min(e,b)); h += `<rect x="${x1}" y="14" width="${Math.max(x2-x1,0)}" height="22" fill="var(${t.party==='D'?'--dem':'--rep'})"><title>${t.name} (${t.party==='D'?'민주':'공화'})</title></rect>`; if (x2-x1 > 62) h += `<text class="t-ink" x="${(x1+x2)/2}" y="29" text-anchor="middle">${t.name.replace('F.Roosevelt','FDR').replace('G.H.W.Bush','Bush').replace('G.W.Bush','Bush')}</text>`;
  }); h += `</g><text x="${mL-6}" y="29" text-anchor="end">집권</text>`;
 if (layers.elec) DATA.elections.forEach(e => { const i = mi(e.date); if (i<a||i>b) return; h += e.kind==='중간선거' ? `<path d="M${sx(i)} 33 l4 4 l-4 4 l-4 -4z" fill="var(--ink)"><title>중간선거 ${e.date}</title></path>` : `<path d="M${sx(i)} 34 l4 7 h-8z" fill="var(--ink)" opacity=".55"><title>대선 ${e.date}</title></path>`; });
 geo = {sx, a, b, W, mL, mR, panels: []};
 PAN.forEach(p => { if (p.k==='kospi' && !layers.kospi) return; const y1 = y0, y2 = y0 + p.h; geo.panels.push([y1, y2]); const sers = p.ser.concat(p.k==='sp' && layers.trend ? [['trend','--c3','장기 추세']] : [], p.k==='kospi' && layers.cmp ? [['sp_rb','--sp','S&P500 환산(1980=100)']] : []); p.t = p.k==='sp' ? 'S&P500 ('+(p.log?'로그':'선형')+', 월말)' : p.k==='kospi' ? '코스피 ('+(p.log?'로그':'선형')+', 월말, 1980=100)' : p.t; let lo = Infinity, hi = -Infinity; const i0 = Math.max(0, Math.floor(a)), i1 = Math.min(N-1, Math.ceil(b));
  sers.forEach(([k]) => { for (let i=i0;i<=i1;i++){ let v = M[k][i]; if (v==null) continue; if (p.clip) v = Math.max(p.clip[0], Math.min(p.clip[1], v)); if (p.log) v = Math.log(v); lo = Math.min(lo, v); hi = Math.max(hi, v);} });
  if (lo === Infinity) { h += `<rect x="${mL}" y="${y1}" width="${W-mR-mL}" height="${p.h}" fill="none" stroke="var(--grid)"/><text x="${mL+8}" y="${y1+20}">${p.k==='kospi'?'코스피 자료는 1980년부터 (이 구간 없음)':'자료 없음'}</text>`; geo.panels.push([y1, y2]); y0 = y2 + gap; return; } if (p.zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0);} if ((p.k==='sp' || p.k==='kospi') && !p.log) lo = 0; const pad = (hi-lo)*.06 || 1; if (!((p.k==='sp' || p.k==='kospi') && !p.log)) lo -= pad; hi += pad; const sy = v => y2 - ((p.log?Math.log(v):v) - lo) / (hi - lo) * p.h; p.sy = sy; p.lo = lo; p.hi = hi;
  h += `<rect x="${mL}" y="${y1}" width="${W-mL-mR}" height="${p.h}" fill="none" stroke="var(--grid)"/>`;
  // 눈금
  const ticks = p.log ? (p.k==='kospi' ? [30,100,300,1000,3000,10000] : [3,10,30,100,300,1000,3000,10000]).filter(v => Math.log(v)>lo && Math.log(v)<hi) : niceTicks(lo, hi, 4);
  ticks.forEach(v => { const yy = p.log ? sy(v) : y2 - (v-lo)/(hi-lo)*p.h; h += `<line x1="${mL}" x2="${W-mR}" y1="${yy}" y2="${yy}" stroke="var(--grid)"/><text x="${mL-6}" y="${yy+4}" text-anchor="end">${v.toLocaleString()}</text>`; });
  h += `<text class="t-ink" x="${mL+6}" y="${y1+14}" style="font-weight:600;paint-order:stroke;stroke:var(--bg);stroke-width:4px">${p.t}</text>`;
  if (layers.rec) { let s = null; for (let i=i0;i<=i1+1;i++){ const on = i<=i1 && M.rec[i]===1; if (on && s===null) s = i; if (!on && s!==null){ h += `<rect x="${sx(s-.5)}" y="${y1}" width="${sx(i-.5)-sx(s-.5)}" height="${p.h}" fill="var(--rec)" opacity=".7"/>`; s = null; } } }
  if (p.k==='sp' && layers.bear) DATA.bears.forEach(br => { const s = mi(br.start), e = mi(br.end); if (e<a||s>b) return; h += `<rect x="${sx(Math.max(s,a))}" y="${y1}" width="${Math.max(sx(Math.min(e,b))-sx(Math.max(s,a)),1)}" height="${p.h}" fill="var(--bear)" opacity=".5"><title>약세장 ${br.start}~${br.end} ${ (br.drop*100).toFixed(0)}%</title></rect>`; });
  if (p.zero) h += `<line x1="${mL}" x2="${W-mR}" y1="${sy(0)}" y2="${sy(0)}" stroke="var(--muted)" stroke-width=".6"/>`;
  if (p.k==='cape') { const md = median(M.cape); h += `<line x1="${mL}" x2="${W-mR}" y1="${sy(md)}" y2="${sy(md)}" stroke="var(--muted)" stroke-dasharray="4 3"/><text x="${W-mR+4}" y="${sy(md)+4}">중앙 ${md.toFixed(1)}</text>`; }
  sers.forEach(([k,c,l]) => { let d = '', pen = false; for (let i=i0;i<=i1;i++){ let v = M[k][i]; if (v==null){ pen=false; continue;} if (p.clip) v = Math.max(p.clip[0], Math.min(p.clip[1], v)); d += (pen?'L':'M')+sx(i).toFixed(1)+' '+sy(v).toFixed(1); pen = true; } h += `<path d="${d}" fill="none" stroke="var(${c})" stroke-width="${p.k==='sp'&&k!=='trend'?1.6:1.3}" ${k==='trend'?'stroke-dasharray="6 4"':''}/>`; });
  if (sers.length>1) sers.forEach(([k,c,l],j) => { h += `<rect x="${W-mR-sers.length*165+j*165}" y="${y1+6}" width="10" height="4" fill="var(${c})"/><text x="${W-mR-sers.length*165+14+j*165}" y="${y1+12}">${l}</text>`; });
  if (p.k==='sp') { if (layers.crash) DATA.crashes.forEach(c => { const i = mi(c.date); if (i<a||i>b) return; const k = Math.min(N-1, Math.round(i)); h += `<circle cx="${sx(i)}" cy="${sy(M.sp[k])}" r="3.4" fill="var(--crash)"><title>${c.date} 하루 ${(c.ret*100).toFixed(1)}%</title></circle>`; });
   if (layers.war) DATA.wars.forEach((w, j) => { const i = mi(w.date); if (i<a||i>b) return; const x = sx(i); h += `<line x1="${x}" x2="${x}" y1="${y2-34}" y2="${y2}" stroke="var(--war)" stroke-width="1.3"><title>${w.date} ${w.name}</title></line><text x="${x}" y="${y2-(38+(j%3)*12)}" text-anchor="middle" style="fill:var(--war);font-size:10px">${w.name.length>8?w.name.slice(0,8):w.name}</text>`; }); }
  y0 = y2 + gap; });
 // 축
 const yrs = []; const span = (b-a)/12, st = span>60?10:span>25?5:span>12?2:1; for (let y=Math.ceil((Y0+a/12)/st)*st; y<=Y0+b/12; y+=st) yrs.push(y);
 yrs.forEach(y => { const x = sx((y-Y0)*12); h += `<text x="${x}" y="${H-6}" text-anchor="middle">${y}</text><line x1="${x}" x2="${x}" y1="${top}" y2="${H-22}" stroke="var(--grid)" stroke-dasharray="2 4"/>`; });
 h += `<line id="vl" x1="0" x2="0" y1="${top}" y2="${H-22}" stroke="var(--ink)" stroke-width=".8" style="display:none"/><rect id="sel" y="${top}" height="${H-22-top}" fill="var(--bar)" opacity=".18" style="display:none"/>`;
 svg.innerHTML = h; geo.H = H; geo.top = top; }
function niceTicks(lo, hi, n){ const span = hi-lo, raw = span/n, mag = Math.pow(10, Math.floor(Math.log10(raw))), step = [1,2,2.5,5,10].map(m=>m*mag).find(s=>s>=raw); const out = []; for (let v=Math.ceil(lo/step)*step; v<=hi; v+=step) out.push(+v.toFixed(6)); return out; }
function legend(){ $('#legend').innerHTML = [['--dem','민주 집권'],['--rep','공화 집권'],['--rec','NBER 경기침체'],['--bear','약세장(-20%↑)'],['--crash','하루 -7% 이하'],['--war','전쟁·위기 발발(사전 지정)']].map(([c,t]) => `<span><i class="sw" style="background:var(${c})"></i>${t}</span>`).join('') + '<span>◆ 중간선거일 ▲ 대선일</span>'; }
// 상호작용
const svgEl = () => $('#svg'); let drag = null;
function xi(e){ const r = svgEl().getBoundingClientRect(), x = (e.clientX - r.left) * (geo.W / r.width); return geo.a + (x - geo.mL) / (geo.W - geo.mL - geo.mR) * (geo.b - geo.a); }
function tip(e, i){ const k = Math.max(0, Math.min(N-1, Math.round(i))), t = $('#tip'), r = $('#chart').getBoundingClientRect(); const ee = ev[k] || []; const rows = [['S&P500', Math.round(M.sp[k]).toLocaleString()],['코스피', M.kospi[k]==null?'-':Math.round(M.kospi[k]).toLocaleString()],['장기 추세 대비', ((M.sp[k]/M.trend[k]-1)*100>=0?'+':'')+((M.sp[k]/M.trend[k]-1)*100).toFixed(0)+'%'],['고점 대비', fmt(M.dd[k]*100,1,'%')],['10년 국채', fmt(M.gs10[k],2,'%')],['3개월 단기', fmt(M.tbl[k],2,'%')],['물가 전년비', fmt(M.infl[k],1,'%')],['신용 스프레드', fmt(M.credit[k],2,'%p')],['CAPE', fmt(M.cape[k],1)]];
 t.innerHTML = `<b>${M.ym[k]}</b> · ${M.pres[k]} (${M.party[k]==='D'?'민주':'공화'}, ${M.term_year[k]}년차)${M.rec[k]===1?' · <span style="color:var(--crash)">경기침체</span>':''}<table>${rows.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('')}</table>${ee.length?'<div style="margin-top:4px;color:var(--war)">'+ee.join('<br>')+'</div>':''}`;
 t.style.display = 'block'; const w = t.offsetWidth; let x = e.clientX - r.left + 14; if (x + w > r.width) x = e.clientX - r.left - w - 14; t.style.left = x+'px'; t.style.top = Math.max(0, e.clientY - r.top - 20)+'px'; }
function wire(){ const s = svgEl(); s.onmousemove = e => { const i = xi(e); if (drag){ const a = drag, b = i; const x1 = geo.sx(Math.min(a,b)), x2 = geo.sx(Math.max(a,b)); const sel = $('#sel'); sel.setAttribute('x', x1); sel.setAttribute('width', x2-x1); sel.style.display='block'; } if (i<geo.a||i>geo.b){ $('#tip').style.display='none'; return; } const vl = $('#vl'); const x = geo.sx(i); vl.setAttribute('x1', x); vl.setAttribute('x2', x); vl.style.display='block'; tip(e, i); };
 s.onmouseleave = () => { $('#tip').style.display='none'; const v = $('#vl'); if (v) v.style.display='none'; };
 s.onmousedown = e => { drag = xi(e); }; window.onmouseup = e => { if (drag==null) return; const i = xi(e), a = Math.min(drag,i), b = Math.max(drag,i); drag = null; if (b-a > 12){ view = [Math.max(0,Math.round(a)), Math.min(N-1,Math.round(b))]; draw(); wire(); } else { const sel = $('#sel'); if (sel) sel.style.display='none'; } };
 s.ondblclick = () => { view = [0, N-1]; draw(); wire(); }; }
// 2번째 차트
function bars(){ const svg = $('#svg2'), W = $('#chart2').clientWidth, H = 300, mL = 50, mR = 14, top = 14, bot = 40, C = DATA.cycles; svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.style.height = H+'px';
 const lo = Math.min(-30, ...C.map(c => c.strong*100)), hi = Math.max(40, ...C.map(c => c.strong*100)), sy = v => top + (hi - v)/(hi - lo)*(H-top-bot), bw = (W-mL-mR)/C.length; let h = '';
 niceTicks(lo, hi, 6).forEach(v => { h += `<line x1="${mL}" x2="${W-mR}" y1="${sy(v)}" y2="${sy(v)}" stroke="var(--grid)"/><text x="${mL-6}" y="${sy(v)+4}" text-anchor="end">${v}%</text>`; });
 h += `<line x1="${mL}" x2="${W-mR}" y1="${sy(0)}" y2="${sy(0)}" stroke="var(--muted)"/><line x1="${mL}" x2="${W-mR}" y1="${sy(DATA.base9)}" y2="${sy(DATA.base9)}" stroke="var(--muted)" stroke-dasharray="4 3"/>`;
 C.forEach((c, k) => { const v = c.strong*100, x = mL + k*bw + bw*.14, w = bw*.72; h += `<rect class="bar" data-k="${k}" x="${x}" y="${Math.min(sy(v), sy(0))}" width="${w}" height="${Math.abs(sy(v)-sy(0))}" rx="2" fill="var(${v>=0?'--bar':'--barneg'})" tabindex="0" aria-label="${c.y+1}년 10월 → ${c.y+2}년 6월 ${v.toFixed(1)}%"/><text x="${x+w/2}" y="${H-bot+14}" text-anchor="middle" style="font-size:10px">${String(c.y+1).slice(2)}</text>`; });
 h += `<text x="${mL}" y="${H-6}">중간선거 해(연도 뒤 2자리) — 10월 말부터 9개월 보유 수익</text>`; svg.innerHTML = h;
 const t = $('#tip'); svg.querySelectorAll('.bar').forEach(el => { const show = e => { const c = C[+el.dataset.k], r = $('#chart2').getBoundingClientRect(); t.innerHTML = `<b>${c.y+1}년 10월 → ${c.y+2}년 6월: ${(c.strong*100).toFixed(1)}%</b> (${c.party==='R'?'공화':'민주'} 집권)<table><tr><td>3년차 연수익</td><td>${(c.y3*100).toFixed(1)}%</td></tr><tr><td>10년 금리 12개월 변화</td><td>${c.d10>0?'+':''}${c.d10.toFixed(2)}%p</td></tr><tr><td>물가 전년비</td><td>${c.infl.toFixed(1)}%</td></tr><tr><td>CAPE</td><td>${c.cape.toFixed(1)}</td></tr><tr><td>신용 스프레드</td><td>${c.credit.toFixed(2)}%p</td></tr><tr><td>고점 대비(10월)</td><td>${(c.dd*100).toFixed(1)}%</td></tr><tr><td>침체 경험(그해 4~10월)</td><td>${c.rec?'예':'아니오'}</td></tr></table>`; t.style.display='block'; const rr = $('#chart').getBoundingClientRect(); t.style.position='fixed'; t.style.left = Math.min(e.clientX + 14, innerWidth - t.offsetWidth - 8)+'px'; t.style.top = Math.min(e.clientY - 10, innerHeight - t.offsetHeight - 8)+'px'; }; el.onmousemove = show; el.onfocus = e => show({clientX: el.getBoundingClientRect().x, clientY: el.getBoundingClientRect().y}); el.onmouseleave = el.onblur = () => { t.style.display = 'none'; t.style.position = 'absolute'; }; });
 $('#tbl').innerHTML = '<table class="t"><tr><th>중간선거 해</th><th>정당</th><th>10월→6월 수익</th><th>3년차</th><th>금리변화</th><th>물가</th><th>CAPE</th><th>스프레드</th></tr>' + C.map(c => `<tr><td>${c.y+1}</td><td>${c.party}</td><td>${(c.strong*100).toFixed(1)}%</td><td>${(c.y3*100).toFixed(1)}%</td><td>${c.d10.toFixed(2)}</td><td>${c.infl.toFixed(1)}</td><td>${c.cape.toFixed(1)}</td><td>${c.credit.toFixed(2)}</td></tr>`).join('') + '</table>'; }
function all(){ draw(); wire(); bars(); }
chips(); controls(); legend(); all(); addEventListener('resize', all);
</script></body></html>"""
open(f"{OUT}/chronicle.html", "w", encoding="utf-8").write(HTML.replace("__DATA__", json.dumps(J, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")))
print("saved", os.path.getsize(f"{OUT}/chronicle.html") // 1024, "KB")
