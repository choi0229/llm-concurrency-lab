# Phase 4 Design — Scalability Boundary (PT-Queue / VT / WebFlux)

Status: Accepted (Phase 4 Unit 1 scope — contract freeze only, no implementation yet; §0 added in
Unit 2 to close 3 residual ambiguities before implementation started)
Date: 2026-08-25

## 0. Unit 2 pre-implementation ambiguity closure (added before any M1/M2/M3 code was written)

### 0-1. M1 `ArrayBlockingQueue` exact construction — freeze

```java
new ThreadPoolExecutor(
    50, 50, 0L, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(500, false),   // fairness=false
    threadFactory, new ThreadPoolExecutor.AbortPolicy());
```

- `fairness=false`: `ArrayBlockingQueue`'s single-arg and `(capacity, false)` constructors are
  identical per the JDK `ArrayBlockingQueue` Javadoc (default access order is non-fair/barging).
  Non-fair is the JDK default; Phase 4 does not introduce fairness-ordering overhead as an
  additional experimental variable.
- `prestartAllCoreThreads = false` (i.e., not called): `ThreadPoolExecutor`'s default lazy-start
  behavior is used — core threads are created on demand as tasks arrive, up to `corePoolSize`, per
  the JDK `ThreadPoolExecutor` Javadoc. Phase 4 does not force all 50 platform threads to exist at
  JVM startup; this keeps M1 a representative, not artificially inflated, "practical" executor.
- `keepAliveTime=0`/irrelevant: with `core==max==50` and `allowCoreThreadTimeOut` left at its
  default (`false`), core threads never idle-expire regardless of the keep-alive value, so the
  value itself has no observable effect — kept at `0` for clarity rather than carrying over an
  unused `60s` figure from Phase 2's P-E.

**Frozen. Not changed after Unit 5 screening starts.**

### 0-2. M1/M2 common Servlet write executor — exact construction

```java
// shared write executor (M1 and M2 both wire the identical class/config)
new ThreadPoolExecutor(
    64, 64, 0L, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(20000, false),
    threadFactory, new ThreadPoolExecutor.AbortPolicy());
```

- `core=max=64`, `ArrayBlockingQueue<>(20000, false)`, `AbortPolicy`, `prestartAllCoreThreads=false`
  — same rationale as §0-1.
- **Per-stream frame queue**: `ArrayBlockingQueue<Frame>(8)`, non-blocking `offer()` only (the SSE
  relay thread must never block on a full per-stream buffer — a full buffer is an overflow
  condition, not backpressure).
- **Two distinct overflow triggers, one outcome semantic**: (a) per-stream queue `offer()` returns
  `false` (that stream's own buffer is full), or (b) the shared write executor itself rejects a
  submitted drain task (`AbortPolicy`, all 64 threads + 20000 queue full). Both map to the same
  terminal outcome, `write_overflow` — recorded through the same idempotent CAS terminal path
  (§Common Request Lifecycle), so only the first trigger for a given request actually counts. They
  are distinguished only for **diagnostics** via two separate counters
  (`servlet.write.overflow.perstream` / `servlet.write.overflow.executor`), not as separate outcome
  values.

### 0-3. M3 Reactor Netty loop topology — freeze

M3 uses the Boot/Reactor **default** resource topology for both the embedded Reactor Netty server
and the `WebClient`'s Reactor Netty client — no custom `HttpClient.runOn(customLoopResources)` /
`HttpServer.runOn(...)` tuning. A `ConnectionProvider` is configured (for the headroom-controlled
`WEBCLIENT_MAX_CONNECTIONS` etc., `phase4-design.md` §7-1) but no custom `LoopResources` is
attached to it. This keeps M3 a representative default-configuration reactive architecture rather
than a hand-tuned one, consistent with §3's fairness principle (M3's identity is "native reactive,"
not "reactive with bespoke thread-pool engineering").

