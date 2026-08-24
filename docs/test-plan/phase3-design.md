# Phase 3 Design — Unit 1 (Contract Freeze)

Status: Accepted (Phase 3 Unit 1 scope)
Date: 2026-08-22

이 문서는 Phase 3 Unit 1의 산출물이다. Unit 1의 목적은 P3-A/P3-B/P3-C 세 구현체의 **실제 기능
구현을 시작하기 전에**, 세 구현체가 공통으로 지켜야 할 계약(contract)을 freeze하는 것이다. Unit 1
에서는 P3-A/B/C 어느 것도 구현하지 않는다 — 아래 "Unit 2 시작 준비"에 필요한 설계와 실측 근거만
확정한다.

세부 계약은 아래 4개 ADR로 분리했다. 이 문서는 그 위의 research question / 역할 / 실험 설계 /
lifecycle / scope만 다룬다.

- `docs/decisions/phase3-mvc-webclient-write-path.md` — P3-A/B Servlet write path
- `docs/decisions/phase3-timeout-cancellation.md` — absolute deadline / cancellation
- `docs/decisions/phase3-admission-connection-pool.md` — admission / connection pool
- `docs/decisions/phase3-metrics-contract.md` — metric contract

## 1. Research Questions

Phase 3는 `docs/decisions/phase3-version-compatibility.md` §0에서 이미 확정한 연구 질문 위에
선다: "Java 8을 유지하면서 Spring 세대만 현실적으로 올렸을 때(Spring 5.3.x/Boot 2.7.x), 어떤
서버 아키텍처가 동시성 부하에 더 유리한가?"를 두 개의 독립 실험으로 쪼갠다.

- **Experiment A (write-path 비용)**: 같은 Servlet(AsyncContext+PrintWriter) 응답 경로 위에서,
  outbound 호출만 blocking(HttpURLConnection, P3-A)에서 non-blocking(WebClient, P3-B)으로 바꿨을
  때 무엇이 달라지는가? — blocking outbound worker pool의 존재/부재가 유일한 통제 변수다.
- **Experiment B (서버 모델 비용)**: Servlet 스택(P3-B, MVC+WebClient) 자체를 걷어내고 완전한
  reactive 스택(P3-C, WebFlux end-to-end)으로 갔을 때 추가로 무엇이 달라지는가? — Servlet
  AsyncContext/Tomcat 스레드 모델의 존재/부재가 통제 변수다.

두 실험을 합치면 "Blocking client on Servlet" → "Non-blocking client on Servlet" → "Non-blocking
client, no Servlet" 3단 계단이 되고, 각 계단에서 정확히 하나의 변수만 바뀐다.

## 2. P3-A / P3-B / P3-C 역할

| | 서버 모델 | Outbound client | Write path |
|---|---|---|---|
| P3-A | Spring MVC (Tomcat, Servlet) | `HttpURLConnection` (blocking) | AsyncContext + PrintWriter, blocking outbound worker 필요 |
| P3-B | Spring MVC (Tomcat, Servlet) | `WebClient` (Reactor Netty, non-blocking) | AsyncContext + PrintWriter, blocking outbound worker 없음 |
| P3-C | Spring WebFlux (Reactor Netty server) | `WebClient` (Reactor Netty, non-blocking) | 완전 reactive response `Flux`, Servlet 없음 |

P3-A는 Phase 1/2의 `gateway-mvc-executor-java8`/`gateway-mvc-java21`의 relay 구조(HttpURLConnection
blocking read → PrintWriter)를 Java 8 + Spring Boot 2.7.18 런타임으로 그대로 재현한 baseline이다.
P3-B는 그 위에 outbound client만 WebClient로 치환한 것이고, P3-C는 서버 모델까지 reactive로 바꾼
것이다.

## 3. Source of Truth — 실제 재사용하는 Phase 1/2 계약

계획 문서가 아니라 실제 저장소 코드를 근거로 확인했다 (`gateway-mvc-java21`, `mock-llm-fastapi`,
`load-test-k6`). Phase 3는 아래를 새로 발명하지 않고 그대로 재사용한다.

### 3-1. Gateway API

- Endpoint: `POST /chat/stream`
- Request body: `@RequestBody(required = false) String` — Front가 보낸 raw JSON을 그대로 Mock LLM에
  relay한다(Gateway가 파싱/재구성하지 않음). body가 없으면 `"{}"`로 대체.
