"""data/search_daily.csv -> 키워드 z, 시장 공통 제거, 제품 총점수, 1.0배 판정, 14일 사건 묶음 -> data/latest.json, data/alert_log.csv
매일 계산하는 것: 정규화 -> 변화(최근 7일 vs 직전 28일) -> z(과거 365일) -> 공통 제거 -> 점수.
고정값(연결 강도, 기준)은 config.json 에 있다.
"""
import os, json, sys
import numpy as np, pandas as pd
 
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
R, B, H = CFG["window_recent_days"], CFG["window_base_days"], CFG["history_days"]
 
def load(asof=None):
    d = pd.read_csv(os.path.join(ROOT, "data", "search_daily.csv"), index_col=0, parse_dates=True)
    if asof: d = d[d.index <= pd.Timestamp(asof)]
    d = d.interpolate(limit=3)
    return d
 
def zscores(d):
    X = (d / d.mean()).clip(lower=0.05)                       # 1) 키워드 자기 평균=1
    mom = np.log(X.rolling(R).mean() / X.shift(R).rolling(B).mean())   # 2) 최근 7일 vs 직전 28일
    mu = mom.rolling(H, min_periods=180).mean().shift(1)
    sd = mom.rolling(H, min_periods=180).std().shift(1)
    return (mom - mu) / sd                                    # 3) 과거 365일 기준 z
 
def build(d):
    Z = zscores(d)
    pool = [k for k in CFG["pool_keywords"] if k in Z.columns]
    mkt = Z[pool].mean(axis=1)
    Zc = Z[pool].sub(mkt, axis=0)                             # 4) 시장 공통 상승 제거
    out = {}
    for p, c in CFG["products"].items():
        if c["mode"] == "score":
            S = sum(Zc[k] * w for k, w in c["weights"].items())
            ratio = (S - S.rolling(H, min_periods=180).mean().shift(1)) / S.rolling(H, min_periods=180).std().shift(1)
        else:                                                  # 제품 자체 검색 z
            S = Z[p]; ratio = Z[p]
        out[p] = pd.DataFrame({"score": S, "ratio": ratio})
    return Z, Zc, mkt, out
 
def events(ratio, thr, merge_days):
    """thr 이상인 날 중 직전 사건 시작일로부터 merge_days 넘게 지난 날 = 새 사건 시작. 이후 merge_days 동안은 '진행'."""
    ev, last = [], None
    for t in ratio.index[ratio >= thr]:
        if last is None or (t - last).days > merge_days:
            ev.append(t); last = t
    return ev
 
def status_on(ratio, ev, day, thr, merge_days):
    r = ratio.get(day, np.nan)
    if np.isnan(r): return "데이터 없음"
    if day in set(ev): return "새 사건"
    in_event = any(0 < (day - e).days <= merge_days for e in ev)
    if in_event and r >= thr: return "진행(유지·확대)"
    if in_event: return "진행(둔화)"
    return "알림 없음"
 
def phase_info(ratio, ev, day, thr, merge_days):
    """사건 진행 중이면 {시작일, 며칠째, 정점, 어제 값, 국면}. 국면: 시작 / 확대(정점 갱신) / 지속(정점의 80% 이상) / 완화(정점의 80% 미만) / 종료(오늘 처음 기준 아래) / 둔화."""
    starts = [e for e in ev if e <= day]
    if not starts or (day - starts[-1]).days > merge_days: return {}
    s = starts[-1]; seg = ratio[(ratio.index >= s) & (ratio.index <= day)].dropna()
    if seg.empty: return {}
    r, peak = float(seg.iloc[-1]), float(seg.max()); prev = float(seg.iloc[-2]) if len(seg) > 1 else None
    if day == s: ph = "시작"
    elif r >= thr: ph = "확대" if r >= peak - 1e-9 else ("지속" if r >= 0.8 * peak else "완화")
    else: ph = "종료" if (prev is not None and prev >= thr) else "둔화"
    return {"start": str(s.date()), "day_n": (day - s).days + 1, "peak": round(peak, 2), "peak_day": str(seg.idxmax().date()),
            "prev": round(prev, 2) if prev is not None else None, "phase": ph}
 
