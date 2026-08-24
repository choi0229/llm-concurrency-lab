# ADR: Phase 3 Admission Control, P3-A Blocking Outbound Executor, WebClient Connection Pool

Status: Accepted (Phase 3 Unit 1 scope)
Date: 2026-08-22

## 1. Admission Control Contract

세 구현체(P3-A/B/C) 모두 application-level admission ceiling을 **동일한 값**으로 둔다.

### 1-1. 후보 기본값과 근거

**50** — Phase 2 `ChatExecutorConfig`의 P-E(`core=10, max=50, SynchronousQueue, AbortPolicy`)와
`VirtualLimitedTaskSubmitter`의 `Semaphore(50)`이 이미 이 값으로 실측 벤치마크를 완료했다
(`docs/portfolio/phase2-unit6-formal-benchmark`, 메모리 기록: VT-Limited가 P-E 대비 platform
thread -30~52%, CPU +18~27%). Phase 3에서 이 값을 재사용하면 Phase 2 결과와의 비교축이 유지된다.
단, 정확한 Formal load(VUS, arrival rate)는 아직 확정하지 않았으므로(§Formal/Secondary Scope,
phase3-design.md §6) 50은 "현재까지의 가장 근거 있는 후보"이지 최종 확정치가 아니다 — Unit 5
functional screening 전에 재확인한다.

### 1-2. 의미론(freeze)

- **tryAcquire 계열, 즉시 성공 또는 즉시 reject** — `Semaphore.tryAcquire()`(non-blocking 오버로드,
  timeout 인자 없음) 또는 이와 동등한 non-blocking 카운터.
- **금지**: `Semaphore.acquire()`(blocking), blocking wait, reactive event-loop wait, hidden
  application queue(=admission 실패를 "잠깐 기다렸다 재시도"로 감추는 어떤 구조도 금지).
- **Admission reject HTTP status/response format**: 기존 Phase 2 contract를 재사용한다 —
  `503 Service Unavailable`, body `{"status":"REJECTED","reason":"executor_saturated"}`
  (`ChatController.java:174-175`, phase3-design.md §3-1). P3-C(WebFlux)에서도 동일 status/body를
  applications-level에서 직접 써서 반환한다(Servlet이 없으므로 `ServerResponse`로 구현).

### 1-3. Permit 반환 — 정확히 한 번, terminal path마다

**Unit 6 amendment(`docs/decisions/phase3-admission-semantics-unification.md`)**: 아래 6개
outcome 각각에서 "정확히 한 번" 반환한다는 원칙 자체는 유지되지만, **normal completion의 정확한
반환 시점**은 최초 freeze(Unit 1) 당시 암묵적으로 "request/response lifecycle 종료 시점"으로
읽혔던 것을 Unit 6에서 "upstream 호출 lifetime 종료 시점"으로 명확히 정정했다 — P3-A/B에서
Servlet write channel이 아직 client로 draining 중이어도 Mock LLM upstream이 자연 종료됐다면 그
시점에 permit을 반환한다(response 자체의 terminal outcome/`gateway_active_streams` 종료는 별개로
계속 추적됨). 나머지 5개 outcome(upstream error/absolute deadline/client disconnect/write
overflow/internal error)은 애초부터 upstream 연결/구독을 그 시점에 이미 정리하므로 이 정정의
영향을 받지 않는다 — 유일한 변경 대상은 normal completion이다.

Permit은 다음 terminal path에서 **정확히 한 번** 반환한다(Phase 1/2
`docs/decisions/request-outcome-accounting.md`의 exactly-once CAS 패턴 승계):

- normal completion (Unit 6부터: upstream 자연 완료 시점 — response drain 완료를 기다리지 않음)
- upstream error
- absolute deadline
- client disconnect
- write overflow
- internal error