- Response: `AsyncContext` 기반, `Content-Type: text/event-stream`, `Content-Encoding` 없음,
  `characterEncoding=UTF-8`.
- Admission 거부 시: `503 Service Unavailable`,
  body `{"status":"REJECTED","reason":"executor_saturated"}` (JSON, SSE 아님).

(`gateway-mvc-java21/.../chat/ChatController.java:78-194`)

### 3-2. Mock LLM API

- Endpoint: `POST /mock/stream` (baseURL 기본값 `http://localhost:8000`, env
  `MOCK_LLM_BASE_URL`로 override)
- Request body(JSON, 전부 선택 필드, `StreamRequest` 모델): `requestId`, `firstChunkDelayMs`,
  `chunkIntervalMs`, `chunkCount`, `chunkSizeBytes`, `failureRate`, `midStreamFailureRate`,
  `failAtChunk`, `forcedDisconnectAfterChunk`, `maxDurationMs`, `stallAfterChunk`, `stallMs`.
- Response: `StreamingResponse`, `media_type=text/event-stream`,
  헤더 `X-Request-Id`, `Cache-Control: no-cache`.

(`mock-llm-fastapi/app/main.py:47-66,192-200`)

### 3-3. SSE event format (byte-level)

```
event: {name}\ndata: {payload}\n\n
```

(`mock-llm-fastapi/app/main.py:68-69` `sse_event()`)

이벤트 3종, 순서는 항상 `delta*` → (`final` 또는 `error`) 하나:

| event | data payload | 의미 |
|---|---|---|
| `delta` | `{"sequence":N,"text":"..."}` | 스트림 청크, `sequence`는 1부터 |
| `final` | `{"status":"COMPLETED"}` | 정상 종료, 이 이후 이벤트 없음 |
| `error` | `{"status":"FAILED","reason":"..."}` | 실패 종료, 이 이후 이벤트 없음 |

Gateway는 Mock LLM이 보낸 SSE 라인을 그대로(`readLine()` 단위로) relay한다 — 이벤트 포맷을
Gateway가 재작성하지 않는다(`ChatController.relay()` 240-264행: `writer.write(line)` +
`writer.write("\n")`, 빈 줄에서만 `flush()`). **Phase 3의 P3-A/B/C도 이 relay 원칙을 유지한다**:
Mock LLM이 보낸 SSE 텍스트를 Gateway가 파싱해서 재직렬화하지 않고, 그대로(byte-preserving) 전달한다.

### 3-4. Correlation ID — 현재 상태(Phase 2)와 Phase 3 방침

Gateway(`ChatController.stream()`)는 자체 `requestId = UUID.randomUUID()`를 생성하지만, 이 값은
**Mock LLM에 전달되지 않는다** — `requestBody`는 Front가 보낸 그대로 relay되고, Front가 우연히
`requestId` 필드를 포함하지 않는 한 Gateway-side 로그의 `requestId`와 Mock LLM-side 로그의
`requestId`(Mock LLM이 없으면 자체 생성)는 서로 다른 값이다. 이는 Phase 1/2부터 이어진 기존 동작이며
Phase 3 Unit 1에서 새로 발견한 사실이다.

**Phase 3 방침**: 이 Phase 2 동작을 승계한다(§27 correlation ID 섹션 참고) — Gateway가 생성한
`requestId`를 로그 correlation에 사용하되, Mock LLM에 전파하는 것은 Unit 1의 scope가 아니므로
바꾸지 않는다. P3-A/B/C 모두 동일하게 "Gateway 자체 requestId, Mock LLM에 미전파"를 유지해 세
구현체 간 이 축의 차이가 없도록 한다.

### 3-5. Admission 정책의 기존 재사용 근거

Phase 2 `ChatExecutorConfig`/`VirtualLimitedTaskSubmitter`/`PlatformTaskSubmitter`가 이미 실측
검증한 패턴을 Phase 3 admission 설계의 근거로 삼는다(세부는
`docs/decisions/phase3-admission-connection-pool.md`):

- P-E(`gateway-mvc-java21/.../executor/ChatExecutorConfig.java:83-96`): `core=10, max=50,
  SynchronousQueue, AbortPolicy` — "thread-first, no wait queue".