def competitor_info(p, cc, Z, pct, prod, thr):
    """경쟁 제품 검색 변화: 우리 알림과 같은 계산(최근 7일 vs 직전 28일 → 365일 기준 z). 이력이 짧아 z 를 못 구하면 z=None."""
    items = []
    for n in cc["names"]:
        if n not in Z.columns: continue
        z = Z[n].iloc[-1]; q = pct[n].iloc[-1]
        items.append({"name": n, "z": None if np.isnan(z) else round(float(z), 2), "pct": None if np.isnan(q) else round(float(q) * 100)})
    zs = [i["z"] for i in items if i["z"] is not None]
    if not zs: return {"label": cc["label"], "items": items, "median_z": None, "verdict": ""}
    m = float(np.median(zs)); u = prod["own_z"] if prod["mode"] == "score" else prod["ratio"]
    hot = [i["name"] for i in items if i["z"] is not None and i["z"] >= thr]
    if u is not None and u >= thr and m < 0.5: v = "우리만 상승 (경쟁은 평소 수준)"
    elif u is not None and u >= thr and m < thr: v = "우리 상승, 경쟁도 다소 상승"
    elif u is not None and u >= thr: v = "분야 전체 상승 (경쟁도 함께 올랐음)"
    elif m >= thr: v = "경쟁만 상승"
    else: v = "경쟁·우리 모두 큰 변화 없음"
    return {"label": cc["label"], "items": items, "median_z": round(m, 2), "our_z": None if u is None else round(float(u), 2), "hot": hot, "verdict": v}
 
def run(asof=None):
    d = load(asof); thr = CFG["threshold"]; M = CFG["event_merge_days"]
    Z, Zc, mkt, out = build(d)
    day = d.index[-1]
    res = {"asof": str(day.date()), "threshold": thr, "market_common_z": round(float(mkt.iloc[-1]), 2), "products": {}}
    log = []
    for p, c in CFG["products"].items():
        df = out[p]; ev = events(df["ratio"].dropna(), thr, M)
        st = status_on(df["ratio"], ev, day, thr, M)
        contrib = {}
        if c["mode"] == "score":
            contrib = {k: {"z_adj": round(float(Zc[k].iloc[-1]), 2), "weight": w, "part": round(float(Zc[k].iloc[-1]) * w, 3)} for k, w in c["weights"].items()}
        res["products"][p] = {"mode": c["mode"], "score": round(float(df["score"].iloc[-1]), 3), "ratio": round(float(df["ratio"].iloc[-1]), 2),
                              "status": st, "contrib": contrib, "own_z": round(float(Z[p].iloc[-1]), 2) if p in Z else None,
                              "last_event": str(ev[-1].date()) if ev else None, "event": phase_info(df["ratio"], ev, day, thr, M)}
        for e in ev: log.append({"product": p, "date": e.date(), "ratio": round(float(df['ratio'][e]), 2)})
    res["reference"] = {k: round(float(Z[k].iloc[-1]), 2) for k in CFG["reference"] if k in Z}
    X = (d / d.mean()).clip(lower=0.05)
    pct = X.rolling(R).mean() / X.shift(R).rolling(B).mean() - 1          # 최근 7일 평균 vs 직전 28일 평균(%)
    for p, cc in CFG.get("competitors", {}).items():
        if p in res["products"]: res["products"][p]["competitors"] = competitor_info(p, cc, Z, pct, res["products"][p], thr)
    return res, pd.DataFrame(log), Z, Zc, out
 
if __name__ == "__main__":
    asof = sys.argv[1] if len(sys.argv) > 1 else None
    res, log, *_ = run(asof)
    json.dump(res, open(os.path.join(ROOT, "data", "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log.to_csv(os.path.join(ROOT, "data", "alert_log.csv"), index=False)
    print(json.dumps(res, ensure_ascii=False, indent=1))
