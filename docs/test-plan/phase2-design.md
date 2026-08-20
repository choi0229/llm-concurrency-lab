# Phase 2 — 최종 설계 (구현 착수 전, Rev. 2)

Status: **설계 문서 — 코드 미작성**. Rev. 1(2026-08-16 오전)에서 사용자 검토로 지적된 10가지 문제
(전역 virtual thread 플래그, primary/secondary 비교 원칙, Gateway-bound/Downstream-bound 분리,
carrier thread 측정 불가능성, JFR/pinning 서술, scheduler metadata, native memory 정책,
histogram/cross-phase 정책, PrintWriter 서술 과잉확정)를 전부 반영한 **최종 개정판**이다. Phase 1은
freeze됐고(`docs/test-results/phase1/phase1-final-report.md`), 이 문서는 그 결론에서 출발한다.
Date: 2026-08-16

## 0. 핵심 질문 (Phase 1에서 이어짐)

Phase 1이 확인한 것: 7.8초 blocking SSE workload에서 platform thread 10개 → capacity≈1.28req/s,
50개 → capacity≈6.41req/s (실측이 이론값과 ±4% 이내로 일치). 처리량은 platform thread 수를 늘리면
증가하지만, 그 대가로 platform thread 수·RSS·native resource가 함께 증가한다.

**Phase 2 질문**: "Blocking Programming Model과 Blocking HTTP I/O를 유지하면서 Platform Thread
대신 Virtual Thread를 쓰면, 동일한 I/O concurrency를 훨씬 적은 platform-thread 비용으로 유지할 수
있는가?"

---

## 1. 비교 원칙 — 변경 변수 고정

Java 8(Phase 1)과 Java 21 Virtual Thread를 직접 비교하지 않는다 — JDK 세대 차이가 결과에 섞이기
때문이다. **같은 Java 21 환경에서**, 다음 lifecycle을 유지한 채 **Chat Executor 하나만** 바꾼다:

```
Front → Spring MVC → AsyncContext → Chat Executor → HttpURLConnection → Mock LLM → SSE relay
                                     ^^^^^^^^^^^^^
                                     여기만 바뀐다
```

- Platform: `ThreadPoolExecutor`
- Virtual: `Executors.newVirtualThreadPerTaskExecutor()`

**Spring Boot의 전역 `spring.threads.virtual.enabled=true`는 Primary/Secondary 어느 비교에서도
사용하지 않는다.** 이 설정은 Tomcat 자신의 요청 처리 스레드, `SimpleAsyncTaskExecutor`, 메시징
리스너 등 chat executor가 아닌 다른 실행 영역까지 한꺼번에 virtual thread로 바꿔버려 "chat executor의
thread model만 바꾼다"는 비교 원칙 자체를 깨뜨린다. 따라서:

- Tomcat 자신의 요청 처리 스레드 풀은 **Platform/Virtual 두 구성 모두 기본값(platform thread)으로
  고정**한다. Chat executor의 코드에서 **직접** `Executors.newVirtualThreadPerTaskExecutor()`를
  생성해 쓰거나, `ThreadPoolTaskExecutor`(Platform)를 쓰는 두 가지 Bean 정의만 다르게 두고, 전역
  프로퍼티는 애플리케이션 설정 어디에도 켜지 않는다.

---

## 2. Primary / Secondary 비교 구조

### 2-1. Primary — P-E vs VT-Limited

| | P-E (Platform) | VT-Limited (Virtual) |
|---|---|---|
| JDK/Spring/HTTP Client/Mock LLM/workload/timeout/Docker CPU·Mem/k6/Prometheus/metric 이름 | 동일 | 동일 |
| Chat Executor | `ThreadPoolExecutor`(core=10, max=50, `SynchronousQueue`, `AbortPolicy`) — Phase 1 Config E 그대로 포팅 | `Executors.newVirtualThreadPerTaskExecutor()` + `Semaphore(50)` 기반 admission gate |
| Chat task / admission concurrency ceiling | 50(스레드 풀 max) | 50(semaphore permit 수) — **동일하게 맞춤** |
| Overload 시 admission semantics | 즉시(수 ms) `RejectedExecutionException` → 503, queue 없음(`SynchronousQueue`) | 즉시(수 ms) `semaphore.tryAcquire()` 실패 → 503, queue/wait 없음 |