- VT-Limited(`.../executor/VirtualLimitedTaskSubmitter.java:46-66`): `Semaphore(50,
  fair=false).tryAcquire()` — non-blocking, 즉시 성공/실패.

두 경우 모두 admission ceiling=50, tryAcquire 계열(non-blocking) 즉시 성공/실패라는 점에서 동일한
의미론이다. Phase 3는 이 의미론을 세 구현체에 공통으로 승계한다.

### 3-6. k6 client-observable 검증 방식

`load-test-k6/sse-verification-test.js`가 `k6/x/sse`(xk6-sse)로 event name + data를 이벤트 단위로
관찰하는 실제 선례다(`client.on('event', ...)`, `event.name`, `event.data`). Phase 3의 client-
observable SSE contract(§4)도 이 방식으로 검증 가능해야 한다 — byte/chunk 단위가 아니라 event 단위
검증이다.

## 4. Client-observable SSE Contract (Freeze)

P3-A/P3-B/P3-C 세 구현체 모두 Client(Front 또는 k6/xk6-sse)가 관찰하는 아래 의미론이 동일해야
한다. **byte buffer/chunk boundary 동일성은 요구하지 않는다** — HTTP/TCP chunk 경계는 구현별로
달라질 수 있다.

최소 보장(client-observable, event 단위):

1. `Content-Type: text/event-stream` 동일
2. `event: {name}\ndata: {payload}\n\n` 포맷 동일 (§3-3)
3. delta의 `event.name == "delta"`이고 `sequence` 오름차순, 최종 `event.name`이
   `final`(성공) 또는 `error`(실패) 중 정확히 하나로 스트림이 종료
4. full-response buffering 금지 — 첫 이벤트가 스트림이 끝나기 전에 실제로 client에 도달
   (TTFC(client)/TTFB(server) 모두 0에 수렴하지 않아야 함, 즉 Mock LLM `firstChunkDelayMs`를
   반영해야 함)
5. 정상 스트림에서 이벤트 누락/중복 없음 (Mock LLM이 보낸 `sequence` 집합과 client가 받은
   `sequence` 집합이 정확히 일치)

이 5가지는 k6/xk6-sse로 이벤트 단위 assertion이 가능하다(Unit 5에서 실제 스크립트 작성).

## 5. Lifecycle State Machine (공통)

```
STARTED
  → ADMITTED        (admission tryAcquire 성공)
  → UPSTREAM_ACTIVE  (Mock LLM 연결/구독 시작)
  → STREAMING        (첫 이벤트 relay 시작)
  → TERMINAL
```

`ADMITTED` 실패 시 `STARTED → TERMINAL(rejected)`로 직행(§9 요구사항, Semaphore/tryAcquire
계열은 즉시 성공/실패이므로 대기 상태가 없다).

### Terminal outcome (고정 값 후보, 8종 — Phase 1/2 `gateway_request_outcome_total`과 동일 계열)

| outcome | 의미 |
|---|---|
| `completed` | Mock LLM이 자연스럽게 스트림을 끝냄 |
| `rejected` | admission tryAcquire 실패 |
| `timeout` | absolute deadline 초과(관련 세부 outcome은 §metrics-contract에서 분화 여부 결정) |
| `upstream_error` | Mock LLM과의 통신 실패(timeout 아님) |
| `client_disconnect` | client로의 write 실패/연결 종료 |
| `write_overflow` | request-local write buffer overflow (§write-path ADR) |
| `internal_error` | 위 어느 것으로도 설명 안 되는 예외 |

한 request는 정확히 하나의 terminal outcome만 가진다(Phase 1/2
`docs/decisions/request-outcome-accounting.md`의 exactly-once CAS 패턴을 P3-A/B/C 모두 승계).

Invariant(세부는 metrics-contract ADR):

```
requests_started == sum(terminal outcome counters)
```

Prometheus time-window counter로 measurement-start cohort를 복원하려 하지 않는다 — Phase 2의
교훈(client cohort와 server chronological metric은 필요 시 별도 해석)을 승계한다.

### 5-1. Lifecycle Start Parity / Application Request Population 정의 (Unit 5)

세 구현체의 실제 코드를 확인한 결과(추측 아님):