**No thread-count claim is carried over from Phase 3 or Unit 0 by assumption** — actual
server/client thread names and counts are observed at runtime in Unit 2 functional smoke and
recorded as evidence (`docs/test-results/phase4/unit2-functional/thread-topology.txt`), not
predicted.

## 1. Research Question

> 같은 runtime / 같은 workload / 같은 host에서 Platform Thread+Executor Queue, Virtual Thread,
> WebFlux 세 모델은 실제로 어느 부하까지 안정적으로 scale하며, 한계에 도달할 때 어떤 resource가
> 먼저 saturation되는가?

Phase 1~3와 달리 "누가 더 빠른가"가 아니라 **scalability boundary와 그 boundary에서의 resource
signature**를 찾는다. Phase 1의 "overload 비용은 사라지지 않고 이동한다"를 최종 boundary에서 다시
확인하는 실험이다.

## 2. Common Runtime

Phase 4 Unit 0(`docs/decisions/phase4-runtime.md`)에서 freeze:

- Eclipse Temurin JDK 21.0.11+10, native macOS ARM64, Rosetta 없음
- Gradle 8.14
- Spring Boot 4.1.0 / Spring Framework 7.0.8
- Tomcat 11.0.22(M1/M2)
- Reactor Core 3.8.6 / Reactor Netty 1.3.6(M3)
- Micrometer 1.17.0(세 모델 공통, §7)
- k6 v1.8.0 + xk6-sse v0.1.11
- host: 이 dev Mac, 10 core, 16GB RAM, Docker 미사용(native)

## 3. Architecture Fairness Principle

Phase 4는 동일 코드에 thread flag 하나만 바꾸는 실험이 아니다. 가능한 부분은 최대한 통제하되,
최종 claim은 **"세 대표 concurrency architecture의 scalability boundary comparison"**으로
제한한다 — "WebFlux라는 keyword 하나의 순수 causal effect"라고 주장하지 않는다.

### M1 vs M2 (강하게 통제)

M1/M2는 다음을 모두 동일하게 유지한다:

- Spring MVC + Tomcat + `AsyncContext`
- 동일 API/SSE contract(§8)
- 동일 request lifecycle(§9)
- 동일 absolute timeout(§10)
- 동일 outbound: `java.net.HttpURLConnection`
- 동일 SSE decoder/relay 로직
- 동일 Servlet response write path(§6)
- 동일 metrics(Micrometer, §7)

의도적 차이는 정확히 하나: **outbound blocking task를 어떤 thread model이 수행하는가**
(bounded `ThreadPoolExecutor` vs `VirtualThreadPerTaskExecutor`).

### M3

Spring WebFlux + Reactor Netty Server + WebClient — representative reactive architecture. M1/M2와
코드를 공유하지 않는다(공유하면 오히려 WebFlux의 아키텍처적 정체성을 훼손함, Phase 3의 동일 원칙
계승 — `docs/decisions/phase3-reactor-resource-topology.md`).

## 4. M1 — PT-QUEUE Architecture

```
Spring MVC → AsyncContext → ThreadPoolExecutor(bounded queue) → HttpURLConnection
  → Mock SSE → common Servlet response write path
```

### 4-1. Queue 타입 — freeze: `ArrayBlockingQueue`

두 후보(`ArrayBlockingQueue` vs `LinkedBlockingQueue(capacity)`)를 JDK `java.util.concurrent`
공식 Javadoc 명시 semantics로 비교했다:

| | `ArrayBlockingQueue` | `LinkedBlockingQueue(capacity)` |
|---|---|---|
| 내부 구조 | 생성 시점에 고정 크기 배열 미리 할당 | capacity가 있어도 노드를 삽입 시점마다 동적 할당(linked node) |
| 락 구조 | put/take가 단일 lock 공유 | put/take가 별도 lock(2-lock queue) — 더 높은 처리량이 목적이나 이 프로젝트의 목적(단순하고 해석하기 쉬운 queue depth)과는 무관 |
| Queue depth 해석 | 배열 점유율 = 정확히 "지금 대기 중인 task 수", 메모리 사용량은 capacity로 상한이 고정(사전 할당) | 논리적 depth는 동일하게 셀 수 있으나, 실제 메모리 할당/해제가 삽입/제거마다 일어나 GC 압력이 ArrayBlockingQueue보다 원리적으로 더 크다 — Phase 4가 RSS/GC를 M1의 resource metric으로 쓰므로 이 차이가 confound가 될 수 있음 |

