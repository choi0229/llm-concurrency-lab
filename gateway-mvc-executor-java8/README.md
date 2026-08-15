# gateway-mvc-executor-java8

Baseline Gateway: Java 8 + Spring Boot 1.5.22.RELEASE(Spring Framework 4.3.25.RELEASE) + Servlet AsyncContext +
전용 ThreadPoolExecutor + HttpURLConnection. 실제 업무에서 경험한 구조의 재현 기준선.
버전 선정 근거는 `docs/decisions/version-compatibility.md` 참고.

**현재 상태: 핵심 릴레이 로직(AsyncContext + 전용 ThreadPoolExecutor + HttpURLConnection) 구현 및 Docker 상에서
end-to-end 검증 완료.** 아직 없는 것: k6 부하 테스트 스크립트, Docker Compose, Client Disconnect/Timeout의
정밀 Scenario 테스트(Phase 1 후반부 예정).

> ⚠️ 여기서 쓰는 Java 8 / Spring Framework 4.3.x / Spring Boot 1.5.x는 모두 보안 지원이 종료(EOL)된 버전이다.
> 이 프로젝트는 특정 실행 모델을 재현해 측정하기 위한 Benchmark Lab이며, 이 버전 조합을 프로덕션에 권장하는
> 것이 아니다. Spring Boot를 쓰는 이유도 "제품 스택 복제"가 아니라 동일 실행 모델을 가장 낮은 구현 난이도로
> 독립 실행 가능하게 만들기 위함이다. 자세한 내용은 `docs/decisions/version-compatibility.md` 참고.

## 실행 방법

로컬에 JDK 8이 없어도(Apple Silicon 등) Docker만으로 빌드~실행이 가능하다. 빌드 스테이지도 런타임 스테이지도
동일하게 JDK 8(Eclipse Temurin)을 쓰므로 로컬 JDK 버전과 무관하다.

```bash
cd gateway-mvc-executor-java8
docker build -t gateway-mvc-executor-java8 .
docker run --rm -p 8080:8080 gateway-mvc-executor-java8
```

로컬에 JDK 8이 있다면 컨테이너 없이도 가능하다:

```bash
./gradlew bootRun
```

Mock LLM과 함께 띄우려면 같은 Docker 네트워크에 연결하고 `MOCK_LLM_BASE_URL`을 지정한다:

```bash
docker network create llm-lab
docker run -d --network llm-lab --name mockllm mock-llm-fastapi
docker run -d --network llm-lab -p 8080:8080 -e MOCK_LLM_BASE_URL=http://mockllm:8000 gateway-mvc-executor-java8
```

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MOCK_LLM_BASE_URL` | `http://localhost:8000` | Mock LLM 주소 |
| `CHAT_CORE_POOL_SIZE` | `10` | 챗봇 전용 Executor core pool size |
| `CHAT_MAX_POOL_SIZE` | `10` | 챗봇 전용 Executor max pool size |
| `CHAT_QUEUE_CAPACITY` | `100` | Executor Queue 용량. `0`이면 `SynchronousQueue`(capacity 0, hand-off) 사용 |
| `CHAT_REJECTION_POLICY` | `ABORT` | `ABORT`(즉시 503) 또는 `CALLER_RUNS`(Tomcat 스레드가 직접 실행) |
| `CHAT_CONNECT_TIMEOUT_MS` | `3000` | Mock LLM 연결 타임아웃 |
| `CHAT_READ_TIMEOUT_MS` | `30000` | Mock LLM 응답 read 타임아웃 (`BufferedReader.readLine()`의 실제 최대 block 시간) |
| `CHAT_TOTAL_TIMEOUT_MS` | `60000` | 전체 릴레이 데드라인. `AsyncContext.setTimeout()`과 워커 스레드의 자체 데드라인 체크 양쪽에 적용 |

## API

### `POST /chat/stream`

Body는 Mock LLM의 `/mock/stream` 요청 바디를 그대로 통과시킨다(passthrough) — k6에서 `firstChunkDelayMs`,
`chunkCount` 등을 그대로 실어 보내면 된다. 응답은 Mock LLM의 SSE 스트림을 그대로 relay한다.

Executor가 포화되어 즉시 거부되면(`ABORT` 정책) `503`과 `{"status":"REJECTED","reason":"executor_saturated"}`를 반환한다.

### `GET /metrics`

Prometheus 포맷. 주요 지표:

- `gateway_async_active_requests` — 현재 열린 AsyncContext 수(대기+처리 합산)
- `gateway_executor_pool_size` / `_active_threads` / `_largest_pool_size` / `_queue_size`(source of truth) / `_completed_task_total` — 매 scrape마다 `ThreadPoolExecutor`의 실제 getter를 읽음
- `gateway_executor_rejected_total`, `gateway_async_timeout_total`, `gateway_client_disconnect_total`, `gateway_upstream_failure_total`
- `executor_queue_wait_seconds` — **mode=pool 작업만** 관측(진짜 Queue 대기). `CALLER_RUNS`로 실행된 작업은 Queue를 거치지 않으므로 여기 포함되지 않음
- `executor_task_start_delay_seconds{mode="pool"|"caller"}` — submit()→run() 시작까지 시간. `caller`는 항상 거의 0(동기 실행이므로) — "지연이 0"이 "비용이 0"을 의미하지 않음을 보여주는 지표
- `executor_task_execution_total{mode}` — 실행 모드별 카운트
- `gateway_ttfb_seconds` — 요청 수신부터 첫 청크 relay까지

## 검증 방법

```bash
curl -s http://localhost:8080/healthz
# 기대 결과: {"status":"ok"}

curl -N -s -X POST http://localhost:8080/chat/stream -H "Content-Type: application/json" \
  -d '{"firstChunkDelayMs":100,"chunkIntervalMs":100,"chunkCount":3,"chunkSizeBytes":16}'
# 기대 결과: event: delta 3회 이후 event: final (Mock LLM 응답이 그대로 relay됨)

curl -s http://localhost:8080/metrics | grep -E "gateway_|executor_"
```

**실제로 검증된 시나리오 (2026-08-10, Docker arm64, mock-llm-fastapi와 함께 기동):**
- 기본 relay: delta 3개 → final, `gateway_ttfb_seconds` ≈ `firstChunkDelayMs`와 일치
- `CHAT_CORE_POOL_SIZE=1, MAX=1, QUEUE=1, ABORT`에서 동시 4개 요청 → 2개 성공(1 실행+1 대기) + 2개 503 거부,
  `gateway_executor_rejected_total=2`, `executor_queue_wait_seconds_count=2`
- `CHAT_REJECTION_POLICY=CALLER_RUNS`에서 포화 시 3번째 요청이 `executor_task_start_delay_seconds{mode="caller"}`에
  거의 0초로 기록됨(동기 실행이므로) — `mode="pool"`은 실제 대기 시간 반영
- Client를 스트림 도중 강제 종료 → `gateway_client_disconnect_total` 증가, upstream `HttpURLConnection.disconnect()`
  호출 확인, **Mock LLM 쪽 `mockllm_cancelled_requests_total`도 함께 증가**(Gateway→Mock LLM까지 취소 전파 확인),
  `gateway_async_active_requests`는 정상적으로 0으로 복귀(AsyncListener idempotent guard 정상 동작)

## 버전 (실측 확정, docs/decisions/version-compatibility.md 참고)

| 항목 | 버전 |
|---|---|
| JDK | Eclipse Temurin 1.8.0_492 |
| Gradle | 3.5.1 (Wrapper, Spring Boot 1.5.22 공식 지원 범위[2.9~3.x] 내 최신) |
| Spring Boot | 1.5.22.RELEASE |
| Spring Framework | 4.3.25.RELEASE |
| Embedded Tomcat | 8.5.43 |
| Jackson | 2.8.11.3 |

## 실패 시 확인 항목

- `docker build` 중 의존성 다운로드 실패: `buildscript`/`repositories`의 `mavenCentral()` 접근이 네트워크 정책으로
  막혀 있는지 확인
- `./gradlew` 실행 시 `Permission denied`: `chmod +x gradlew` 필요 (Git이 실행 권한을 보존하지 못했을 경우)
- 컨테이너가 뜨자마자 죽음: `docker logs <container>`로 Tomcat 포트 충돌(8080 이미 사용 중) 여부 확인
- `/healthz`가 404: `HealthController`의 `@RestController` 스캔 대상 패키지(`com.llmconcurrencylab.gatewaymvc`)
  하위에 있는지, `GatewayApplication`과 같은 패키지 트리인지 확인
- `/chat/stream`이 즉시 끊기고 `gateway_upstream_failure_total`만 증가: `MOCK_LLM_BASE_URL`이 Gateway 컨테이너
  기준으로 접근 가능한 주소인지 확인(같은 Docker 네트워크 위의 컨테이너명, `localhost` 아님)
- 모든 요청이 `executor_saturated`로 거부됨: `CHAT_CORE_POOL_SIZE`/`CHAT_MAX_POOL_SIZE`/`CHAT_QUEUE_CAPACITY`가
  기대한 값으로 주입됐는지 `/metrics`의 `gateway_executor_pool_size`로 확인