- **P3-A/P3-B**: `@RequestBody(required = false) String body`는 Spring MVC의 argument resolver가
  handler 메서드 **진입 이전에** 동기적으로 처리한다 — 즉 body decode가 항상
  `startNanos`/`requests_started`/admission/`deadlineNanos`보다 먼저 끝난다
  (`gateway-mvc-blocking-spring5`/`gateway-mvc-webclient`의 `ChatController.stream()` 실제 코드
  확인).
- **P3-C(수정 전)**: `request.bodyToMono(String.class)`가 lazy/async이므로,
  `startNanos`/`requests_started`/admission/`deadlineNanos`를 `flatMap` 바깥(구독 전)에 두면 이
  값들이 body decode **완료 전에** 먼저 계산돼 P3-A/B와 기준점이 어긋난다. Unit 5에서 발견 후
  `flatMap(requestBody -> ...)` 내부로 전체 lifecycle-start 시퀀스를 이동해 P3-A/B와 동일한 순서로
  정렬했다(`gateway-webflux`의 `ChatController.stream()` 참고).

**Phase 3 공통 정의(세 구현체 동일 적용):**

```
T0(application lifecycle 시작) := request body decode 성공 직후
```

즉 `startNanos`는 "HTTP request body를 성공적으로 다 읽은 시점" 이후에만 설정된다 — 그 이전
(HTTP/TCP parsing, body 수신 자체의 실패 등)은 container/framework-level 관심사이며 Gateway
application lifecycle에 속하지 않는다.

**Application request population**(Phase 3 전체에서 `gateway_requests_started_total` 및 이후
모든 accounting invariant의 모집단)은 다음으로 정의한다:

> request body decode에 성공해 Gateway application lifecycle에 실제 진입한 요청

body decode 자체가 실패한 요청(예: client가 업로드 도중 끊음)은 세 구현체 모두
`gateway_requests_started_total`에 포함되지 않는다 — P3-A/B는애초에 handler 메서드에 진입하지
못하므로, P3-C는 수정 후 `flatMap`에 도달하지 못하므로, 구조적으로 동일하게 제외된다. 이런
container-level 실패를 Gateway가 별도 outcome으로 억지로 집계하지 않는다.

## 6. Formal / Secondary Scope

Unit 1에서 확정하는 것은 **계약(contract)뿐**이다. 아래는 이 문서의 범위 밖이며 이후 Unit에서
결정한다:

- P3-A/B/C 실제 Gateway 구현 — Unit 2+
- Admission ceiling/write-executor pool size/queue capacity의 **정확한 숫자** — Unit 5
  functional screening 전 확정(§admission-connection-pool ADR, §write-path ADR)
- Formal load profile(VUS, arrival rate) — Unit 5/6
- Mock LLM 성능 테스트, k6 load test, Formal benchmark — 전부 금지(§29 그대로 승계)

## 7. Unit 1에서 실행한 것 (허용 범위 내)

- 저장소 실제 코드 감사(§3)
- Reactor Netty 1.0.39 `ConnectionProvider` API 실측(`javap` 및 실행 테스트) —
  `docs/decisions/phase3-admission-connection-pool.md` §2
- absolute deadline operator 후보 3종 실행 검증(Reactor Core 3.4.34) —
  `docs/decisions/phase3-timeout-cancellation.md` §3, 실제 **버그 2건을 코드로 발견**
- Actuator `/actuator/prometheus` capability spike(Spring Boot 2.7.18 + Micrometer 1.9.17 +
  Tomcat 9.0.83 + Reactor Netty 1.0.39) — `docs/decisions/phase3-metrics-contract.md` §2

모든 spike의 원본 로그/소스는 `docs/test-results/phase3/unit1-capability-spikes/`에 보존했다.
spike 코드 자체는 disposable이며 저장소에 정식 module로 커밋하지 않는다.

## 8. Correlation ID / Logging Contract

요청마다 §3-4의 Gateway 자체 `requestId`를 생성하고 로그 전반에 전파한다(Mock LLM에는 전파하지
않음 — §3-4 참고). 최소 lifecycle debug 로그 지점(P3-A/B/C 공통):

- request start(admission 이전)
- admission reject
- upstream start(Mock LLM 연결/구독 시작)
- first chunk(TTFB 발생 시점)
- terminal outcome(§5의 outcome 값 중 하나 + 소요 시간)