**결정: `ArrayBlockingQueue`.** 이유: (1) capacity가 사전에 고정 배열로 할당되어 "queue capacity"라는
architecture parameter의 의미가 메모리 관점에서도 정확히 일치하고, (2) M1의 RSS/GC 관찰이 queue
구현체 자체의 동적 할당 패턴에 오염되지 않는다.

### 4-2. Worker count — freeze: 50 (fixed, core=max=50)

`Executors.newFixedThreadPool`과 동등한 방식으로 `corePoolSize=maxPoolSize=50`을 직접
`ThreadPoolExecutor`에 지정한다(host CPU core 수나 `availableProcessors()` 기반 동적 sizing은
사용하지 않음 — §54 freeze 원칙).

근거:
- Phase 1/2에서 반복 검증된 규모(Phase 2 P-E도 max=50)를 Phase 4의 출발점으로 재사용.
- Normal SSE workload는 CPU-bound가 아니라 blocking I/O-bound(각 task가 대부분의 시간을
  `HttpURLConnection` 소켓 read 대기)이므로, worker 수를 CPU core 수(10)에 맞출 이유가 없다 —
  I/O-bound task는 스레드 수를 core 수보다 훨씬 크게 잡는 것이 표준적인 선택이다.
- Little's Law로 raw service capacity를 계산하면: service time ≈ 7.8s(§워크로드), worker=50 →
  이론적 sustained throughput 상한 ≈ 50/7.8 ≈ **6.41 req/s**(고정 τ 가정, 큐잉 지연 없는 이상적
  steady-state). 이 값이 M1의 architecture-defined raw ceiling이며, Open Formal(MSAR)에서 M1이
  이 근처에서 saturate되는지가 검증 대상이 된다(H4-b).
- 16GB Mac에서 platform thread 50개의 memory footprint(각 thread stack 기본 1MB 전후) 자체가
  실험을 파괴할 위험은 낮다.

**해석 주의**: worker=50 채택은 "Platform Thread의 이론적 최대치가 50"이라는 뜻이 아니다. 정확한
해석은 **"Phase 4에서 사전에 freeze한 50-worker bounded Platform Thread architecture의 boundary"**다.

### 4-3. Queue capacity — freeze: 500

worker=50, queue=500 → M1이 한 번에 보유 가능한 최대 task는 **550**(즉시 서비스 50 + 대기
500)이다. 이 구조적 계산은 예비 architecture 설명일 뿐 실측 결과가 아니다.

근거:
- 너무 작으면(queue가 worker 수에 가까우면) worker saturation 직후 바로 reject가 발생해 "Queue
  Wait이 TTFC를 어떻게 열화시키는가"(H4-b)라는 M1의 핵심 관찰 대상 자체를 만들 수 없다.
- 너무 크면 사실상 unbounded backlog architecture가 되어 M1이 PT-QUEUE라는 정체성(bounded
  resource control)을 잃는다.
- 500은 worker의 10배 규모로, 짧은 burst(예: closed-model 200~400 concurrent 진입 순간)를
  흡수하면서도 "reject 없이 무한정 받아주는" 구조가 되지 않도록 설계했다.
- **queue capacity 자체가 MRC/RED boundary에 영향을 준다는 사실은 limitation이 아니라 M1
  architecture의 명시적 정책으로 기록한다** — 이 값이 다르면 M1의 MRC/MSC도 달라질 것이라는 점을
  Phase 4 Final Report에 명시한다.