**중복 release 금지.** Phase 2 `VirtualLimitedTaskSubmitter`가 이미 이 문제의 한 사례를 문서화했다
— submission 자체가 실패하면(permit 획득 후 `chatExecutor.execute()`가 예외를 던지는 경우)
`VirtualTaskInstrumentedRunnable`의 `finally`가 절대 실행되지 않으므로, `trySubmit()`의 catch
블록이 그 자리에서 직접 `semaphore.release()`해야 한다(`VirtualLimitedTaskSubmitter.java:55-65`).
Phase 3의 admission 구현도 "정상 실행 경로의 finally"와 "submission 자체 실패 경로"를 모두
release 지점으로 취급해야 한다.

**Atomic terminal state / idempotent cleanup**: 요청당 하나의 `AtomicBoolean`(또는 동등한 CAS)
기반 release-once 가드를 두고(Phase 1/2 `AsyncRequestState.releaseOnce()` 패턴, P3-C는 reactive
`doFinally(SignalType)` 안에서 동일 가드 사용), 이 가드를 통과한 호출만 permit을 반환하고 terminal
outcome metric을 기록한다. cleanup 자체(permit release, AsyncContext complete, WebClient
subscription dispose 등)는 여러 코드 경로에서 몇 번을 시도해도 안전하도록 idempotent해야 한다.

## 2. P3-A Blocking Outbound Executor

### 2-1. 목적

`HttpURLConnection`은 blocking I/O이므로 P3-A는 이를 실행할 별도 Platform Thread executor가
필요하다 — 이 executor는 §write-path ADR의 **공통 Servlet write executor와 완전히 다른
resource**다(하나는 outbound HTTP 호출을 blocking하고, 다른 하나는 client로의 write만 담당).

이 executor의 존재 자체가 Experiment A(phase3-design.md §1)의 측정 대상이다 — P3-B에서 이
executor를 제거했을 때(WebClient는 이 스레드가 필요 없음) 비용이 어떻게 바뀌는지를 직접 비교한다.

### 2-2. 설계(기본 후보)

Admission ceiling(§1, 후보 50)과 동일한 최대 blocking worker 수를 기본 설계 후보로 둔다. 추가
hidden queue는 만들지 않는 방향을 우선한다:

```
core = max = admission ceiling (후보 50)
SynchronousQueue
AbortPolicy
```

Phase 2 P-E(`ChatExecutorConfig.platformExecutor()`)와 같은 "thread-first / no wait queue"
의미론이다. 다만 P-E는 `core=10, max=50`으로 core/max를 분리했는데, P3-A는 **core=max**로
둔다(§2-3에서 이유를 확정) — admission 자체가 이미 동시 요청 수를 ceiling으로 제한하므로, admitted
된 요청은 outbound 호출을 즉시 실행할 수 있어야 하고 core pool 워밍업으로 인한 지연을 만들 이유가
없다.

### 2-3. `java.util.concurrent.ThreadPoolExecutor` semantics 재확인 (Java 8, 변경 없음)

Java 8과 21 사이에 `ThreadPoolExecutor`/`SynchronousQueue`/`AbortPolicy`의 핵심 동작은 변경되지
않았다(JDK 공식 API — `java.util.concurrent` 패키지는 이 세 클래스 모두 Java 5부터 동일 계약을
유지). Phase 1(Java 8)과 Phase 2(Java 21) 양쪽에서 이미 동일 클래스로 실측 검증된 이력이 있으므로
Unit 1에서 별도 스크래치 재현 없이 다음을 그대로 승계한다:

- `corePoolSize == maximumPoolSize`이고 `SynchronousQueue`를 큐로 쓰면, task는 **큐에 절대
  쌓이지 않는다** — 매 `execute()` 호출은 (a) 대기 중인 idle worker가 즉시 handoff를 받거나,
  (b) `poolSize < maximumPoolSize`(이 경우 없음, core=max이므로 항상 거짓)면 새 스레드를 만들거나,
  (c) 둘 다 불가능하면 즉시 `RejectedExecutionException`을 던진다(`AbortPolicy`).
