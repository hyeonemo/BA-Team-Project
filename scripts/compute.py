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
                              "last_event": str(ev[-1].date()) if ev else None}
        for e in ev: log.append({"product": p, "date": e.date(), "ratio": round(float(df['ratio'][e]), 2)})
    res["reference"] = {k: round(float(Z[k].iloc[-1]), 2) for k in CFG["reference"] if k in Z}
    return res, pd.DataFrame(log), Z, Zc, out

if __name__ == "__main__":
    asof = sys.argv[1] if len(sys.argv) > 1 else None
    res, log, *_ = run(asof)
    json.dump(res, open(os.path.join(ROOT, "data", "latest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log.to_csv(os.path.join(ROOT, "data", "alert_log.csv"), index=False)
    print(json.dumps(res, ensure_ascii=False, indent=1))