### 4-4. Rejection policy — freeze: `AbortPolicy`

`CallerRunsPolicy` 금지(Tomcat caller thread를 blocking workload에 참여시켜 새로운 confound를
만들기 때문, Unit 0 design brief §3과 동일 이유). `AbortPolicy`가 `RejectedExecutionException`을
던지면 이를 `outcome=rejected`로 매핑한다(§11).

### 4-5. Capacity Interpretation — 세 종류의 boundary를 분리한다

M1에는 최소 세 종류의 서로 다른 boundary가 있을 수 있으며, 의도적으로 분리해서 관찰한다:

1. **Worker saturation** — `executor_active == 50`(§metrics-contract).
2. **SLO saturation** — queue wait 누적으로 TTFC/stream-duration SLO 실패(AMBER).
3. **Hard architecture boundary** — worker+queue 모두 가득 차 `AbortPolicy`가 발동(RED,
   `outcome=rejected`).

M1의 MSC, MRC, "첫 rejection이 발생하는 N"은 서로 다른 값일 수 있다(§scalability-definition.md
§MRC/MSC).

## 5. M2 — Virtual Thread Architecture

```
Spring MVC → AsyncContext → VirtualThreadPerTaskExecutor → HttpURLConnection
  → Mock SSE → M1과 동일 common Servlet response write path
```

- Application admission 없음. `Semaphore`/VT-Limited/bounded task queue/artificial concurrency
  ceiling 금지 — 요청마다 virtual thread task를 새로 생성한다(`Executors.
  newVirtualThreadPerTaskExecutor()`, Unit 0 Spike B에서 `Thread.isVirtual()==true`로 실증).
- Primary metric은 **virtual task active**(custom gauge)와 **JVM live platform-thread
  count**(`jvm_threads_live_threads`, Micrometer `JvmThreadMetrics`)를 별개 지표로 분리한다 —
  전자는 애플리케이션이 만든 virtual thread 수, 후자는 JVM이 실제로 점유한 OS-level platform
  thread 수(virtual thread carrier pool 크기 + Tomcat NIO worker 등)이며 **동일하지 않다**.
  `jvm_threads_live_threads`가 virtual thread 수 자체를 포함하는지 여부는 Unit 2에서 실제
  `/actuator/prometheus` 출력으로 확인한다(§metrics-contract.md §CPU/Thread Policy) — 추측하지
  않는다.
- H4-c(§13)가 바로 이 두 지표의 관계(1:1 증가 여부)를 검증 대상으로 삼는다.

## 6. M1/M2 공통 — Servlet Write Path Contract

M1/M2에서 outbound thread model 이외에 response architecture가 달라지면 안 된다(§3 fairness
principle). Phase 3에서 검증한 "request-local serialized channel + shared bounded Servlet write
executor + `PrintWriter`" 구조를 **설계 패턴으로만** 재사용 후보로 참고하되, Phase 3 Java 8 모듈의
코드를 직접 재사용/수정하지 않고 Phase 4 공통 컴포넌트로 새로 작성한다(Unit 2 구현 범위).

Phase 4 Normal SSE workload(§11, chunkSize=64B, 35 chunks, 200ms interval)는 output이 매우
작고 느리므로, write subsystem이 Primary scalability boundary가 되는 것 자체가 목적이 아니다
— 충분한 headroom을 사전에 설계한다.

