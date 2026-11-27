"""data/search_daily.csv -> 키워드 z, 시장 공통 제거, 제품 총점수, 1.0배 판정, 사건 상태 추적(연속 3일 기준 아래면 종료) -> data/latest.json, data/alert_log.csv
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

def _std(S):
    return (S - S.rolling(H, min_periods=180).mean().shift(1)) / S.rolling(H, min_periods=180).std().shift(1)

def build(d):
    Z = zscores(d)
    pool = [k for k in CFG["pool_keywords"] if k in Z.columns]
    mkt = Z[pool].mean(axis=1)
    Zc = Z[pool].sub(mkt, axis=0)                             # 4) 시장 공통 상승 제거
    nan = pd.Series(np.nan, index=Z.index)
    out = {}
    for p, c in CFG["products"].items():
        own = Z[p]                                             # 제품 자체 검색 z
        w = c.get("weights") or {}
        if c["mode"] == "score" and w:
            S = sum(Zc[k] * wt for k, wt in w.items())         # 연결 점수 = 연결 키워드 z x 연결 강도의 합
            sr = _std(S)
            ratio = pd.concat([sr, own], axis=1).max(axis=1)   # 알림 판정값 = 연결 점수와 제품 자체 검색 중 큰 쪽
        else:
            S = own; sr = nan; ratio = own
        lay = c.get("layers") or {}
        def lr(keys):
            keys = [k for k in (keys or []) if k in Zc.columns]
            return _std(sum(Zc[k] for k in keys) / len(keys)) if keys else nan
        out[p] = pd.DataFrame({"score": S, "ratio": ratio, "score_ratio": sr, "own_ratio": own,
                               "ing_ratio": lr(lay.get("ing")), "cat_ratio": lr(lay.get("cat"))})
    return Z, Zc, mkt, out

def trigger(sr, orr, thr):
    """오늘 어느 신호가 기준을 넘었나: both(연결 점수와 제품 자체 모두) / own(제품 자체만) / score(연결 점수만) / None."""
    s = sr is not None and not np.isnan(sr) and sr >= thr; o = orr is not None and not np.isnan(orr) and orr >= thr
    return "both" if s and o else "own" if o else "score" if s else None

def pattern(ing, cat, own, sr, thr):
    """3층(성분·효과 I / 카테고리 C / 제품 P) 중 기준을 넘은 층의 조합. 층 신호는 없는데 연결 점수 합산만 넘으면 S."""
    up = lambda v: v is not None and not np.isnan(v) and v >= thr
    parts = [n for n, v in (("I", ing), ("C", cat), ("P", own)) if up(v)]
    return "+".join(parts) if parts else ("S" if up(sr) else "")

def track(ratio, thr, end_days):
    """날마다 사건 상태를 이어서 추적한다 (고정 14일 창 대신 상태 기계).
    - 기준 이상인 첫날(열린 사건 없음) = 시작. 열린 사건 중 기준 이상 = 확대/지속/완화.
    - 기준 아래로 end_days일 연속 내려가면 그날 '종료'로 사건을 닫는다. 그 전까지는 '둔화'(사건은 열려 있음 -> 잠깐 흔들려도 같은 사건).
    - 닫힌 뒤 다시 기준 이상이 되면 새 사건의 '시작'."""
    rows, active, s, peak, below, prev = {}, False, None, None, 0, None
    for t, r in ratio.dropna().items():
        if r >= thr:
            if not active: active, s, peak, below = True, t, r, 0; ph = "시작"
            else:
                peak = max(peak, r); below = 0
                ph = "확대" if r >= peak - 1e-9 else ("지속" if r >= 0.8 * peak else "완화")
        elif active:
            below += 1
            if below >= end_days: ph = "종료"; active = False
            else: ph = "둔화"
        else: ph = None
        if ph: rows[t] = {"start": s, "peak": peak, "prev": prev, "phase": ph, "r": r, "below": below}
        prev = r
    return rows

def events(ratio, thr, end_days):
    """사건 시작일 목록 (track 의 '시작' 날)."""
    return [t for t, v in track(ratio, thr, end_days).items() if v["phase"] == "시작"]

def status_on(ratio, tr, day):
    r = ratio.get(day, np.nan)
    if np.isnan(r): return "데이터 없음"
    v = tr.get(day)
    if v is None: return "알림 없음"
    return {"시작": "새 사건", "확대": "진행(유지·확대)", "지속": "진행(유지·확대)", "완화": "진행(유지·확대)", "둔화": "진행(둔화)", "종료": "진행(둔화)"}[v["phase"]]

def phase_info(ratio, tr, day):
    """사건 중이면 {시작일, 며칠째, 정점, 어제 값, 국면}. 국면: 시작 / 확대(정점 갱신) / 지속(정점의 80% 이상) / 완화(정점의 80% 미만) / 둔화(기준 아래, 아직 열린 사건) / 종료(기준 아래 end_days일째, 사건 닫힘)."""
    v = tr.get(day)
    if v is None: return {}
    seg = ratio[(ratio.index >= v["start"]) & (ratio.index <= day)].dropna()
    return {"start": str(v["start"].date()), "day_n": (day - v["start"]).days + 1, "peak": round(float(v["peak"]), 2),
            "peak_day": str(seg.idxmax().date()), "below": v["below"], "prev": None if v["prev"] is None else round(float(v["prev"]), 2), "phase": v["phase"]}

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
    d = load(asof); thr = CFG["threshold"]; M = CFG["event_end_days"]
    Z, Zc, mkt, out = build(d)
    day = d.index[-1]
    res = {"asof": str(day.date()), "threshold": thr, "market_common_z": round(float(mkt.iloc[-1]), 2), "products": {}}
    log = []
    for p, c in CFG["products"].items():
        df = out[p]; tr = track(df["ratio"], thr, M); ev = [t for t, v in tr.items() if v["phase"] == "시작"]
        st = status_on(df["ratio"], tr, day)
        contrib = {}
        if c["mode"] == "score":
            contrib = {k: {"z_adj": round(float(Zc[k].iloc[-1]), 2), "weight": w, "part": round(float(Zc[k].iloc[-1]) * w, 3)} for k, w in c["weights"].items()}
        res["products"][p] = {"mode": c["mode"], "score": round(float(df["score"].iloc[-1]), 3), "ratio": round(float(df["ratio"].iloc[-1]), 2),
                              "status": st, "contrib": contrib, "own_z": round(float(Z[p].iloc[-1]), 2) if p in Z else None,
                              "score_ratio": None if np.isnan(df["score_ratio"].iloc[-1]) else round(float(df["score_ratio"].iloc[-1]), 2),
                              "own_ratio": None if np.isnan(df["own_ratio"].iloc[-1]) else round(float(df["own_ratio"].iloc[-1]), 2),
                              "trigger": trigger(df["score_ratio"].iloc[-1], df["own_ratio"].iloc[-1], thr),
                              "ing_ratio": None if np.isnan(df["ing_ratio"].iloc[-1]) else round(float(df["ing_ratio"].iloc[-1]), 2),
                              "cat_ratio": None if np.isnan(df["cat_ratio"].iloc[-1]) else round(float(df["cat_ratio"].iloc[-1]), 2),
                              "layers": c.get("layers") or {},
                              "pattern": pattern(df["ing_ratio"].iloc[-1], df["cat_ratio"].iloc[-1], df["own_ratio"].iloc[-1], df["score_ratio"].iloc[-1], thr),
                              "last_event": str(ev[-1].date()) if ev else None, "event": phase_info(df["ratio"], tr, day)}
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