**핵심 질문**: "동일한 chat task / admission concurrency ceiling에서 platform thread와 virtual thread의
thread/RSS/Heap/latency/throughput 비용은 얼마나 다른가?"

**VT-Limited의 admission 구조**(P-E의 `ThreadPoolExecutor.execute()`가 동기적으로 accept/reject를
결정하는 것과 구조적으로 대응시킨다):

1. `ChatController.stream()`(Tomcat 요청 스레드)에서 **virtual thread를 만들기 전에** 먼저
   `semaphore.tryAcquire()`(non-blocking, 대기 없음)를 호출한다.
2. 실패하면 → `outcome=rejected`로 즉시 기록, 503 응답(Phase 1의 `RejectedExecutionException` catch
   경로와 동일한 코드 모양) — **이 경우 virtual thread를 아예 생성하지 않는다.**
3. 성공하면 → 그제서야 `Executors.newVirtualThreadPerTaskExecutor()`에 task를 제출한다. 이 permit은
   **그 task의 전체 생명주기 동안**(upstream 호출 + SSE relay 전부) 점유되고, task 종료(성공/실패
   불문, `finally`)에 `release()`된다.

permit을 "upstream 호출 구간"이 아니라 **task 전체 생명주기**에 걸어두는 이유: P-E에서 "platform
thread 50개가 다 찼다"는 것은 정확히 "50개 요청이 admission부터 relay 종료까지 그 스레드를 붙잡고
있다"는 뜻이다 — VT-Limited의 permit 점유 범위를 여기 맞추지 않으면 "동일한 chat task / admission
concurrency ceiling"이라는 전제 자체가 깨진다.

`java.util.concurrent.Semaphore`는 `AbstractQueuedSynchronizer` 기반(park/unpark)이며
`synchronized`를 쓰지 않으므로 virtual thread를 pin하지 않는다고 알려져 있다 — 이 전제도 §6의 JFR
검증 대상에 포함한다(가정으로 남기지 않는다).

**Primary VT-Limited는 `Semaphore.tryAcquire()`(non-blocking) 하나만 쓴다.** `Semaphore.acquire()`
(blocking 대기), 대기 queue, permit 획득까지 걸린 시간을 재는 metric(예: `executor_semaphore_
wait_seconds`), permit 부족 시 대기시간이 늘어나는지를 보는 실험은 Primary/Secondary 어디에도 두지
않는다 — blocking-wait 모델은 `SynchronousQueue`+`AbortPolicy`인 P-E의 admission semantics(대기
없이 즉시 accept/reject)와 달라져 §2-1의 "direct comparison" 전제 자체를 깨뜨리기 때문이다. 이런
"기다리는 VT" 모델이 필요해지면 **Phase 2 범위 밖의 별도 Future Work**("VT-Waiting" 실험 후보)로만
기록하고, 이번 Primary/Secondary 설계에는 구현하지 않는다.

### 2-2. Secondary — VT-Unlimited

요청마다 virtual thread를 생성하되 **admission gate(Semaphore) 없이** 곧바로 upstream을 호출한다.
**P-E와 "thread model 하나만 바뀐 직접 비교"라고 표현하지 않는다** — admission semantics 자체가
다르기 때문이다(P-E/VT-Limited는 50에서 즉시 reject, VT-Unlimited는 admission 제한이 없음). 목적은
§3(Gateway-bound/Downstream-bound)에서 별도로 규정한다.

---

## 3. Gateway-bound / Downstream-bound 실험 분리