| 항목 | Freeze 값 | 근거 |
|---|---|---|
| Write executor 크기 | 고정 64 threads(core=max=64) | M1의 50 worker보다 크게 잡아, write executor가 M1의 executor보다 먼저 병목이 되지 않게 한다. M2는 admission이 없어 훨씬 높은 concurrency(수천)까지 갈 수 있으므로, write executor 자체도 "빠른 논블로킹에 가까운 짧은 write 작업"을 다수 스레드로 병렬 처리하는 구조로 설계한다. |
| Write queue capacity | 20,000(bounded) | Unit 0 design brief의 closed screening 최상단 후보(5000)의 4배 — 사전 정의된 screening 상한보다 충분한 headroom을 두어, Unit 3/4/5 calibration 전부터 write queue가 hidden limiter가 되지 않게 한다. |
| Per-stream buffer capacity | 8 frames | 각 stream은 실제로 한 번에 최대 1~2개의 pending frame만 가지면 충분(200ms 간격의 순차 chunk이므로) — 8은 순간적인 지연을 흡수할 여유를 주면서도 per-stream 무제한 버퍼링(memory leak 위험)을 방지한다. |
| Overflow semantic | 버퍼 초과 시 해당 stream을 종료하고 `outcome=write_overflow`로 기록(연결을 강제로 끊지 않고 우아하게 종료) | Phase 3의 `gateway.write.overflow`/`servlet.write.overflow` 패턴과 동일한 개념(§metrics-contract.md) — silent drop 금지, 반드시 outcome에 반영. |
| Ordering | Stream별 FIFO(request-local serialized channel 하나가 그 stream의 모든 frame을 순서대로 처리) | SSE는 순서가 의미를 가지므로(delta 순번) 필수. |
| Final-drain semantic | upstream이 `final` 이벤트를 보낸 뒤에도, 그 stream에 남은 pending frame을 모두 write할 때까지 `outcome=completed`로 확정하지 않는다 | Phase 3의 "drain 완료 후 postflight" 원칙과 동일. |
| Client-disconnect 감지 | write 시도 중 `IOException` 또는 Servlet `AsyncListener.onError`/`onComplete` 콜백 | 표준 Servlet 비동기 lifecycle. |

**중요(§9 원칙)**: Unit 3/4 calibration에서 write queue/buffer overflow가 M1/M2의 model
boundary보다 먼저 발생한다면 이를 숨기지 않고 `SERVLET_WRITE_LIMIT`(§failure taxonomy)으로
분류한다. 결과를 맞추기 위해 screening 이후 write pool을 키우는 것은 금지 — 이 표의 값이 부족하면
**Formal 시작 전**에만 재검토한다(Formal 진행 중 변경 금지).

## 7. M3 — WebFlux Architecture

```
Reactor Netty Server → WebFlux → WebClient → Reactor Netty Client → Mock SSE → reactive response
```

금지: `AsyncContext`, Servlet API, `PrintWriter`, Servlet write executor, blocking outbound
worker, `boundedElastic`로 blocking architecture 흉내내기, application admission gate.

M3는 native reactive response architecture를 그대로 유지한다 — M1/M2에 맞추기 위해 인위적인
buffering/executor를 추가하지 않는다(Phase 3 Unit 5 §18과 동일 원칙, `docs/decisions/
phase3-metrics-contract.md` §8).

### 7-1. WebClient `ConnectionProvider` 정책 — 숫자는 Unit 3 이후 freeze

`WEBCLIENT_MAX_CONNECTIONS`가 숨은 admission ceiling이 되면 안 된다. 원칙만 지금 freeze한다:

- `WEBCLIENT_MAX_CONNECTIONS`는 **planned Gateway max concurrency의 최소 1.25배**(headroom
  정책, §resource-safety-policy.md의 Direct Mock headroom과 동일 비율을 채택)여야 한다.
- 정확한 숫자는 Unit 3 control calibration이 Gateway target range를 확정한 뒤, Closed
  Screening 시작 **전에** freeze한다 — 이는 결과를 보고 튜닝하는 것이 아니라, Gateway target
  range 자체가 Unit 3의 산출물이라서 그 전에는 exact connection count를 정할 수 없는 구조적
  dependency다.
- Primary Normal workload 실행 중 `pending acquire`(§metrics-contract.md 후보 metric)가 지속적으로
  0보다 크면 hidden limiter/anomaly로 취급한다.

### 7-1-1. Unit 3 freeze — 숫자 확정