- 즉 P-E의 기존 Javadoc 주석(`GatewayMetrics.java:16-22`)이 이미 지적한 대로, **queue wait time은
  구조적으로 항상 0에 가깝다** — `SERVLET_WRITE_QUEUE_CAPACITY`류의 "큐에 쌓인 시간"을 재는
  metric은 P3-A의 blocking outbound executor에는 의미가 없고(§metrics-contract ADR §23), 대신
  admission 자체의 reject 여부만 관찰 대상이다.
- **admission ceiling(§1)과 이 executor의 max 크기가 동일**하므로, admission을 통과한 요청은
  이 executor에서 `RejectedExecutionException`을 받을 수 없다 — 이론상 이 executor의
  `AbortPolicy`가 실제로 발동하는 경우는 "admission ceiling과 executor max가 설정 실수로
  어긋났을 때"뿐이며, 이는 곧 시작 시점 invariant 검증 대상이다(두 값이 항상 같은 env var 또는
  같은 source에서 파생되도록 구현에서 강제한다 — Unit 2에서 반영).

## 3. WebClient `ConnectionProvider` Contract (P3-B/P3-C 공통)

### 3-1. 목표

application admission(§1)이 **primary limiter**여야 한다 — connection pool이 그보다 더 작은
hidden capacity limiter가 되면 안 된다. Formal 정상 상태에서 connection pool의 pending queue는
0에 가까운 diagnostic 값이어야 한다(§metrics-contract ADR §26 invariant).

### 3-2. 실측 확인 — Reactor Netty 1.0.39 `ConnectionProvider.ConnectionPoolSpec` 실제 API

추측하지 않고 실제 resolve된 `reactor-netty-core-1.0.39.jar`를 `javap`으로 역검증했다
(`docs/test-results/phase3/unit1-capability-spikes/connectionprovider-api-javap.txt`). 확인된
빌더 메서드:

```
maxConnections(int)
pendingAcquireMaxCount(int)
pendingAcquireTimeout(Duration)
maxIdleTime(Duration)
maxLifeTime(Duration)
metrics(boolean)
```

공식 Javadoc(projectreactor.io/docs/netty/1.0.39) 확인 결과:

- `maxConnections`: 기본값 `ConnectionProvider.DEFAULT_POOL_MAX_CONNECTIONS`.
- `pendingAcquireMaxCount`: 기본값 **`2 * maxConnections`**. `-1`은 무제한. 그 외 값은 pending
  큐의 상한.
- `pendingAcquireTimeout`: 기본값 `ConnectionProvider.DEFAULT_POOL_ACQUIRE_TIMEOUT`(pending
  상태로 대기할 수 있는 최대 시간).
- `maxIdleTime`/`maxLifeTime`: 미지정 시 무제한(각각 idle/lifetime 상한 없음).

### 3-3. 핵심 실측 결과 — `pendingAcquireMaxCount(0)`은 지원되지 않는다

지시문 §15가 요구한 대로 **추측하지 않고 직접 실행**해 확인했다
(`docs/test-results/phase3/unit1-capability-spikes/connectionprovider-pendingAcquireMaxCount-0-result.txt`):

```java
ConnectionProvider.builder("spike-pool")
    .maxConnections(1)
    .pendingAcquireMaxCount(0)   // <- 0
    .pendingAcquireTimeout(Duration.ofMillis(300))
    .build();
```

```
Exception in thread "main" java.lang.IllegalArgumentException:
    Pending acquire max count must be strictly positive
    at reactor.netty.resources.ConnectionProvider$ConnectionPoolSpec.pendingAcquireMaxCount(ConnectionProvider.java:598)
```

**결론: `pendingAcquireMaxCount=0`으로 "pending 큐 자체를 없애는 진짜 fail-fast"는 Reactor Netty
1.0.39에서 지원되지 않는다** — 허용값은 양의 정수(고정 상한) 또는 `-1`(무제한)뿐이다. 이는 Unit 0
ADR이 미결 위험으로 남긴 질문에 대한 확정 답이다.