**Formal 실행 시**: 로그 레벨은 **INFO 고정**. chunk 단위 상세 로그(각 delta 이벤트마다 로그를
남기는 것)는 금지 — Formal 부하 하에서 로깅 자체가 병목이 되는 것을 막기 위함이며, Phase 1/2도
같은 원칙을 지켜왔다(`ChatController.relay()`는 chunk 루프 안에서 로그를 남기지 않고, 위 5개
lifecycle 지점에서만 로그를 남긴다).

`requestId`는 metrics-contract ADR §7에서 이미 명시한 대로 metric 태그로는 쓰지 않는다 — 로그
전용이다.

## 9. Unit 5 — DNS 정책, Config-axis Freeze, Warm-up 원칙

### 9-1. DNS 정책(고정)

Unit 3에서 관찰된 macOS native DNS resolver 경고(`Unable to load
MacOSDnsServerAddressStreamProvider`, 근본 원인은
`docs/decisions/phase3-reactor-resource-topology.md` §6에 기록)를 Experiment A/B의 confound에서
제거하기 위해, Unit 5부터 **Phase 3 Formal까지** 세 구현체 모두:

```
MOCK_LLM_BASE_URL=http://127.0.0.1:8000
```

를 사용한다(`localhost`/hostname/실제 DNS lookup 금지). P3-A(`HttpURLConnection`)/P3-B/P3-C
(`WebClient`) 모두 동일 정책 적용.

### 9-2. Unit 6 Screening 전까지 반드시 P3-A/B/C에서 동일하게 유지해야 하는 config axis(freeze)

정확한 숫자는 Unit 6 screening 근거 확보 전까지 확정하지 않는다(§Formal Config Freeze 원칙,
`docs/decisions/phase3-metrics-contract.md`/`phase3-admission-connection-pool.md`와 동일). 단
"어떤 축이 세 구현체에서 반드시 같은 값이어야 하는가"는 지금 freeze한다:

- `CHAT_ADMISSION_LIMIT` (application admission ceiling)
- `CHAT_TOTAL_TIMEOUT_MS` (absolute deadline)
- `CHAT_ASYNC_WATCHDOG_MARGIN_MS` (P3-A/B safety watchdog margin — P3-C는 해당 없음, §14 참고)
- `MOCK_LLM_BASE_URL` (§9-1, 127.0.0.1 고정)
- P3-B/P3-C: `WEBCLIENT_MAX_CONNECTIONS`, `WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT`,
  `WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS`, `WEBCLIENT_CONNECT_TIMEOUT_MS`, `WEBCLIENT_LOOP_THREADS`
  (`docs/decisions/phase3-reactor-resource-topology.md` — 동일 `ConnectionProvider`/
  `LoopResources` 정책)
- P3-A/B: `SERVLET_WRITE_POOL_SIZE`, `SERVLET_WRITE_QUEUE_CAPACITY`,
  `SERVLET_PER_STREAM_BUFFER_CAPACITY`(P3-C는 해당 없음)
- P3-A만: `CHAT_BLOCKING_POOL_SIZE`(P3-B/C는 해당 없음, admission ceiling과 동일 값 사용 원칙 유지)

이 목록 자체가 freeze 대상이며, 각 축의 **값**은 Unit 6 이전에 근거를 갖고 확정한다.

### 9-3. Warm-up 요구사항 원칙(freeze)

Unit 3에서 P3-B의 첫 WebClient 요청에서, Unit 4에서 P3-C의 첫 요청에서 각각 cold-start 비용
(첫 요청 TTFB가 이후 요청보다 유의미하게 높음)이 관찰됐다(Reactor Netty/코덱/커넥션 초기화 비용
추정, 정밀 원인 분석은 하지 않음). 이 관찰로부터 다음을 원칙으로 freeze한다:

> Screening(Unit 6)과 Formal(이후 Unit) 어디에서든, 세 구현체 **모두에 동일한 warm-up phase**를
> 거친 뒤 측정을 시작해야 한다 — 특정 구현체만 warm-up을 건너뛰거나 다른 길이의 warm-up을 적용하면
> 안 된다.

warm-up 자체의 성능 수치는 지금 비교하지 않으며, warm-up 구간은 향후 measurement population에
포함하지 않는다(Unit 6 이후 harness가 구체적인 warm-up 절차/길이를 정의한다 — 이 Unit에서는
"필요하다"는 원칙만 freeze).

## 10. Unit 5.5 — Blocking / Slow-Client Diagnostic 결과 반영