Unit 3 Direct Mock closed-model control calibration 결과(`docs/test-results/phase4/
unit3-control-calibration/closed-control-summary.json`): robust clean ceiling **N=800**(낮은
run-to-run variance, TTFC/duration p95가 baseline 대비 완만하게만 증가), N=1600은 두 반복 실행 사이
TTFC p95가 2.09s~5.03s로 큰 변동을 보여 "안정적으로 clean"의 근거로 채택하지 않음(marginal), N=3200에서
명확한 실패(785/3200 미완료) 관찰.

```
SAFE_CLOSED_MAX = robust_clean_ceiling(800) / 1.25 = 640
WEBCLIENT_MAX_CONNECTIONS(screening) = SAFE_CLOSED_MAX(640) × 1.25 = 800
```

**freeze**: `WEBCLIENT_MAX_CONNECTIONS=800`(screening override — M3 `WebClientConfig`의 코드 기본값
100은 "FUNCTIONAL_ONLY_NOT_PHASE4_CAPACITY_CONFIG"로 그대로 유지, Unit 4/5 harness가 env로
override), `WEBCLIENT_PENDING_ACQUIRE_MAX_COUNT=800`(maxConnections와 동일 크기 — pending queue
자체가 조기 limiter가 되지 않도록 여유), `WEBCLIENT_PENDING_ACQUIRE_TIMEOUT_MS`/
`WEBCLIENT_CONNECT_TIMEOUT_MS`는 기존 기본값(10000ms/3000ms) 유지 — headroom 정책과 직접 관련 없음.

M1 hard architecture ceiling(worker 50+queue 500=550)과의 관계: `800 / 550 ≈ 1.45×` ≥ 1.25× 최소
headroom — **CONTROL BLOCKER 아님**, M1 자체 boundary를 control-limited 없이 관찰 가능.

## 8. Common API / SSE Contract

세 모델 공통:

- `POST /chat/stream` — request body는 기존 Normal request body semantics(Phase 1~3에서 이미
  검증된 필드셋)를 재사용한다.
- Mock: `POST /mock/stream`(무수정 재사용, Unit 0 §7 확인).
- Response `Content-Type: text/event-stream`.
- Semantic SSE event 이름: `delta`, `final`, 그리고 Mock/Gateway semantics에 실제로 존재하는 경우
  `error`(Unit 0에서 확인한 Mock 소스 기준 `error` 이벤트가 실제 존재함 — §mock-llm-fastapi
  `sse_event("error", ...)` 호출 다수 확인, 그대로 포함).
- **Byte-identical한 raw whitespace/HTTP chunk boundary는 요구하지 않는다.** SSE parser가
  관찰하는 `event name` / `payload` / `order` / `final` 여부가 세 모델에서 동일해야 한다(Phase 3
  Unit 5가 실측으로 확인한 것과 동일한 종류의 구조적 차이 — 콜론 뒤 공백 유무 등 — 가 M3에서도
  나타날 수 있음을 예상하고 이를 계약 위반으로 취급하지 않는다).

### 8-1. Parity claim의 정확한 범위 — Unit 2 실측 이후 명확화

Unit 2가 실제로 검증/확인한 것은 다음 네 가지이며, 이것들을 "parity"라고 부른다:

- Normal SSE semantic parity(§8 위 항목들 — event/data/order/final)
- application lifecycle/outcome semantic parity(§9의 "body decode 성공 = lifecycle start"가 세
  모델에 동일 적용, outcome label set 동일)
- timeout/disconnect/upstream-error **classification** parity(세 모델 모두 동일한 원인을 동일한
  outcome 이름으로 분류 — `timeout`/`client_disconnect`/`upstream_error`)
- common custom metric 이름/semantic parity(`docs/decisions/phase4-metrics-contract.md` §3)