Primary/Secondary가 "무엇을 비교하는가"라면, 이 축은 "어떤 환경 조건에서 비교하는가"다.

### Experiment A — Gateway-bound

Mock LLM capacity를 Phase 1과 동일하게 충분히 크게 설정한다(`MOCK_LLM_MAX_CONCURRENT_
PROCESSING=0`, 무제한 — Mock LLM이 병목이 되지 않도록 고정하는 Phase 1 원칙 유지).

**대상**: P-E vs VT-Limited(§2-1 Primary), 참고로 VT-Unlimited도 같은 조건에서 실행해 "admission
제한이 없을 때 Gateway 자체가 어디까지 버티는가"의 상한을 관찰한다(단, 이 관찰은 참고용이며 P-E와의
"직접 비교"로 쓰지 않음 — §2-2).

**목적**: Platform vs Virtual의 **Gateway 자체** scalability(thread/RSS/Heap/latency/throughput) 비교.

### Experiment B — Downstream-bound

Mock LLM concurrency를 명시적으로 제한한다(예: `MOCK_LLM_MAX_CONCURRENT_PROCESSING=20` 또는 `50` —
Phase 1에서 이미 구현된 기존 knob 재사용, 신규 구현 불필요).

**대상**: VT-Unlimited vs VT-Limited(§2-1의 동일 구현체 재사용, 새 variant 만들지 않음).

**측정**: `mock_active`, `mock_waiting`(Mock LLM 자체 admission 지표, 이미 존재), completion
throughput, TTFC, timeout, reject.

**핵심 질문**: "Gateway가 더 많은 virtual thread를 유지할 수 있다는 것이 전체 시스템 completion
throughput 증가를 의미하는가?" — VT-Unlimited가 downstream 제한을 무시하고 계속 upstream을 호출하면
그 부하가 Mock LLM 자체의 큐(`mock_waiting`)로 그대로 옮겨갈 뿐인지, VT-Limited(Gateway 쪽에서
이미 50으로 자체 제한)가 오히려 더 나은/동등한 completion throughput을 내는지를 실측으로 확인한다.

**선택 확장**: 시간이 허락하면 P-E도 Experiment B에 포함해 "Platform의 50-cap이 downstream 제한과
어떤 관계를 갖는가"를 참고로 볼 수 있다 — 필수는 아니다.

---

## 4. Spring MVC / Servlet Async 처리 방식