Unit 5.5는 **benchmark가 아니라 diagnostic**이었다(BlockHound로 non-blocking 경로에 실제
blocking 호출이 있는지, Slow Client 조건에서 P3-B/P3-C가 구조적으로 어떤 리소스 패턴을 보이는지
두 질문만 다룸). 전체 결과는
`docs/test-results/phase3/unit5.5-diagnostics/SUMMARY.md`에 있으며, 이 절은 Screening/Formal
설계에 영향을 주는 부분만 원칙으로 freeze한다.

### 10-1. Screening/Formal에서 BlockHound는 계속 OFF

BlockHound는 Java agent instrumentation이며 diagnostic 목적으로만 사용한다. **Unit 6
Screening과 이후 Formal 어디에서도 BlockHound를 켠 상태로 측정하지 않는다** — instrumentation
overhead가 측정치를 오염시키기 때문이다. BlockHound가 필요해지면(회귀 의심 등) 별도
diagnostic run으로 수행하고 `docs/test-results/phase3/unit5.5-diagnostics/blockhound/` 패턴을
따라 evidence를 분리 보존한다.

### 10-2. Screening/Formal 트래픽에 Slow Client workload를 포함하지 않는다

Unit 5.5 §10~§16에서 사용한 Slow Client(xk6-sse 기반, 인위적으로 소비 속도를 늦춘 클라이언트)와
그에 맞춘 Mock LLM 대용량 워크로드(`chunkCount`/`chunkSizeBytes` 상향)는 **구조적 관찰 전용**이며,
Screening/Formal의 정상 트래픽 프로파일에 섞지 않는다. Formal의 VUS/arrival rate/워크로드 크기는
여전히 Unit 6이 별도로 정의한다(§6).

### 10-3. capacity-planning 관점에서 기록해 둘 위험 — P3-C의 admission permit 해제 시점

Slow Client 진단 중, P3-C(WebFlux)에서 `terminal: outcome=completed`(및 admission permit 해제)가
클라이언트가 실제로 데이터 수신을 마친 시점보다 훨씬 먼저 발생하는 현상이 재현성 있게 관찰됐다
(60-chunk 시나리오: 169ms vs 클라이언트 관측 9.32s, 20-chunk 시나리오: 25ms vs 3.18s — 두 규모
모두 동일 패턴). **실측된 사실은 정확히 이것뿐이다**: P3-C의 reactive response Publisher terminal
boundary가 client physical receive completion보다 크게 선행한다. Reactor Netty의 내부
write/flush 단계를 직접 계측하지 않았으므로, "모든 아이템이 Reactor Netty 내부 채널 write
buffer에 accept됨을 complete로 간주하기 때문"이라는 설명은 WebFlux/Reactor Netty의 문서화된
demand-driven 비동기 쓰기 모델과 부합하는 **구조적 가설**이지 직접 확인된 root cause가 아니다
(상세: `docs/test-results/phase3/unit5.5-diagnostics/SUMMARY.md` D2). 이는 버그가 아니다 — 정확한
내부 메커니즘이 무엇이든, 관찰된 현상(terminal이 물리적 전달에 선행) 자체는 reactive/non-blocking
모델의 결과다. 반면 P3-A/B는 bounded per-stream buffer + fail-fast overflow로 느린 클라이언트를
명시적으로 경계짓는다(대신 그런 스트림 자체가 실패로 종료됨).

성능 우열 주장은 하지 않는다. 이 발견은 **Unit 6 시작 전 반드시 해결하거나 범위를 정의해야 하는
중요한 미해결 설계 문제**로 취급한다 — "그 외 실질적 미해결 위험 없음"이라고 결론짓지 않는다:
P3-C의 admission ceiling은 "관리상 동시 처리 중인 요청 수"만 제한하며, 느린 클라이언트에게 아직
물리적으로 전달되지 않은 바이트가 Reactor Netty 내부 버퍼에 얼마나 쌓여 있는지는 제한하지 않는다
— 반면 P3-A/B는 permit을 Servlet 최종 write/drain terminal까지 유지한다. 즉 동일한
`CHAT_ADMISSION_LIMIT` 값이 세 구현체에서 동일한 physical concurrency를 의미한다고 가정할 수
없다. 이 질문은 Unit 6 Admission Semantics Audit(`docs/decisions/
phase3-admission-semantics-unification.md`)에서 해결한다.
