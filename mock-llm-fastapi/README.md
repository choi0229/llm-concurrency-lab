# mock-llm-fastapi

모든 Gateway 구현체가 공통으로 호출하는 통제 가능한 Mock LLM Streaming Server.
실제 LLM을 쓰지 않는 이유는 `docs/decisions/version-compatibility.md` 및 프로젝트 설계 원칙(섹션 6) 참고.

**⚠️ 로컬 venv 실행과 Docker 실행은 서로 다른 Python/FastAPI/uvicorn 버전으로 resolve된다** (로컬은 시스템
Python 3.9.6 → FastAPI 0.128.8/uvicorn 0.39.0, Docker는 `python:3.12-slim` → FastAPI 0.141.1/uvicorn 0.52.1,
`requirements.txt`에 pin된 값). 로컬 venv는 빠른 개발 반복(iteration)에만 쓰고, **모든 공식 Benchmark/부하 테스트는
반드시 Docker 이미지로 실행한 인스턴스만 사용한다.**

## 실행 방법

### 로컬 (venv)

```bash
cd mock-llm-fastapi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
cd mock-llm-fastapi
docker build -t mock-llm-fastapi .
docker run --rm -p 8000:8000 mock-llm-fastapi
```

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MOCK_LLM_LOG_LEVEL` | `INFO` | 로그 레벨 |
| `MOCK_LLM_MAX_CONCURRENT_PROCESSING` | `0` (무제한) | 동시 처리 가능한 요청 수 (Section 20 Bottleneck Simulation용) |
| `MOCK_LLM_MAX_WAITING` | `0` (무제한) | 처리 대기열 최대 크기, 초과 시 즉시 `error` 이벤트로 거부 |

## API

### `POST /mock/stream`

Request Body (모든 필드 optional, 기본값 있음):

```json
{
  "requestId": "optional-correlation-id",
  "firstChunkDelayMs": 200,
  "chunkIntervalMs": 100,
  "chunkCount": 10,
  "chunkSizeBytes": 64,
  "failureRate": 0.0,
  "midStreamFailureRate": 0.0,
  "failAtChunk": null,
  "forcedDisconnectAfterChunk": null,
  "maxDurationMs": 30000
}
```

- `failureRate`: 첫 청크 전 즉시 실패할 확률
- `midStreamFailureRate`: 스트림 중간에 실패할 확률 (실패 지점은 랜덤)
- `failAtChunk`: 특정 청크 이후 결정적으로 실패시킴 (재현 가능한 테스트용, `midStreamFailureRate`보다 우선)
- `forcedDisconnectAfterChunk`: `error` 이벤트 없이 특정 청크 이후 스트림을 그냥 끊음 (강제 연결 종료 시뮬레이션)

Response: `text/event-stream`

```
event: delta
data: {"sequence":1,"text":"..."}

event: delta
data: {"sequence":2,"text":"..."}

event: final
data: {"status":"COMPLETED"}
```

실패 시:

```
event: error
data: {"status":"FAILED","reason":"immediate_failure|mid_stream_failure|max_duration_exceeded|waiting_queue_full"}
```

응답 헤더 `X-Request-Id`에 correlation id가 echo 됨 (요청에 없으면 서버가 생성).

### `GET /healthz`

`{"status": "ok"}` — Docker Healthcheck 및 로컬 확인용.

### `GET /metrics`

Prometheus 포맷. 노출 지표: `mockllm_active_requests`, `mockllm_waiting_requests`,
`mockllm_current_concurrency`, `mockllm_completed_requests_total`,
`mockllm_failed_requests_total{reason}`, `mockllm_cancelled_requests_total`,
`mockllm_first_chunk_latency_seconds`, `mockllm_stream_duration_seconds`,
`mockllm_bytes_streamed_total`.

## 검증 방법

```bash
# 1. Health check
curl -s http://localhost:8000/healthz
# 기대 결과: {"status":"ok"}

# 2. 기본 스트리밍 (3개 청크, 빠르게)
curl -N -s -X POST http://localhost:8000/mock/stream \
  -H "Content-Type: application/json" \
  -d '{"firstChunkDelayMs":100,"chunkIntervalMs":100,"chunkCount":3,"chunkSizeBytes":16}'
# 기대 결과: event: delta 3회 이후 event: final, 총 약 0.5초 소요

# 3. 즉시 실패
curl -N -s -X POST http://localhost:8000/mock/stream \
  -H "Content-Type: application/json" \
  -d '{"failureRate":1.0}'
# 기대 결과: event: error, reason=immediate_failure

# 4. 결정적 mid-stream 실패
curl -N -s -X POST http://localhost:8000/mock/stream \
  -H "Content-Type: application/json" \
  -d '{"chunkCount":5,"failAtChunk":2}'
# 기대 결과: delta sequence=1,2 이후 error(mid_stream_failure), sequence=3~5는 전송되지 않음

# 5. 메트릭 확인
curl -s http://localhost:8000/metrics | grep mockllm_
```

## 실패 시 확인 항목

- `pip install` 실패: Python 버전이 3.9 미만이거나 네트워크 접근이 막혀 있는지 확인 (`python3 --version`)
- 스트리밍 응답이 즉시 끊기고 아무 이벤트도 안 옴: 리버스 프록시/curl이 SSE를 버퍼링하고 있을 수 있음 — `curl -N`(no-buffer) 옵션 확인
- `/metrics`가 비어 있음: 아직 `/mock/stream` 요청이 한 번도 없었다면 Gauge/Counter는 0으로 존재해야 하며, 완전히 비어있다면 `prometheus-client` 임포트 실패 여부를 서버 로그에서 확인
- Docker 빌드 시 arm64 관련 오류: `docker buildx ls`로 현재 builder가 `linux/arm64`를 지원하는지 확인 (Apple Silicon 호스트에서는 기본적으로 native 지원됨)
