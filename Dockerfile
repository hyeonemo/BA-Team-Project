# 시범 배포용 (컨테이너를 돌릴 수 있는 클라우드 어디서나)
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
# 환경변수: ACCESS_CODE(필수), NAVER_CLIENT_ID/SECRET(실수집일 때), SERVER_ARGS(예: --demo), PORT(호스트가 정해 줌)
# 기업 등록 데이터는 /app/tenants 에 쌓이므로 이 폴더에 영구 저장소(볼륨)를 연결해야 재시작해도 남는다.
EXPOSE 8000
CMD ["sh", "-c", "python scripts/server.py ${SERVER_ARGS}"]
