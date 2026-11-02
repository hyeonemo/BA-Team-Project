"""data/*.csv, latest.json -> docs/index.html (파일 하나로 열리는 독립 HTML, 외부 라이브러리 없음). GitHub Pages 가 docs/ 를 공개한다."""
import os, json, html
import numpy as np, pandas as pd
import compute as C

ROOT = C.ROOT; CFG = C.CFG
def svg_lines(series, labels, colors, dates, hline=None, hlabel=None, w=620, h=150, shade_last=7, unit=""):
    n = len(dates); L, Rr, T, Bt = 46, 12, 12, 26
    allv = [v for s in series for v in s if not np.isnan(v)]
    if hline is not None: allv.append(hline)
    lo, hi = min(allv), max(allv); pad = (hi - lo) * 0.1 or 1; lo -= pad; hi += pad
    X = lambda i: L + (w - L - Rr) * i / (n - 1); Y = lambda v: T + (h - T - Bt) * (1 - (v - lo) / (hi - lo))
    out = [f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='{html.escape(' / '.join(labels))} 최근 {n}일 추이'>"]
    out.append(f"<rect x='{X(n-shade_last)}' y='{T}' width='{X(n-1)-X(n-shade_last)}' height='{h-T-Bt}' fill='var(--shade)'/>")
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        out.append(f"<line x1='{L}' x2='{w-Rr}' y1='{Y(v):.1f}' y2='{Y(v):.1f}' stroke='var(--grid)'/><text x='{L-6}' y='{Y(v)+4:.1f}' text-anchor='end' class='tick'>{(round(v) if abs(v)>=0.5 or v>0 else 0):.0f}{unit}</text>")
    if hline is not None:
        out.append(f"<line x1='{L}' x2='{w-Rr}' y1='{Y(hline):.1f}' y2='{Y(hline):.1f}' stroke='var(--accent)' stroke-dasharray='4 4'/><text x='{w-Rr}' y='{Y(hline)-4:.1f}' text-anchor='end' class='tick' fill='var(--accent)'>{hlabel}</text>")
    for s, c in zip(series, colors):
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(s) if not np.isnan(v))
        out.append(f"<polyline points='{pts}' fill='none' stroke='{c}' stroke-width='1.8'/>")
    for i in (0, n // 2, n - 1):
        out.append(f"<text x='{X(i):.1f}' y='{h-8}' text-anchor='middle' class='tick'>{dates[i].strftime('%m/%d')}</text>")
    out.append("</svg>")
    leg = "".join(f"<span><i style='background:{c}'></i>{html.escape(l)}</span>" for l, c in zip(labels, colors))
    return "".join(out) + f"<div class='legend'>{leg}</div>"

def build():
    res, log, Z, Zc, out = C.run()
    d = C.load(); X = (d / d.mean()).clip(lower=0.05)
    N = 35; idx = X.index[-N:]
    PAL = ["#1f77b4", "#d6604d", "#4d9a4d", "#8e6bbf"]
    cards = []
    for p, r in res["products"].items():
        cfg = CFG["products"][p]
        base = X[p].rolling(28).mean().shift(7)                       # 7일 전까지의 28일 평균 = 기준선(100)
        own = (X[p] / base * 100).loc[idx]
        series, labels, cols = [own.values], [p + " (제품 자체)"], [PAL[0]]
        if cfg["mode"] == "score":
            for j, k in enumerate(cfg["weights"]):
                b = X[k].rolling(28).mean().shift(7); series.append((X[k] / b * 100).loc[idx].values); labels.append(k); cols.append(PAL[j + 1])
        chart = svg_lines([pd.Series(s).rolling(7, min_periods=1).mean().values for s in series], labels, cols, list(idx), hline=100, hlabel="직전 4주 평균=100", unit="")
        if cfg["mode"] == "score":
            ratio = out[p]["ratio"].loc[idx]
            chart2 = svg_lines([ratio.values], ["총점수 (평소 폭의 몇 배)"], [PAL[3]], list(idx), hline=res["threshold"], hlabel=f"기준 {res['threshold']:.1f}배", unit="")
            rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{v['z_adj']:+.2f}</td><td>{v['weight']:.2f}</td><td>{v['part']:+.3f}</td></tr>" for k, v in r["contrib"].items())
            tbl = f"<table><tr><th>연결 키워드</th><th>오른 정도(z)</th><th>연결 강도</th><th>기여</th></tr>{rows}<tr class='sum'><td colspan='3'>총점수</td><td>{r['score']:+.3f}</td></tr></table>"
            note = f"총점수 {r['score']:+.2f}는 평소 폭의 <b>{r['ratio']:.1f}배</b>입니다."
        else:
            chart2 = ""; tbl = ""
            note = f"제품 자체 검색이 평소 폭의 <b>{r['ratio']:.1f}배</b>입니다. 연결 키워드가 없어 제품 자체 검색만 감지합니다."
        cls = "on" if r["status"] in ("새 사건",) or r["status"].startswith("진행(유지") else ""
        cards.append(f"<section class='card {cls}'><h3>{html.escape(p)} <small>{html.escape(r['status'])}</small></h3><p>{note}</p>{chart}{chart2}{tbl}</section>")
    recent = log[log.date >= (pd.Timestamp(res['asof']) - pd.Timedelta(days=28)).date()]
    rows = "".join(f"<tr><td>{e.date}</td><td>{html.escape(e.product)}</td><td>{e.ratio:.1f}배</td></tr>" for e in recent.sort_values("date", ascending=False).itertuples()) or "<tr><td colspan='3'>최근 4주 새 알림 없음</td></tr>"
    brief = f"<table><tr><th>날짜</th><th>제품</th><th>평소 폭 대비</th></tr>{rows}</table>"
    method = """<ol><li>데이터랩 일 단위 검색량을 키워드 자기 평균으로 나눠 같은 잣대로 맞춥니다.</li>
<li>최근 7일 평균을 직전 28일 평균과 비교하고, 그 키워드의 과거 365일 변동폭으로 나눠 z(평소보다 얼마나 올랐나)를 구합니다.</li>
<li>같은 날 풀 전체 평균 z를 빼서 시장 전체의 공통 상승(올영세일·시즌)을 없앱니다.</li>
<li>제품 총점수 = 연결 키워드 z × 연결 강도의 합. 연결 강도는 과거 같이 움직인 정도(상관)이고 우연 수준(약 0.19) 이하는 쓰지 않습니다.</li>
<li>총점수가 자기 평소 폭의 1.0배 이상이면 알림. 같은 사건은 14일 안에서 한 번으로 묶고 진행 상황으로 알립니다.</li>
<li>연결 키워드가 없는 제품(테라크네365, 리제덤365)은 제품 자체 검색이 1.0배 이상일 때만 알립니다.</li></ol>
<p class='mute'>한계: 연결 강도는 전체 과거로 구해 과거 결과가 실제보다 좋게 보일 수 있고, 앞으로 4주 동안 실제 알림으로 확인하는 시험 중입니다.</p>"""
    css = """:root{--bg:#fff;--fg:#1c1f23;--mute:#667;--grid:#e3e6ea;--shade:#eef3fb;--card:#fafbfc;--line:#d8dce1;--accent:#c0392b}
@media(prefers-color-scheme:dark){:root{--bg:#14171a;--fg:#e8eaed;--mute:#9aa3ad;--grid:#2a2f35;--shade:#1d2630;--card:#1a1e22;--line:#2d333a;--accent:#ff7b6b}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,'Apple SD Gothic Neo','Noto Sans KR',sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 56px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}h3{font-size:16px;margin:0 0 4px}
small{font-weight:400;color:var(--mute);font-size:12.5px;margin-left:6px}.mute{color:var(--mute)}
.card{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:14px 14px 10px;margin:12px 0}.card.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
svg{width:100%;height:auto;display:block}.tick{font-size:10.5px;fill:var(--mute)}.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--mute);margin:2px 0 8px}.legend i{display:inline-block;width:10px;height:3px;margin-right:5px;vertical-align:middle}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:8px 0}th,td{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left}tr.sum td{font-weight:600}"""
    page = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>에스트라 검색 신호 브리핑</title><style>{css}</style></head><body><main>
<h1>에스트라 크림 검색 신호 브리핑</h1><p class='mute'>기준일 {res['asof']} · 시장 공통 변화 {res['market_common_z']:+.1f} · 참고: {' · '.join(f'{k} {v:+.1f}배' for k, v in res['reference'].items())}</p>
<h2>오늘의 제품별 상태</h2>{''.join(cards)}
<h2>최근 4주 알림 (주간 브리핑)</h2>{brief}
<h2>계산 방법</h2>{method}</main></body></html>"""
    open(os.path.join(ROOT, "docs", "index.html"), "w", encoding="utf-8").write(page)
    print("docs/index.html", len(page) // 1024, "KB")
if __name__ == "__main__":
    build()