Phase 1과 동일하게 Servlet `AsyncContext`(`request.startAsync()` + `asyncContext.
setTimeout(CHAT_TOTAL_TIMEOUT_MS)`)를 유지한다 — Reactive(WebFlux)로 전환하지 않는다(§0의 "Blocking
Programming Model 유지"가 전제). Tomcat 자신의 커넥터/요청 스레드는 §1에서 명시한 대로 모든 구성에서
platform thread로 고정한다.

---

## 5. Blocking HTTP Client

**결정: `HttpURLConnection`을 그대로 사용한다**(Phase 1 `MockLlmClient`를 최소 변경 포팅) —
§1의 원칙(변수는 Chat Executor 하나만)을 지키기 위함이며, HTTP client 교체는 그 자체로 두 번째
변수가 된다.

이것이 "pinning이 없다"는 가정은 아니다 — `HttpURLConnection` 내부 구현이 JDK 버전에 따라
`synchronized`를 쓸 수 있고, virtual thread가 그 안에서 블로킹하면 carrier를 pin할 수 있다는 것이
JDK 21의 알려진 제약이다. **가정하지 않고 §6의 JFR로 실측한다.** `java.net.http.HttpClient`로의
교체는 주 비교선에서 하지 않으며, 필요해지면 명시적으로 분리된 별도 실험으로만 다룬다.

---

## 6. JFR / Pinning 진단 설계

**Phase 2 primary runtime은 Java 21로 고정한다.** JDK 21에서는 virtual thread가 `synchronized`
블록/메서드 안에서 블로킹하거나 native 프레임을 거치는 구간에서 carrier를 계속 붙잡는(pin) 현상이
발생할 수 있다. **[JEP 491: Synchronize Virtual Threads without Pinning](https://openjdk.org/jeps/491)**
이 JDK 24에 이 `synchronized` pinning 문제 자체를 해소했다(monitor를 carrier가 아닌 virtual thread에
결속시켜, virtual thread가 synchronized 블록 안에서 블로킹해도 carrier를 놓아줄 수 있게 함) — Spring
공식 문서도 이 변경을 근거로 pinning 처리 개선을 위해 JDK 24 이상을 권장한다(`docs/decisions/
version-compatibility.md` §6). **따라서 Phase 2가 JDK 21에서 관측하는 pinning 양상은 JDK 24+로
일반화하지 않는다** — 이는 이 설계의 명시적 스코프 제한이다. exact JDK 21 patch(조사 시점 후보:
Temurin 21.0.12, 구현 착수 시 재확인)를 모든 formal/diagnostic run의 `environment.json`에 기록한다.

**Pinning은 가설이다.** 사전 코드 검토로 아래를 **candidate로만** 나열한다 — 어느 것도 실제 원인이라고
미리 확정하지 않는다:

- `HttpServletResponse.getWriter()`(`PrintWriter`)를 통한 SSE relay 쓰기 경로 — **사전 코드 검토에서
  확인한 pinning candidate 중 하나**(다른 candidate와 동등한 지위, "가장 유력하다"고 서술하지 않는다).
- `HttpURLConnection` 내부(connect/getInputStream) — JDK 21 patch별 실제 구현 확인 필요.
- Gateway 자체 코드의 `synchronized` 사용처 — Phase 1 코드(`AsyncRequestState`는 `AtomicBoolean`
  CAS 기반)를 그대로 포팅하는 한 새로 추가되지 않아야 하며, §2-1의 Semaphore/§8의 신규 metric 코드도
  `synchronized` 키워드를 쓰지 않는 것을 설계 원칙으로 못박는다.
- Logging appender, 기타 native/synchronized 경로.

**실제 원인은 JFR `jdk.VirtualThreadPinned` 이벤트의 stack trace로만 판단한다** — 코드 리뷰는 후보를
좁히는 용도이지 결론을 내리는 용도가 아니다.

**JFR 이벤트 수집 대상**: `jdk.VirtualThreadPinned`, `jdk.VirtualThreadStart`, `jdk.VirtualThreadEnd`,
`jdk.VirtualThreadSubmitFailed`.

**정책**(Phase 1의 `monitoring-baseline.md` §3과 동일 원칙 승계): **Formal benchmark run에서는 JFR
OFF.** 계측 오버헤드를 비교 결과에 섞지 않기 위함이다. VT-Unlimited/VT-Limited 각각에 대해 별도의
**diagnostic pass**(Phase 1 calibration run과 같은 성격 — 짧은 warm-up+measurement)를 JFR ON으로
수행해 이벤트 발생 여부·빈도·지속시간·stack trace를 수집하고, §5/§6 candidate 목록과 대조해 원인을
판단한다. 이 결과는 formal 비교 결과와 분리된 별도 섹션으로 문서화한다.

---

## 7. Virtual Thread Scheduler Metadata

모든 VT run(formal + diagnostic)에서 다음을 `environment.json`에 기록한다:

- `Runtime.availableProcessors()`
- `jdk.virtualThreadScheduler.parallelism`
- `jdk.virtualThreadScheduler.maxPoolSize`
- Docker CPU limit(`cpus:` 값)
- JDK full version(`java -version` 실측)

scheduler property를 **명시적으로 설정하지 않은 경우에도** "default/not explicitly configured"라고
명확히 기록한다(빈 값으로 남기지 않는다 — Unit 6.7에서 배운 "존재하지 않는 population을 암묵적으로
0으로 표시하지 않는다"는 원칙과 같은 정신).

**Formal baseline에서는 scheduler parallelism/maxPoolSize를 성능이 좋아 보이도록 임의 튜닝하지
않는다** — `cpus:1.0` 컨테이너 제한 하에서 JDK가 resolve하는 기본값을 그대로 쓰고 실측 기록한다.
튜닝이 필요하다고 판단되면 그 튜닝 자체를 별도의, 명시적으로 분리된 diagnostic 실험으로 수행한다.

---

## 8. Virtual Thread / Platform Thread Metric 정의

### 8-1. Platform Thread(양쪽 구성 공통, formal metric)

JDK 21에는 애플리케이션이 현재 carrier thread 수를 정확히 가져오는 단순 public API가 없다 —
**carrier thread count는 formal metric에 포함하지 않고 diagnostic 전용으로만 다룬다**(§6 diagnostic
pass에서 thread dump 기반으로 필요시 참고, formal metric 이름도 formal 결과표 컬럼도 만들지 않는다).

**Formal metric**(`ThreadMXBean` 기반, P-E/VT-Limited/VT-Unlimited 전부 동일 방식으로 수집 — 이
두 값만이 formal authoritative platform thread metric이다):

- **JVM live platform thread count** — `ThreadMXBean.getThreadCount()`(JVM 전체의 현재 platform
  thread 수 — Tomcat 자신의 스레드, GC/JIT 스레드, chat executor 스레드/carrier를 모두 포함한 전체
  footprint). 이 총량 자체가 Phase 2의 핵심 질문("동일 I/O concurrency를 platform thread 몇 개로
  유지하는가")에 정확히 대응하는 값이라 의도적으로 전체를 잰다.
- **JVM peak platform thread count** — `ThreadMXBean.getPeakThreadCount()`. 각 measurement
  window 시작 시 `resetPeakThreadCount()`를 호출해 그 window 안의 peak만 잡는다(Prometheus 5s
  scrape 샘플링에 의존하는 Phase 1의 `max_over_time` 방식보다 정밀 — JMX가 내부적으로 정확한 peak을
  추적하므로 짧은 스파이크를 놓치지 않는다).

### 8-2. Virtual Thread(application-level metric, VT 구성 전용)

한 task = 한 virtual thread라는 구조를 metric 이름/설명에 명시한다:

- `chat_virtual_tasks_active`(Gauge) — 현재 in-flight 요청 처리 task(=virtual thread) 수.
- `chat_virtual_tasks_started_total`(Counter)
- `chat_virtual_tasks_completed_total`(Counter, 모든 terminal outcome 포함)

Virtual thread **자체**의 생성/종료 수를 애플리케이션 카운터와 별개로 검증하려면 `jdk.
VirtualThreadStart`/`jdk.VirtualThreadEnd`(§6 diagnostic pass)로 교차 확인한다 — formal metric으로
쓰지 않는다.

### 8-3. VT 구성에서 관측되지 않는 Phase 1 metric

`executor_queue_wait_seconds`는 VT 구성에는 queue 개념이 없어 관측되지 않는다 — "0"이 아니라 "not
applicable/not emitted"로 명시 문서화한다(Unit 6.7 교훈 재적용). VT-Limited의 admission
reject/accept는 새 metric을 만들지 않고 **Phase 1의 `gateway_request_outcome_total{outcome=
"rejected"}`를 그대로 재사용**한다(§2-1의 `tryAcquire()` 실패 경로가 Platform의
`RejectedExecutionException` 경로와 동일한 outcome 값을 기록하도록 구현) — 이는 §9(metric 이름
동일 유지)와 자연스럽게 맞아떨어진다.

---

## 9. Native Memory 측정

Formal benchmark에서는 Phase 1과 동일하게 **JFR/NMT를 기본 OFF**로 유지한다. 주 비교 지표는:

- Process RSS
- Heap
- GC(빈도/시간, 기존 JVM export)
- JVM total live platform thread count(§8-1)

**Native Memory 상세 분석(Thread 카테고리 등)이 필요할 경우에만** `-XX:NativeMemoryTracking=summary`
+ `jcmd VM.native_memory`를 켠 **별도 diagnostic run**을 수행한다. **NMT ON 결과와 NMT OFF formal
latency 결과를 같은 population의 수치처럼 직접 비교하지 않는다** — 계측 오버헤드가 섞인 값과 섞이지
않은 값을 나란히 놓고 "차이"로 해석하지 않는다는 뜻이다(Phase 1 `monitoring-baseline.md` §3 원칙의
직접 연장).

---

## 10. Histogram Schema / Cross-Phase Metric

Phase 2는 **Day 1부터** Unit 6.7이 freeze한 최종 `FINE_LATENCY_BUCKETS`(`docs/decisions/
monitoring-baseline.md` §6)를 P-E/VT-Limited/VT-Unlimited **모든 Java 21 구현체에 동일하게**
적용한다 — Phase 1처럼 중간에 재보정할 필요가 없다. 이로써 **Phase 2 내부**(§2의 Primary/Secondary
비교)는 server-side histogram으로도 동일 정밀도로 비교 가능하다.

**Phase 1 vs Phase 2**(cross-phase) 비교의 authoritative metric은 여전히 **k6 client-side
Trend**(`client_ttfc_completed_seconds`, `client_stream_duration_seconds`)만 사용한다 — bucket
schema가 Phase 1과 다르기 때문이다. 이 cross-phase 비교는 JDK/Spring 세대 차이라는 confound가 있어
**참고용(secondary)**으로만 취급하고, Phase 2의 1차 결론은 항상 §2의 Phase 2 내부 비교에서 나온다
(이미 확정된 Phase 1 Cross-Phase Metric Compatibility 정책 그대로 승계, 변경 없음).

---

## 11. 테스트 Load 선정 방법

Phase 1의 R1/R2/R5/R12를 그대로 재사용하지 않는다 — §2-1에서 P-E와 VT-Limited의 admission
ceiling(50)을 동일하게 맞췄지만, 그 ceiling에 도달하는 데 필요한 arrival rate나 VT-Unlimited/
Downstream-bound 조건의 반응은 사전에 알 수 없기 때문이다.

**절차 — closed-model screening → open-model short pilot → formal RPS 선정** (Little's Law로
closed-model concurrency 경계를 open-model RPS로 직접 환산하지 않는다 — Phase 1의 `unit6-
benchmark-plan.md` §3이 정확히 이 환산(L=λW)으로 LOW/STRESS/OVERLOAD load를 선정했고, Unit 6.6
정정(`unit6-formal-benchmark-results.md` §3)에서 이 값이 §2의 capacity 추정과 혼동되지 않도록
표현을 정리해야 했던 경험이 있다. Phase 2는 같은 category의 혼동 여지를 원천적으로 피하기 위해
수식 환산 대신 실측 pilot으로 대체한다):

1. **Closed-model screening**(소규모, Phase 1 Unit 5와 동일 성격)을 P-E / VT-Limited /
   VT-Unlimited(Experiment A 조건) 및 VT-Unlimited/VT-Limited(Experiment B, downstream 제한
   조건)에 대해 각각 수행해 **concurrency 특성**(어느 concurrency에서 reject/thread 증가/caller
   전파 등이 시작되는지)을 파악한다.
2. `workerCount / serviceTime`(§0의 Phase 1 capacity 추정과 같은 단순 산술)은 **rough capacity
   estimate로만** 참고한다 — 이 값을 Little's Law라고 부르지 않고, closed concurrency를 open
   arrival rate로 수식으로 직접 환산하는 근거로도 쓰지 않는다.
3. **Open-model short pilot**(`02-constant-arrival-rate.js`, 짧은 warm-up+measurement)을 1의
   screening으로 얻은 대략적 범위 안에서 몇 개 RPS 후보로 실행해, **실제** arrival/completion/
   reject/timeout 곡선을 관찰한다 — 이 실측 곡선이 formal RPS 선정의 직접 근거다.
4. Pilot 결과로 최소 4개 축을 포함하는 formal RPS를 확정한다: (a) 모든 구성이 안전한 구간, (b)
   구성별 반응이 갈리기 시작하는 구간, (c) P-E/VT-Limited의 admission ceiling(50)을 명확히 넘는
   구간, (d) Experiment B에서 downstream 제한을 넘는 구간.
5. Screening/pilot 실측 수치는 구현 착수 시점에 이 문서 후속 개정으로 반영한다 — 지금은 추측하지
   않는다.

---

## 12. 예상되는 Confounding Factor

| Confound | 설명 | 대응 |
|---|---|---|
| GC 알고리즘 차이 | Java 8과 21의 기본 GC가 다름 | P-E/VT-Limited/VT-Unlimited 전부 동일 GC 플래그 명시 고정, `environment.json` 기록 |
| Carrier parallelism이 `cpus:1.0`에서 어떻게 resolve되는지 | 추측 시 VT 결과 해석이 틀릴 수 있음 | §7에서 명시적으로 실측·기록 |
| HttpURLConnection pinning | VT의 이론적 이점을 부분적으로 상쇄할 수 있음 | §6으로 실측, 가정하지 않음 |
| Semaphore 자체의 pinning 여부 | "AQS 기반이라 안전하다"는 것도 이론일 뿐 | §6 JFR 검증 대상에 명시적으로 포함 |
| VT-Unlimited를 P-E의 "직접 비교 대상"으로 오독 | admission semantics가 다름(§2-2) | 모든 보고서 서술에서 "직접 비교"라는 표현을 P-E vs VT-Limited에만 쓴다 |
| Experiment A/B 사이 Mock LLM 설정 차이를 Gateway 결과로 오인 | Mock LLM capacity가 실험마다 다름(A: 무제한, B: 20/50 제한) | 모든 결과 표에 어느 Experiment(A/B) 조건인지 항상 명시 |
| JIT/warm-up 특성 차이(JDK 8 vs 21) | Cross-phase 비교에서 곡선 모양이 다를 수 있음 | Cross-phase는 secondary(§10), warm-up 2분 유지 + pilot으로 steady-state 확인 |
| macOS Docker Desktop 시계 drift / Prometheus anonymous volume 재사용 | Phase 1에서 실제로 결과를 오염시켰던 환경 이슈, JDK 버전과 무관하게 재발 가능 | Phase 1의 preflight 가드·`--renew-anon-volumes` 그대로 재사용 |

---

## 13. Phase 2 구현 순서

1. Java 21 / Spring Boot 4.x 정확 patch 버전 확정 — `docker manifest inspect`/`./gradlew
   dependencies`/`java -version` 실측(`version-compatibility.md` §6 후보 → 실측 확정으로 갱신).
2. Gateway 코드 포팅 — `ChatController`/`AsyncRequestState`/`GatewayMetrics`/`MockLlmClient`의
   **request lifecycle, correlation/outcome classification, timestamp wrapper의 공통 부분**은
   그대로 재사용하고 `javax.servlet`→`jakarta.servlet` 네임스페이스만 전환한다. **Platform과 VT는
   executor semantics 자체가 다르므로 "로직 100% 동일 재사용"이라고 하지 않는다** — `Instrumented
   Runnable`의 queue wait/caller-runs/pool size/rejected-execution 계측은 Platform(P-E) 전용으로
   분리하고, VT는 별도로 virtual task active/start/completed(§8-2)와 admission reject(§2-1의
   `tryAcquire` 실패 경로, 기존 `outcome=rejected` 재사용)만 계측한다 — executor별 계측 코드는
   분리하되 request/outcome instrumentation은 공유한다.
3. P-E(Platform, Phase 1 Config E 포팅) 구성 + `/healthz`/invariant 정상 동작 확인.
4. VT-Limited 구성 추가: `Executors.newVirtualThreadPerTaskExecutor()` + `Semaphore(50)`
   `tryAcquire()` 기반 admission gate(§2-1, non-blocking만 — `acquire()` 사용 안 함) + §8 metric
   (`chat_virtual_tasks_*`, `ThreadMXBean` 기반 platform thread count/peak).
5. HttpURLConnection/Semaphore pinning 여부 JFR 진단 pass(§6, 비-formal) 먼저 실행 — 결과에 따라
   §5의 HTTP client 결정을 재확인.
6. VT-Unlimited 구성 추가(admission gate 없음).
7. Screening(§11)으로 각 구성 capacity 경계 발견 → R-value 확정, 이 문서 후속 개정에 반영.
8. Preflight/reliability guard 이식(clock drift, anonymous volume renewal, invariant validator) —
   Phase 1 스크립트 재사용/파라미터화.
9. Experiment A(Gateway-bound) formal run 실행 — P-E vs VT-Limited(Primary) + VT-Unlimited(참고).
10. Experiment B(Downstream-bound) formal run 실행 — VT-Unlimited vs VT-Limited, Mock LLM
    concurrency 제한 조건.
11. NMT diagnostic pass(§9, 별도 독립 실행) 수행.
12. 결과 수집/집계 스크립트 확장(Phase 1의 `collect_formal_result.py` 패턴 재사용).
13. Cross-phase 비교(§10) 정리.
14. Phase 2 최종 보고서 작성(Phase 1과 동일 포맷).

---

## 14. Phase 2 완료 기준

- Primary(P-E vs VT-Limited, Experiment A)와 Secondary(VT-Unlimited vs VT-Limited, Experiment B)
  formal run이 각각 동일 방법론(warm-up/measurement/반복, invariant 검증)으로 완료되고 전부
  accounting invariant를 통과한다.
- §8의 formal metric(JVM live/peak platform thread count, `chat_virtual_tasks_*`)이 P-E/VT-Limited/
  VT-Unlimited 전부에서 실측 문서화된다 — carrier thread 수 같은 비공식 값을 authoritative로 쓰지
  않는다.
- `jdk.VirtualThreadPinned` 발생 여부·빈도·위치가 diagnostic pass로 실측되고, §6 candidate 목록과
  대조해 원인이 판단되거나 "판단 불가"로 명시적으로 기록된다(추측을 결론으로 남기지 않는다).
- 동일 chat task / admission concurrency ceiling(50)에서 P-E vs VT-Limited의 platform-thread 수·RSS·Heap·
  latency·throughput 비교표가 확보된다.
- Experiment B(Downstream-bound)로 "Gateway의 virtual thread 여유가 전체 시스템 completion
  throughput 증가를 의미하는가"가 **실측으로 확인되거나 반박**된다(결론 없이 남기지 않는다).
- Cross-phase(Phase 1 vs Phase 2) 비교는 k6 client-side Trend만으로 수행되고, JDK/Spring 세대 차이
  confound가 보고서에 명시된다.
- Phase 1과 동일한 신뢰성 검증 절차(clock drift/anonymous volume/invariant)가 Phase 2 run에도
  적용되고 Experiment Integrity 섹션으로 문서화된다.
- Phase 2 canonical 결과 문서 + Phase 2 최종 보고서(Phase 1 `phase1-final-report.md`와 동일 구조)가
  작성된다.

**이 설계 문서는 구현 착수 전 최종본이다. Phase 2 코드(Java Source/Dockerfile/k6 script)는 이
설계에 대한 사용자 승인 이후에 작성한다.**
