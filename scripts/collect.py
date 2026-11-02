"""네이버 데이터랩 검색어 트렌드(일 단위)를 풀 키워드 전체에 대해 받아 data/search_daily.csv 로 저장한다.
키는 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 에서만 읽는다(저장소에 올리지 않는다).
키워드마다 따로 요청한다: 데이터랩 비율은 요청마다 그 안의 최대값=100 으로 맞춰지기 때문에
매 실행마다 전체 구간을 새로 받아 덮어쓴다(과거 파일과 섞지 않는다).
"""
import os, sys, json, time, datetime as dt, urllib.request, urllib.error
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))

def call(path, body):
    cid, sec = os.environ["NAVER_CLIENT_ID"], os.environ["NAVER_CLIENT_SECRET"]
    req = urllib.request.Request(CFG["api_base"] + path, data=json.dumps(body).encode(), method="POST",
        headers={"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": sec, "Content-Type": "application/json"})
    last = None
    for i in range(4):
        try:
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read().decode()[:200]}"
            if e.code < 500 and e.code != 429: break
        except Exception as e:
            last = repr(e)
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"데이터랩 호출 실패: {last}")

def main(end=None):
    end = end or (dt.date.today() - dt.timedelta(days=1)).isoformat()   # 어제까지
    cols = {}
    for name, kws in CFG["keywords"].items():
        r = call("/search-trend/v1/search", {"startDate": CFG["start_date"], "endDate": end, "timeUnit": "date",
                 "keywordGroups": [{"groupName": name, "keywords": kws}]})
        cols[name] = pd.Series({p["period"]: p["ratio"] for p in r["results"][0]["data"]})
    df = pd.DataFrame(cols); df.index = pd.to_datetime(df.index); df.index.name = "date"; df = df.sort_index()
    df.to_csv(os.path.join(ROOT, "data", "search_daily.csv"))
    os.makedirs(os.path.join(ROOT, "data", "snapshots"), exist_ok=True)
    df.tail(60).to_csv(os.path.join(ROOT, "data", "snapshots", f"{end}.csv"))
    print(f"저장 완료: {df.shape[0]}일 x {df.shape[1]}개 키워드, 마지막 날짜 {df.index.max().date()}")
    return df

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