**"client-visible HTTP contract가 완전히 동일하다"고는 표현하지 않는다.** Unit 2 F4(upstream
connection failure)에서 실측된 대로, 실패 시 client가 실제로 받는 HTTP 응답은
architecture-specific이다 — M1/M2는 이미 커밋된 200 응답(빈 body, Servlet 조기 커밋 구조)으로
끝나고, M3는 응답 커밋 전에 에러가 발생해 실제 500 응답(WebFlux lazy-commit 구조)이 된다. 두
경우 모두 Gateway 내부 outcome 분류는 동일하게 `upstream_error`이지만, **client가 실제로 보는
HTTP status/body는 다르다** — 이 차이는 architecture-specific이며 맞추기 위해 어느 쪽도 수정하지
않는다(Phase 3 Unit 5 §8-3/§8-4와 동일 원칙, `docs/test-results/phase4/unit2-functional/SUMMARY.md`
F4 행 참고).

## 9. Request Population — Application Lifecycle Start

세 모델에서 "application request 시작"의 의미를 통일한다:

**request body decode 성공 → application lifecycle start**(`startNanos` 기록, task/subscription
생성 이전).

**M1의 queue 대기 시간은 application lifecycle에 포함한다** — M1의 scalability degradation
핵심이 Queue Wait이므로, queue에 들어간 시점부터 이미 "started"로 카운트해야 M1의 실제 client-facing
지연(TTFC 등)에 queue wait이 반영된다.

## 10. Timeout Contract — freeze: `CHAT_TOTAL_TIMEOUT_MS=60000`

세 모델 동일 absolute total deadline(60초), deadline 시작 시점은 §9의 application lifecycle
start와 동일하게 통일한다.

- Normal stream은 약 7.8초이므로 stable load에는 충분한 headroom(약 7.7배).
- M1 queue overload 상황에서는 queue wait + service time이 60초를 넘으면 timeout이 실제
  reliability boundary가 될 수 있다 — **이를 숨기지 않는다.** 이것은 M1 architecture의 자연스러운
  behavior이지 버그가 아니다.
- Per-chunk idle timeout(존재한다면)과 absolute deadline을 혼동하지 않는다 — Phase 4 Primary는
  absolute deadline만 사용한다(per-chunk idle timeout은 이번 Unit에서 도입하지 않음).
- **Formal 진행 중 이 값을 늘려 M1 결과를 개선하는 행위는 금지한다.**

## 11. Cancellation Contract

Application outcome은 세 모델에서 동일 semantic(§metrics-contract.md outcome label)으로
매핑하되, cleanup mechanism 자체는 architecture-specific이다:

- **M1**: timeout/cancel 시점에 task가 아직 queue에 있으면 queue에서 제거 가능한지 Unit 2
  구현에서 확인한다(`ThreadPoolExecutor`가 `Runnable`을 `remove()`로 큐에서 제거하는 것은 JDK
  표준 API로 가능 — 실제 채택 여부는 Unit 2에서 구현). 이미 worker에서 `HttpURLConnection`
  blocking 중이면 소켓/read cancellation semantics를 Unit 2에서 확인(`disconnect()` 및
  interrupt 조합).
- **M2**: virtual thread interrupt + `HttpURLConnection` cleanup(blocking read는 interrupt에
  반응하지 않을 수 있으므로 `disconnect()` 병행 필요 여부를 Unit 2에서 확인).
- **M3**: reactive cancellation → WebClient upstream request cancel(Reactor의 표준
  `Mono`/`Flux` cancellation propagation).

## 12. Application Admission Policy

Phase 4 Primary는 **application admission gate가 없다.**

- M1의 bounded executor+queue는 M1 **architecture의 resource policy**이며, 별도의 공통
  admission gate로 간주하지 않는다.
- M2/M3에 M1의 queue capacity(550)와 숫자를 맞추기 위한 가짜 admission gate를 만들지 않는다 —
  이 비대칭 자체가 Phase 4의 연구 질문이다.

## 13. Hypotheses — H4-a ~ H4-g (Final wording, freeze)

Screening 결과를 보기 전에 최종 wording을 freeze한다. 이후 결과에 맞춰 wording을 바꾸지 않는다.