### 3-4. `pendingAcquireMaxCount`가 양수일 때의 실제 동작 — 실측 확인

```java
ConnectionProvider.builder("spike-pool")
    .maxConnections(1)
    .pendingAcquireMaxCount(1)
    .pendingAcquireTimeout(Duration.ofMillis(300))
    .build();
```

sole connection을 3초간 점유하는 요청 1개 + 100ms 뒤 도착한 요청 2개를 실행한 결과
(`docs/test-results/phase3/unit1-capability-spikes/connectionprovider-pendingAcquireMaxCount-1-timeout-result.txt`):

```
request2 (fired ~100ms later while pool full): ERROR:
    reactor.netty.internal.shaded.reactor.pool.PoolAcquireTimeoutException:
    Pool#acquire(Duration) has been pending for more than the configured timeout of 300ms
request2 time-to-error (ms, measured from its own start): 313
```

`pendingAcquireTimeout`이 실제로 pending 대기를 그 시간만큼으로 정확히 bound한다는 것을 확인했다
(설정 300ms, 실측 313ms — 스케줄링 오버헤드 수준의 오차). 예외 타입은
`reactor.netty.internal.shaded.reactor.pool.PoolAcquireTimeoutException`이다 — 이 클래스명을
P3-B/C의 upstream-failure 분류 코드(및 metrics-contract의 `reason` 라벨)에서 그대로 사용한다.

### 3-5. Phase 3 정책(freeze)

진짜 "pending queue 0"은 API가 지원하지 않으므로, 대신 **구조적으로 pending이 발생하지 않게
설계**해서 §3-1의 목표를 달성한다:

```
maxConnections >= admission ceiling (§1, 후보 50)
pendingAcquireMaxCount = 작은 양의 정수 (예: 1 — "혹시 발생하면 즉시 드러나야 하는" 안전망)
pendingAcquireTimeout = 짧은 값 (예: 수백 ms — admission이 정상 동작한다면 도달할 일이 없어야 함)
maxIdleTime = 미지정 또는 근거 확보 후 지정 (Unit 5 이전 확정)
maxLifeTime = 미지정 (Mock LLM/Formal 시나리오 특성상 connection recycling을 강제할 이유가 아직
              없음 — 근거 없이 값을 넣지 않는다)
```

`maxConnections >= admission ceiling`이 성립하는 한, application admission을 통과한 요청 수가
이미 `maxConnections` 이하이므로 pool은 구조적으로 절대 가득 차지 않고 pending은 발생하지 않는다.
`pendingAcquireMaxCount`/`pendingAcquireTimeout`은 "정상 동작 시 절대 발동하지 않아야 하는
안전망"이며, 만약 Formal 중 이 경로가 실제로 발동한다면 그 자체가 admission ceiling과
`maxConnections` 설정이 어긋났다는 버그 신호로 취급한다(§metrics-contract ADR invariant).

정확한 `maxConnections` 숫자(50 이상의 얼마)와 `pendingAcquireTimeout` 값은 Unit 5 functional
screening 전에 확정하고 Formal 시작 후 변경하지 않는다(§write-path ADR §5-2와 동일 원칙).

## 4. Hidden Queue 방지 — 공통 점검 리스트

Phase 3 P3-A/B/C 어디에도 아래 형태의 "숨은 대기열"이 없어야 한다(Unit 2 구현 리뷰 체크리스트):

- Semaphore/락의 blocking `acquire()`
- 크기 제한 없는 `LinkedBlockingQueue` 등 unbounded queue
- reactive 체인 안에서 `.delayElements()`/`.retryWhen()` 등으로 admission 실패를 감추는 재시도
- WebClient `ConnectionProvider`의 `pendingAcquireMaxCount`를 크게(사실상 무제한처럼) 잡아
  admission ceiling보다 더 많은 요청이 connection을 놓고 대기하게 두는 구성