- **H4-a**: PT-QUEUE / VT / WebFlux의 Maximum Sustainable Concurrency(MSC)가 동일한지 검증한다.
- **H4-b**: PT-QUEUE에서 concurrency 증가에 따라 executor active saturation, queue depth/wait,
  client TTFC가 어떤 관계를 보이는지 검증한다.
- **H4-c**: VT에서 virtual-task concurrency 증가가 JVM live platform-thread count와 1:1로
  증가하는지 검증한다.
- **H4-d**: WebFlux에서 concurrent stream 증가에 따라 JVM live platform-thread footprint가 어떤
  scaling curve를 보이는지 검증한다.
- **H4-e**: VT/WebFlux의 first non-sustainable boundary에서 어떤 resource/failure signature가
  관찰되는지 탐색한다. 원인을 미리 CPU/RSS/socket 중 하나로 확정하지 않는다.
- **H4-f**: 세 model의 MSC ranking과 MSAR ranking이 동일한지 검증한다.
- **H4-g**: 더 높은 MSC/MSAR가 더 낮은 CPU/RSS resource cost를 반드시 의미하는지 검증한다.

모두 결과-neutral wording을 유지한다(특정 model이 이긴다고 가정하는 표현 없음).

## 14. Experiment A / B 개요 (실행은 Unit 5+ 이후)

- **Experiment A(Closed, concurrent SSE streams)**: closed-model concurrent connections, k6
  `01-concurrent-connections.js` 후보 재사용. Primary metric: MSC(보조: MRC, First SLO Failure,
  First Hard Failure, Breaking Signature).
- **Experiment B(Open, constant-arrival-rate)**: open-model constant-arrival-rate, k6
  `03-constant-arrival-rate-continuous.js` 후보 재사용. Primary metric: MSAR.
- Warm-up 원칙: Closed는 JVM/network cold-start 제거를 위한 작은 pre-warm만 허용, 실제 wave는
  한 번에 N개 시작(single-wave). Open은 warm-up → measurement의 continuous lifecycle(Phase 2/3
  패턴 계승). **정확한 duration은 Unit 4/7 protocol에서, screening 전에 freeze한다** — 이번
  Unit에서 숫자를 정하지 않는다.
- k6 스크립트 재사용 시 admission 관련 파라미터/태그가 남아있다면(Phase 2/3는 admission이 있었음)
  Unit 2에서 제거 필요 여부를 확인한다 — 이번 Unit에서는 코드를 읽기만 했고 수정하지 않았다.

## 15. Common Normal SSE Workload — freeze (Unit 0에서 재확인)

```
firstChunkDelayMs = 1000
chunkIntervalMs   = 200
chunkCount        = 35
chunkSizeBytes    = 64
```

Mock LLM은 unlimited processing/waiting(`MOCK_LLM_MAX_CONCURRENT_PROCESSING=0`,
`MOCK_LLM_MAX_WAITING=0`, 기본값 그대로 — Unit 0 §7에서 소스 레벨로 확인). 이 workload는 Phase 4
Primary 전체에서 변경 금지. Control calibration(Unit 3)도 가능하면 동일 workload를 사용한다.

## 16. Unit Roadmap (계승, 변경 없음)

Unit 0(완료) → **Unit 1(이번 Unit)** → Unit 2(M1/M2/M3 구현) → Unit 3(Load Generator/Direct
Mock/OS calibration) → Unit 4(Closed harness verification) → Unit 5(Closed screening) → Unit
5.5(Closed Formal Protocol Freeze+Canary) → Unit 6(Closed Formal 27 runs) → Unit 7(Open
screening) → Unit 7.5(Open Formal Protocol Freeze+Canary) → Unit 8(Open Formal 27 runs) → Unit
9(Formal Result Integrity Review) → Unit 10(Final Report/Portfolio/README/Freeze).

각 Unit 완료 후 반드시 멈춘다. 다음 Unit 자동 시작 금지.
