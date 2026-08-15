# Phase 1 Final Report — Baseline (`gateway-mvc-executor-java8`) Executor Concurrency Lab

Status: **Phase 1 완료**. Unit 1~6.7을 종합한 최종 보고서. 새 실험/새 metric/새 Executor 설정을
추가하지 않았다 — 기존 canonical 문서(`unit5-executor-matrix-screening.md`,
`unit6-formal-benchmark-results.md`, `docs/decisions/*.md`)를 source of truth로 그대로 인용/종합했다.
Date: 2026-08-15

---

## 1. Executive Summary

Phase 1은 "Servlet AsyncContext + 전용 `ThreadPoolExecutor` + `HttpURLConnection`(blocking) + SSE
relay"라는 실무에서 관찰된 실행 모델을, Java 8 / Spring Boot 1.5.22 Baseline으로 재현해 그 concurrency
한계를 정량적으로 측정했다. Executor screening(Unit 5, closed-model)으로 4가지 구성(D/E/A/B)의 포화
양상을 먼저 관찰하고, 그중 서로 다른 trade-off를 대표하는 3개 구성(B/E/A)을 constant-arrival-rate(open
model, R1/R2/R5/R12 = 1/2/5/12 req/s) 조건에서 warm-up 2분 + measurement 5분, 총 24개 formal
run(config당 8회)으로 반복 검증했다(Unit 6~6.7). 핵심 결론:

**"Blocking I/O를 유지하는 한 overload 비용은 사라지지 않고, Queue Wait / Reject / Platform Thread
증가 / Caller Thread Propagation 중 다른 형태로 이동한다."**

세 구성 모두 자신의 capacity(B≈1.28rps, E≈6.41rps) 안에서는 사실상 동일하게 동작하지만(R1), capacity를
넘는 순간부터 서로 다른 자원을 대가로 치른다 — B는 accept 자체를 포기하고, E는 platform thread를 늘리며,
A는 executor reject를 피하는 대신 overload를 Tomcat caller thread로 역전파한다. `HttpURLConnection`의
blocking read가 absolute deadline 이후에도 worker를 붙잡는 구조적 한계도 재현·측정했다. 이 결론이
Phase 2(Java 21 Virtual Thread)의 출발점이 된다.

---

## Cross-Phase Metric Compatibility (정책 — Phase 2 이후에도 유지)

Phase 1의 공식 24개 formal run은 **Unit 6 당시의 histogram bucket schema**(`GatewayMetrics.
FINE_LATENCY_BUCKETS`, 1초~60초 구간 22개 bucket)로 수집됐다. Unit 6.7에서 0.5~3초 구간을 더 세분화한
schema를 freeze했지만(`docs/decisions/monitoring-baseline.md` §6), 이는 **Phase 2 이후 새 run부터
적용**되며 Phase 1의 기존 run에는 소급 적용되지 않는다.

**따라서:**

- **Phase 1 server-side histogram quantile과 Phase 2 server-side histogram quantile을 동일한 정밀도의
  절대값처럼 직접 비교하지 않는다** — bucket 경계가 다르면 `histogram_quantile()`의 선형보간 오차 크기
  자체가 달라지기 때문이다.
- **Cross-implementation / Cross-phase latency 비교의 authoritative metric은 항상 동일한 k6 client-side
  Trend로 한다** — 특히 `client_ttfc_completed_seconds`(completed-only TTFC)와 client-side stream
  duration(`client_stream_duration_seconds`). 이 metric들은 서버 구현체나 bucket schema와 무관하게 원본
  표본에서 직접 percentile을 계산하므로, JDK/Executor 모델이 바뀌어도(Phase 2의 Virtual Thread 등) 같은
  기준으로 비교 가능하다.
- **Server-side histogram(`gateway_ttfb_seconds`, `executor_queue_wait_seconds`)은** (1) 내부 병목 분석,
  (2) Queue Wait / TTFB 관계 분석, (3) **동일 bucket schema로 실행된 run 간 비교**에만 사용한다.
- Phase 2 착수 시점에 필요하면 Phase 1 대표 configuration(B/E/A 각 1개 부하)을 최종 schema로 짧게
  재실행해 server-side comparison baseline을 별도로 만들 수 있다 — 이는 Phase 2의 작업이며, **Unit 7에서는
  추가 Benchmark를 하지 않는다.**

---

## 2. 실무 문제 정의

원래 관찰된 업무 구조:

```
Front → Spring MVC → AsyncContext → ThreadPoolExecutor → HttpURLConnection → FastAPI(LLM) SSE → Front relay
```

Tomcat의 request 처리 스레드는 `request.startAsync()` 호출 직후 즉시 반환되어 다음 요청을 받을 수
있지만, 실제 upstream(LLM) 호출을 수행하는 **chat 전용 `ThreadPoolExecutor`의 platform thread 하나는
FastAPI stream이 끝날 때까지(또는 실패할 때까지) blocking**된다 — `HttpURLConnection.getInputStream()`/
`BufferedReader.readLine()`이 JDK 표준 blocking API이기 때문이다. 이 구조에서는 동시 요청 수가 늘어날
때 "Tomcat 자체는 안 막히지만, LLM 응답을 실제로 기다리는 스레드 풀이 유한 자원"이라는 병목이 생긴다.
Phase 1은 이 병목이 서로 다른 `ThreadPoolExecutor` 설정(core/max/queue/rejection policy)에서 어떻게
다른 모습으로 나타나는지를 측정하는 것이 목적이다.

---

## 3. Baseline Architecture

- **JDK**: Java 8(Eclipse Temurin 8, arm64 native)
- **Application**: Spring Boot 1.5.22.RELEASE → Spring Framework 4.3.25.RELEASE, Embedded Tomcat
  8.5.43, Jackson 2.8.11.3 (모두 Spring Boot 1.5.22 BOM으로 고정 resolve, `docs/decisions/
  version-compatibility.md` §2)
- **Request lifecycle**: `ChatController.stream()` → `request.startAsync()` + `asyncContext.
  setTimeout(CHAT_TOTAL_TIMEOUT_MS)` → `chatExecutor.execute(new InstrumentedRunnable(...))` → (pool
  worker 또는 CallerRunsPolicy에 의한 caller thread) → `MockLlmClient.openStream()`
  (`HttpURLConnection`, connect/read timeout을 absolute deadline 기준 `remainingMs`로 clamp) →
  `BufferedReader.readLine()` 루프로 각 SSE 라인을 그대로 relay → EOF 시 `completed`, 그 외 경로는
  `docs/decisions/request-outcome-accounting.md`의 8종 outcome 중 하나로 exactly-once 기록 →
  `asyncContext.complete()`
- **단일 absolute deadline 설계**(`docs/decisions/timeout-semantics.md`): `AsyncContext.setTimeout()`과
  자체 deadline 체크가 **같은 기준점(요청 수신 시각) · 같은 길이**를 쓰도록 통일 — Queue Wait 시간이
  deadline 예산에서 빠지지 않는다.
- **Metrics**: `io.prometheus:simpleclient:0.16.0` 네이티브 API(Counter/Gauge/Histogram, label 직접
  지원) — Dropwizard MetricRegistry는 label 차원이 없어 채택하지 않음.
- 이 조합(Java 8/Spring 4.x/Boot 1.5.x)은 **모두 EOL 버전**이며, 프로덕션 권장이 아니라 실무에서 실제
  관찰된 실행 모델을 그대로 재현해 한계를 측정하기 위한 선택이다(`version-compatibility.md` §0).

---

## 4. Phase 1 Research Questions / Hypotheses — 최종 판정

| # | 가설 | 근거 | 판정 |
|---|---|---|---|
| H1-a | Bounded Queue + core<max(Config D)에서는 queue가 먼저 차고 그 다음에야 pool이 는다 | Unit 5 Table 2: D는 concurrency=30까지 `pool_size_peak=10`(불변) · `queue_size_peak=20`(꽉 참), concurrency=40에서야 `pool_size_peak=20`으로 증가 — ThreadPoolExecutor 공식 문서화된 동작 그대로 재현 | **SUPPORTED** |
| H1-a' | SynchronousQueue(Config E)는 queue wait 없이 pool이 더 빨리 증가한다 | Unit 5, 동일 core=10/max=50 조건에서 D vs E를 concurrency=30으로 직접 비교: D `pool=10,queue=20,TTFC p95=10.80s` vs E `pool=30,queue=0,TTFC p95=1.13s` — capacity(=max) 안에서 queue wait가 구조적으로 0 | **SUPPORTED** |
| H1-b | Large Queue + CallerRuns(Config A)에서 capacity 초과 후 caller thread가 실제 Tomcat 요청 스레드에서 실행된다 | Unit 5: `caller_runs`가 초과분과 1:1 일치 + 컨테이너 로그 thread-name 집계로 `nio-8080-exec-*`/`io-8080-exec-*`(Tomcat 스레드)에서 실제 실행 확인. Unit 6/6.6 formal에서도 재현: A-R5 caller_runs=875, A-R12=2975, platform_thread_peak 39→124로 부하와 함께 증가 | **SUPPORTED** |
| H1-c | Queue Wait가 TTFC 증가의 원인이 되는지 | 가장 깨끗한 증거는 Unit 5의 D vs E 자연실험(위 H1-a')이다 — core/max가 완전히 동일한 두 config에서 유일한 차이가 "queue wait 존재 여부"인데 TTFC p95가 10.80s vs 1.13s로 **약 9.6배** 차이난다. Unit 6 Table B에서도 B/E는 QueueWait(completed)와 TTFC(completed)가 밀접하게 함께 움직인다(B-R2: QueueWait와 TTFC 모두 capacity 초과 직후 급증) | **SUPPORTED** |
| H1-c′ (참고, Unit 5 원 표현) | Small Queue(B)는 처리량을 높이지 않지만 overload를 빠르게 드러낸다(B vs A) | B는 Queue 크기와 RejectionPolicy 두 축이 동시에 다른 A와 비교되므로 confound가 있다(Unit 5 §4). 방향성(B: capacity 안에서 TTFC 불변, A: capacity 초과분이 이미 accept된 요청의 TTFC까지 늘림)은 Unit 5·6 전체에서 일관되게 재현됐지만, "queue 크기만의 순수 효과"로는 분리되지 않는다 | **PARTIALLY SUPPORTED** (confound 있음, Unit 5 §4에 명시) |
| H-timeout | `HttpURLConnection`의 blocking read는 absolute deadline 이후에도 platform worker를 계속 점유할 수 있다 | Unit 5 착수 전 0-3 검증: `AsyncContext.onTimeout`이 t≈10.32s에 발동했지만 worker thread는 t≈12.55s까지(약 2.24초 더) `readLine()`에 blocking — read timeout이 absolute deadline이 아니라 매 read마다 리셋되는 상대 시간이기 때문 | **SUPPORTED** |
| H-disconnect | Client disconnect는 Front→Gateway→Mock LLM까지 cancellation으로 전파되고 active resource가 0으로 회수된다 | Unit 4-1 검증(`docs/decisions/load-generator.md` §4): k6 `client.close()`(3번째 event에서 강제 종료) → Mock LLM `mockllm_cancelled_requests_total` +1, `completed=0`, `active_requests=0` 확인. Gateway 측 `PrintWriter.checkError()` → `client_disconnect` outcome 경로도 코드로 존재(`request-outcome-accounting.md`) | **PARTIALLY SUPPORTED** — 메커니즘 자체는 실측 확인됐지만, 정식 24-run formal benchmark(scenario 02)는 client 측이 의도적으로 연결을 끊는 시나리오를 만들지 않아 **부하 조건에서 반복 검증되지는 않았다**(단발 verification 1건) |

---

## 5. Test Environment

| 항목 | 값 |
|---|---|
| Host | macOS (Darwin), Apple Silicon (arm64) |
| Container platform | 전 서비스 `linux/arm64` 고정(에뮬레이션 없음, `docker-compose.yml`) |
| Gateway JDK | Eclipse Temurin 8 (`eclipse-temurin:8-jdk-jammy`/`8-jre-jammy`) |
| Gateway Framework | Spring Boot 1.5.22.RELEASE / Spring Framework 4.3.25.RELEASE / Tomcat 8.5.43 |
| Build | Gradle 3.5.1 (Spring Boot 1.5.22 공식 지원 범위 내) |
| Gateway 자원 제한 | `cpus: 1.0`, `mem_limit: 1g` |
| Mock LLM | FastAPI 0.141.1 / uvicorn 0.52.1 / Python 3.12.13, `python:3.12-slim` linux/arm64 |
| Mock LLM 자원 제한 | `cpus: 2.0`, `mem_limit: 1g` (병목이 되지 않도록 넉넉히, `MAX_CONCURRENT_PROCESSING=0`) |
| Load generator | k6 v1.8.0 + xk6-sse v0.1.11 (정적 링크 커스텀 바이너리, `docs/decisions/load-generator.md`) |
| Monitoring | Prometheus v3.13.2 + Grafana, `scrape_interval=5s`(`docs/decisions/monitoring-baseline.md`) |
| Gateway timeout | `CHAT_CONNECT_TIMEOUT_MS=3000` / `CHAT_READ_TIMEOUT_MS=30000` / `CHAT_TOTAL_TIMEOUT_MS=60000` |
| Mock LLM이 병목이 아니었다는 근거 | Unit 5(21 run) + Unit 6(24 formal run) 전부 `mock_waiting_peak(mockllm_waiting_requests)=0` — 관찰된 모든 saturation/reject/timeout/caller-runs 현상은 전적으로 Gateway `ThreadPoolExecutor`에서 비롯됨 |

---

## 6. Mock LLM Workload

Formal Benchmark(Unit 6 이후)가 쓰는 workload(`load-test-k6/scenarios/02-constant-arrival-rate.js`
기본값):

```
firstChunkDelayMs = 1000
chunkIntervalMs   = 200
chunkCount        = 35

theoretical stream duration = firstChunkDelay + (chunkCount − 1) × chunkInterval
                             = 1000 + 34 × 200 = 7800ms = 7.8s
```

**실측 검증**(R1, Mock/Gateway가 전혀 경합하지 않는 조건, `client_stream_duration_seconds`): B/E/A 세
config 모두 median 7.9~7.96s로 이론값(7.8s)과 사실상 일치(오차는 네트워크/relay 오버헤드 수준).

> **참고**: Unit 5 Screening은 다른 scenario(`01-concurrent-connections.js`, `chunkCount=20`)의
> 기본값을 썼으므로 이론적 stream duration이 `1000+19×200=4.8s`였다 — 이는 계산 오류가 아니라 **Screening과
> Formal Benchmark가 서로 다른 workload 스크립트를 썼다는 사실**이며, 두 값을 혼동하지 않는다. 공식 분석
> 대상(Unit 6 이후)은 항상 7.8s다.

---

## 7. Executor Configurations

정식 Formal Benchmark(Unit 6)가 채택한 핵심 3개 전략:

| config | core | max | queue | rejection policy | 전략 |
|---|---|---|---|---|---|
| **B** | 10 | 10 | 20(bounded) | ABORT | Bounded Admission / Fast Rejection |
| **E** | 10 | 50 | 0(SynchronousQueue) | ABORT | Queue Avoidance / Thread Expansion |
| **A** | 10 | 10 | 100(bounded) | CALLER_RUNS | Rejection Avoidance / Overload Propagation |

**Config D**(core=10, max=50, queue=20(bounded), ABORT)는 Screening 단계의 control로만 썼다 — E와
동일한 core/max 조건에서 "queue가 있으면 무슨 일이 생기는가"(H1-a)를 보여주는 대조군이다. Screening
결과 E가 동일 core/max 조건에서 queue wait 측면에서 D보다 항상 같거나 낫고(H1-a′), D의 유일한 잠재적
장점(bounded queue로 인한 예측 가능한 메모리 사용)이 이 규모에서는 뚜렷하게 드러나지 않아, **정식
Benchmark 3회×5분 실행 대상에서 D를 제외**했다(Unit 6 계획 §0) — 열등해서가 아니라 H1-a 검증 목적을
Screening으로 이미 달성했고, 실행 시간을 아끼기 위함이다.

---

## 8. Screening Results (Unit 5, closed-model)

21개 run(concurrency 10~150), Mock LLM 비병목 확인(§5 참고) 하에서:

- **D — queue-first growth**: concurrency≤core(10)에서는 pool만 쓰고, core 초과분은 queue가 먼저
  흡수하며 queue가 꽉 찬 뒤에야 pool이 max까지 증가(H1-a).
- **E — thread-first growth**: SynchronousQueue라 queue에 저장할 곳이 없어, capacity(=max) 안에서는
  요청이 오는 즉시 pool이 늘어난다 — queue wait가 구조적으로 0(H1-a′).
- **A — CallerRuns propagation**: core+queue(110) 초과분이 정확히 1:1로 `caller_runs`가 되고, 그 실행이
  실제 Tomcat 요청 처리 스레드(`nio-8080-exec-*`)에서 동기적으로 일어남이 스레드 이름 로그로 확인됨(H1-b).
- **B — bounded backlog**: accepted가 core+queue(30)에서 하드 캡되고, capacity 안에 든 요청의 TTFC는
  concurrency가 늘어도 거의 고정(10.8~11.0s) — 초과분은 즉시(수 ms) 503 reject.

이 Screening 결과를 근거로 B(fast-fail)/E(queue 최소화)/A(무손실, 참고용) 세 전략을 Unit 6 정식
Benchmark 대상으로 추천했다(D 제외).

---

## 9. Formal Benchmark Methodology

**Open model 채택 이유**: Unit 5는 closed-model(고정 VUS)이었다 — Screening 목적에는 적합하지만, config마다
reject 속도가 다르면 VU가 iteration을 반복하는 속도도 달라져 **같은 VUS로도 config마다 실제 arrival
rate가 달라진다**(공정성 원칙 위배). Unit 6부터는 `constant-arrival-rate` executor(open model,
`02-constant-arrival-rate.js`)로 전환해 모든 config에 **정확히 동일한 유입률(RPS)**을 강제했다.

**부하 축(R1/R2/R5/R12) 선정 근거**: Unit 5 Screening에서 실측한 각 config의 closed-model capacity
경계(B: concurrency=30, E: concurrency=50, A: concurrency=110에서 각각 reject/caller-runs 시작)를,
Little's Law(`L=λW`, 이 용도로만 사용 — capacity 추정 자체에는 쓰지 않음)로 open-model 유입률로 환산해
R2(2rps, 모든 config의 core=10 이하 안전 구간)/R5(5rps, config별로 반응이 갈리기 시작하는 구간)/R12(12rps,
B/E capacity를 넘지만 A는 아직 안 넘는 구간)를 도출했다(Unit 6 계획 §3). R1(1rps)은 Unit 6.5에서 "세
config가 전혀 다르지 않은" 완전 안전 baseline으로 추가됐다.

**실행 조건**: Warm-up 2분(정식 결과에서 제외) → Measurement 5분(정식 결과로 채택) → Drain. R2/R1은
config당 1회(sanity — Screening에서 이미 안정적일 것이 명확히 예측된 구간), R5/R12는 config당 3회
반복(반복 간 variance를 봐야 하는 핵심 비교 구간).

---

## 10. Official Dataset

Unit 6.7에서 확정한 canonical dataset을 그대로 사용한다(`unit6-formal-benchmark-results.md` "Official
Formal Dataset" 참고):

| config | 구성 | runs |
|---|---|---|
| B | Attempt 3: R2×1, R5×3, R12×3 + Unit 6.5: R1×1 | 8 |
| E | Attempt 3: R2×1, R5×3, R12×3 + Unit 6.5: R1×1 | 8 |
| A | Unit 6.6 rerun: R2×1, R5×3, R12×3 + Unit 6.5: R1×1 | 8 |
| **합계** | | **24 (formal)** |

**Calibration-only(별도, formal 미포함)**: B-R2, E-R5 fine-bucket 재검증 2 run.

**공식 결과 계산에서 절대 제외**: Attempt 1(clock-domain 위험으로 전체 폐기), Attempt 2(Prometheus
anonymous volume 재사용으로 전체 폐기), A의 old rerun(dropped_iterations 문제로 폐기, §18 참고).

---

## 11. Throughput 결과

**병렬 worker 처리량 추정치**(단순 산술 — 요청 하나가 platform worker/thread 하나를 서비스 시간(7.8s)
동안 통째로 점유한다고 가정, **Little's Law가 아니다**):

```
B (core=max=10): 10 / 7.8s ≈ 1.282 req/s
E (max=50):      50 / 7.8s ≈ 6.410 req/s
```

**실측 completion rate와 비교** (median completion_ratio × arrival rate):

| config | 추정 capacity | 실측 completion rate | 비율(실측/추정) |
|---|---|---|---|
| B (R5/R12 평균) | 1.282 req/s | ≈1.33 req/s | ~1.04 |
| E (R12) | 6.410 req/s | ≈6.32 req/s | ~0.99 |

**±4% 이내로 일치** — 이 단순 병렬-worker 모델이 B/E의 capacity 초과 구간 sustained completion rate를
잘 설명한다. **A는 이 모델을 적용하지 않는다** — CallerRunsPolicy가 활성화되면 "core=10 고정 worker
수"라는 전제 자체가 깨지기 때문이다(§15).

---

## 12. Latency / Queue Wait

**Client TTFC(`client_ttfc_completed_seconds`, completed-only)를 authoritative latency로 사용한다**
— 원본 표본에서 직접 percentile을 계산하므로 histogram bucket 보간 오차가 없다.

**Queue Wait → TTFC 관계**: R1에서는 세 config 모두 queue wait가 없어 TTFC가 순수 `firstChunkDelay(1s)`
수준(1.01s)에 고정된다. capacity를 넘으면 queue wait가 급증하고 TTFC도 함께 급증한다 — 가장 깨끗한
증거는 Unit 5의 D vs E 자연실험(§4 H1-c, core/max 동일·queue wait 유무만 다름 → TTFC 9.6배 차이).
Unit 6 formal에서도 B는 capacity 초과 즉시 TTFC p95가 16.9s 근방에 고정되고(queue capacity 20이 항상
꽉 찬 상태), E는 capacity(6.41rps) 도달 전까지 TTFC가 1.01s로 완전히 안정적이다가 R12에서만 급변한다.

**Server histogram의 한계**: Prometheus classic histogram의 `histogram_quantile()`은 bucket 내부에서
균등분포를 가정해 선형보간한다 — 원래 bucket 경계(`..., 10, 30, 60`)가 너무 성겨서 큰 지연(수십 초)
구간의 quantile이 실제보다 훨씬 위로 왜곡됐다(B-R2: client TTFC 16.91s vs gateway TTFB 28.90s로 관측,
실제로는 ~12초 오차). Unit 6.6에서 1초~60초 구간 bucket을 8→22개로 재보정해 이 오차를 크게 줄였다
(재보정 후 B-R2 calibration: TTFC 16.77s vs TTFB 17.76s, 격차 ~1초). 다만 절대값이 작은(1~2초) 구간은
여전히 상대오차가 남아, Unit 6.7에서 Phase 2용 추가 세분화 schema를 freeze했다(§ Cross-Phase Metric
Compatibility, `monitoring-baseline.md` §6). **Server histogram은 참고용이며, 결론은 항상 client-side
Trend를 근거로 한다.**

---

## 13. B — Bounded Admission / Fast Rejection

**장점**:
- backlog upper bound(core+queue=30)가 명확 — 자원 사용이 예측 가능
- capacity 안에 든 요청의 TTFC는 부하가 늘어도 사실상 고정(R2~R12에서 16.7~16.9s로 일정)
- overload가 명시적으로(503 reject) 드러남 — 클라이언트가 즉시 재시도 판단 가능

**단점**:
- capacity(≈1.28rps) 이상 요청은 accept 자체를 포기한다 — R2에서 이미 reject 33.4%, R12에서 88.9%
  (Table A)
- **accepted 요청도 queue 내부에서는 오래 기다릴 수 있다** — TTFC p95가 R1의 1.01s에서 R2 이상 16.9s
  근방으로 급증한 뒤 그 수준에 고정된다. "빠른 방식"이 아니다 — accept된 요청의 지연 자체는 결코 짧지
  않다.

---

## 14. E — Queue Avoidance / Thread Expansion

**장점**:
- queue wait가 구조적으로 거의 없음(SynchronousQueue)
- max capacity(6.41rps)에 도달하기 전까지 TTFC가 완전히 안정(R1~R5: 1.00~1.01s, R5에서도 completion_ratio
  100% 유지)
- 세 config 중 가장 높은 completion throughput(R12에서도 completion_ratio 52.7%로 B(11.1%)보다 훨씬 높음)

**비용**:
- platform thread가 부하와 함께 계속 증가 — pool_size_peak가 R1의 10에서 R12의 50까지, 그 대가로
  `platform_thread_peak`도 40→108로, Heap peak 26.2MB→68.9MB, RSS peak 159.0MB→225.3MB로 증가(Table
  C/D)
- max에 도달하면 결국 reject가 발생 — R12에서 reject 47.3%

**핵심 trade-off**: E는 "capacity까지는 최고의 latency", 그 대가는 "capacity까지 도달하는 동안 계속
늘어나는 thread/RSS 비용"이다.

---

## 15. A — Rejection Avoidance / Overload Propagation

**Reject가 0이라는 것과 성공률이 높다는 것을 혼동하지 않는다** — A는 R2~R12 전 구간에서 reject=0%지만,
timeout_rate가 R2에서 56.5%, R5에서 29.5%로 상당히 높다(Table A). "reject 없음" ≠ "성공 보장".

CallerRuns가 증가하면서 **executor isolation → Tomcat thread 사용**으로 부하가 역전파된다: R2에서
caller_runs=33(전체의 5.5%, 아직 작음)이지만, R5에서 875건, R12에서 2975건까지 늘며 "core=10 고정
worker" 모델 자체가 깨진다(§11). 이 초과 실행 자원 덕분에 completion_ratio는 오히려 R5의 68.5%에서
R12의 86.6%로 **개선**되지만(§4), 이는 CallerRunsPolicy가 Tomcat request thread를 실행 자원으로
흡수하기 때문이며, executor가 원래 제공하려던 "chat 작업과 Tomcat 자체 요청 처리 능력을 분리한다"는
isolation을 정면으로 훼손한 대가다.

| config-load | caller_runs | platform_thread_peak | Heap peak(MB) | RSS peak(MB) |
|---|---|---|---|---|
| A-R1 | 0 | 39 | 26.2 | 157.5 |
| A-R2 | 33 | 48 | 42.2 | 192.2 |
| A-R5 | 875 (875–876) | 66 (66–81) | 69.7 | 228.8 |
| A-R12 | 2975 (2973–2977) | 124 (121–129) | 130.5 | 310.9 |

R12에서 platform_thread_peak(124)와 RSS peak(310.9MB)는 세 config 중 가장 크다 — A는 "손실 없음"의
대가로 **thread/memory 비용이 세 config 중 가장 크게** 증가한다.

---

## 16. Timeout / Blocking Read

절대 deadline(`CHAT_TOTAL_TIMEOUT_MS`)을 구현했음에도, `HttpURLConnection`/`BufferedReader.readLine()`
의 blocking 특성 때문에 worker가 deadline 이후로도 붙잡혀 있을 수 있다(Unit 5 착수 전 0-3 검증,
`docs/test-results/phase1/absolute-deadline-blocking-read.md`):

- t≈10.32s: `AsyncContext.onTimeout` 발동(deadline=10s) → 클라이언트에게는 이미 응답 종료
- t≈12.55s: worker thread가 `SocketTimeoutException`을 그제서야 catch — 이 순간까지 `gateway_
  executor_active_threads`가 1을 유지
- **platform worker thread가 AsyncContext 타임아웃 이후로도 약 2.24초(2.235503s) 더 blocking read
  상태로 남아있었다.**

원인은 버그가 아니라 구조적 한계다 — Socket read timeout은 absolute deadline이 아니라 **매 read
호출마다 리셋되는 상대 시간**이라, 상류에서 chunk가 조금이라도 계속 오면 실제 차단 해제 시점이 "마지막
성공한 read 이후 경과 시간"만큼 계속 뒤로 밀릴 수 있다. Config A(CallerRunsPolicy)처럼 caller thread
자체가 blocking read에 묶이는 구성에서는, 이 지연이 **Tomcat request thread 하나를 그만큼 더 오래
점유**한다는 뜻이므로 더 중요하다. 이것이 **Phase 2로 이어지는 가장 중요한 구조적 질문 중 하나**다(§22).

---

## 17. Client Disconnect

Unit 4-1에서 실측 검증됨(`docs/decisions/load-generator.md` §4): k6가 6개 chunk 중 3번째 event 수신
직후 `client.close()`로 연결을 강제 종료하면, Mock LLM 쪽 `mockllm_cancelled_requests_total`이 1
증가하고 `completed=0`, `active_requests=0`으로 즉시 회수됨을 확인했다 — `close()`가 실제 TCP 연결까지
끊어 **Front(k6) → Gateway → FastAPI Mock LLM까지 취소가 전파**됨을 end-to-end로 확인했다.

Gateway 측 경로도 코드로 확인된다: `PrintWriter.checkError()`가 클라이언트로의 쓰기 실패를 감지하면
`client_disconnect` outcome으로 exactly-once 기록되고(`docs/decisions/request-outcome-accounting.md`),
`connection.disconnect()`가 `finally` 블록에서 항상 호출되어 upstream 연결도 정리된다.

**범위의 한계**: 이 검증은 Unit 4-1의 단발 verification run이며, 정식 24-run formal benchmark
(`02-constant-arrival-rate.js`)는 client가 의도적으로 중간에 연결을 끊는 시나리오를 만들지 않는다 —
즉 이 cancellation 경로는 **메커니즘은 확인됐지만 부하 조건에서 반복 검증되지는 않았다**(§4 H-disconnect
판정: PARTIALLY SUPPORTED).

---

## 18. Experiment Integrity — Benchmark 결과 신뢰성 확보 과정

이 절은 실패담이 아니라 **장시간 반복 Benchmark의 신뢰성을 실제로 어떻게 검증했는가**를 보여준다.

**Attempt 1**(21/21 완료, 3개 run invariant 위반): host 시계와 Docker VM(Prometheus가 실제로 실행되는
clock domain)이 preflight 통과 이후에도 벌어질 수 있음을 확인 → **전체 폐기**, 모든 측정 타임스탬프를
Docker VM 자신의 시계로 캡처하도록 수정.

**Attempt 2**(21/21 완료, 2개 run invariant 위반): clock-domain 수정은 유효했지만 원인이 아니었음이
드러남 — `docker compose up --force-recreate`가 **Prometheus의 익명 volume(TSDB 데이터)을 기본적으로
재사용**해 config 전환 후에도 이전 config의 값이 최대 5분간 그대로 서빙됨을 직접 재현해 확인 → **전체
폐기**, `--renew-anon-volumes` 추가.

**Attempt 3**(공식 채택): anonymous volume renewal + Docker VM 기준 timestamp + invariant validation →
21/21 valid. Unit 6.5에서 R1 3 run 추가(3/3 valid).

**A의 dropped_iterations**: Attempt 3에서 A는 모든 부하 수준에서 k6 `dropped_iterations`가 2.5~2.8%
발생(B/E는 0%) — VU 상한 문제가 아니라 A의 응답 지연 분산이 커서 k6의 VU spin-up 속도가 못 따라간
것으로 진단, `PRE_ALLOCATED_VUS`를 100→800으로 늘려 검증(720/720 iteration 전부 시작, dropped_
iterations=0 확인) 후 **A의 formal run 7개를 재실행**(Unit 6.6) → 7/7 valid, dropped_iterations 0%.

이 외에 Screening(Unit 5) 단계에서 발견/수정한 측정 인프라 이슈도 같은 신뢰성 확보 과정의 일부다:
Docker Desktop VM 시계가 최대 203초까지 drift하는 현상을 실측해 preflight 가드(drift>5초 시 중단)를
추가했고, Prometheus scrape(5s 간격)와 run 종료 시점 사이의 race로 값이 0으로 잡히는 결과 수집
스크립트 버그를 발견해 수정했다(둘 다 Unit 5 §5, 애플리케이션 레벨 버그 아님).

**애플리케이션(Gateway) 레벨에서는 Phase 1 전 기간 동안 새 버그를 발견하지 못했다** — Unit 5 21개 run +
Unit 6 24개 formal run 전부 `gateway_request_received_total == sum(gateway_request_outcome_total)`
invariant 통과, `unexpected_error` 관측 0건.

---

## 19. Measurement Lessons

1. **Client TTFC와 Server TTFB는 population이 다를 수 있다** — `gateway_ttfb_seconds`는 첫 chunk가
   relay되기만 하면(outcome 무관) 기록되지만, `client_ttfc_completed_seconds`/`executor_queue_wait_
   seconds{outcome="completed"}`는 끝까지 성공한 요청만 포함한다. timeout이 많은 config(A)에서 이
   차이가 두드러진다(A-R2: TTFB 59.44s vs TTFC/QueueWait(completed) 52.8s대) — 코드로 확정된 사실이며
   bucket 문제가 아니다(Unit 6.7).
2. **"completed-only" metric은 별도로 분리해야 한다** — `client_ttfc_seconds`(outcome 태그, 전체
   population)와 `client_ttfc_completed_seconds`(completed 전용)는 서로 다른 metric이다. 초기 문서가
   이를 혼동해 서술했던 것을 Unit 6.7에서 코드 대조로 정정했다.
3. **Histogram bucket 성김은 quantile을 왜곡한다** — classic histogram의 `histogram_quantile()`은
   bucket 내부에서 균등분포를 가정해 선형보간한다. 큰 값(수십 초) 구간뿐 아니라 작은 값(1~2초) 구간도
   bucket이 성기면 왜곡된다(Unit 6 §5, Unit 6.7 §5-6).
4. **CallerRuns task는 executor queue wait population에서 아예 제외된다** — `executor_queue_wait_
   seconds`는 pool worker가 실행한 task만 관측하고, caller-run task는 애초에 큐에 들어가지 않으므로
   이 metric에 전혀 잡히지 않는다(코드 주석으로 명시적 설계, `GatewayMetrics.java`). A의 R5/R12처럼
   caller_runs 비중이 큰 config에서는 이 metric이 실제 유입량의 상당 부분을 반영하지 못한다는 뜻이다.
5. **arrival rate와 completion throughput은 다르다** — k6 constant-arrival-rate는 "요청을 그 rate로
   시도한다"는 뜻이지 "그 rate로 완료된다"는 뜻이 아니다. B-R12는 12rps를 시도하지만 실제
   completion rate는 ≈1.33/s에 불과하다(§11).
6. **k6 VU 사전할당 부족은 VU 상한과 무관하게 iteration을 드롭시킬 수 있다** — 응답 지연 분산이 큰
   대상(A)에서 `PRE_ALLOCATED_VUS`가 부족하면 `MAX_VUS`에 여유가 있어도 `dropped_iterations`가 발생한다
   (§18).

---

## 20. Java 8 / Spring 4.x 환경에서의 현실적인 개선안

**Phase 1 결과만을 근거로** 작성한다.

- 업스트림 blocking I/O를 유지하는 chat executor에 **무조건 큰(사실상 무제한) queue를 쓰지 않는다** —
  A처럼 queue+CallerRuns 조합은 reject를 없애지만 지연을 데드라인까지 밀어붙이고 Tomcat thread까지
  잠식한다(§15).
- **bounded queue + explicit overload policy**를 쓴다 — B처럼 capacity를 명확히 정하고 그 이상은
  즉시(수 ms) reject하면, 최소한 accept된 요청의 지연은 예측 가능해진다.
- **단일 absolute deadline** 설계를 유지한다 — Queue Wait 시간이 deadline 예산에서 빠지지 않도록,
  `AsyncContext.setTimeout()`과 자체 deadline 체크를 같은 기준점·같은 길이로 통일한다(이미 구현됨,
  `docs/decisions/timeout-semantics.md`).
- **stale work를 실행 직전에 차단**한다 — worker가 실제로 upstream을 호출하기 전에 `state.isReleased()`
  /`remaining<=0`을 먼저 체크해, 이미 클라이언트에게 응답이 끝난 요청에 자원을 쓰지 않는다(이미 구현됨).
- **client disconnect를 상위(upstream)까지 전파**한다 — `PrintWriter.checkError()`로 쓰기 실패를
  감지해 즉시 `connection.disconnect()`하면 이미 버려진 요청이 upstream 자원을 계속 붙잡지 않는다(이미
  구현·검증됨, §17).
- **executor 자체를 metric으로 노출**한다 — `pool_size`, `queue_size`, `caller_runs`, `queue_wait`
  없이는 이 Phase 1 전체 분석이 불가능했다. 실무에서도 이 4가지가 없으면 "지금 executor가 어느
  전략으로 포화됐는지" 원인 파악이 불가능하다.
- **queue wait를 직접 모니터링**한다 — TTFC가 늘어나는 원인이 upstream(LLM) 지연인지 Gateway 자체
  queue wait인지 구분할 수 있어야, 잘못된 곳(예: LLM 스케일)에 투자하는 실수를 막는다.
- **FastAPI(또는 실제 LLM 서버)의 capacity와 Gateway executor 크기를 연계**해서 설계한다 — Phase 1은
  Mock LLM을 의도적으로 비병목으로 고정했지만(§5), 실무에서는 downstream capacity가 Gateway executor
  크기보다 작을 수 있고, 그 경우 이 Phase 1의 결론(overload 비용이 executor 안에서 이동한다)에 downstream
  자체의 saturation이 추가로 겹친다.

**단, "Small Queue + Abort가 항상 정답"이라고 결론 내리지 않는다** — B/E/A는 서로 다른 비즈니스
요구(빠른 실패로 클라이언트가 즉시 재시도할지, 더 오래 기다리더라도 손실을 최소화할지, thread/memory
비용을 얼마나 감수할지)에 따라 선택이 갈리는 trade-off이지, 셋 중 하나가 객관적으로 우월한 것이 아니다.

---

## 21. Phase 1의 한계

- **Mock LLM 사용** — 실제 LLM 추론 서버(GPU 병목, 배치 처리, 실제 토큰 생성 지연 분포)를 대체하지
  않는다. Mock은 고정된 chunk 간격(200ms)으로만 지연을 흉내낸다.
- **macOS Docker Desktop 환경** — Linux 프로덕션 환경과 다른 VM 계층이 존재하며, 이번 실험에서
  실제로 시계 drift(최대 203초) 문제를 유발했다(§18). 클라우드/Linux 호스트에서는 재현되지 않을 수
  있는 환경 특유의 이슈다.
- **CPU/Memory 조건이 한정적** — `cpus:1.0`/`mem_limit:1g`(Gateway) 한 조합만 측정했다. 다른 자원
  한도에서 capacity 임계값이 어떻게 이동하는지는 확인하지 않았다.
- **HTTP client는 `HttpURLConnection`만 사용** — 다른 blocking/non-blocking client(Apache
  HttpClient, `RestTemplate`, WebClient 등)의 특성은 포함하지 않는다.
- **플랫폼 스레드만 검증** — Virtual Thread, Reactor(WebFlux) 등 다른 동시성 모델은 아직 다루지 않았다
  (Phase 2 대상, §22).
- **server histogram schema가 Phase 1 도중 보정됐다** — Unit 6.6에서 1초~60초 구간 bucket을
  8→22개로 재보정했다. Phase 1 formal 24-run은 이 보정 이후 schema로 수집됐지만, Unit 6.7에서 추가로
  0.5~3초 구간을 더 세분화한 schema는 Phase 2 이후에만 적용된다(Cross-Phase Metric Compatibility
  참고) — Phase 1 안에서도 시점에 따라 정밀도가 달랐다는 사실 자체를 한계로 기록한다.
- **client disconnect cancellation은 부하 조건에서 반복 검증되지 않았다** — 메커니즘은 확인됐지만
  단발 verification 1건뿐이다(§17).
- **실제 LLM/GPU 병목 미검증** — Mock LLM은 의도적으로 비병목으로 고정했으므로(§5), "LLM 서버 자체가
  느려지거나 포화될 때" Gateway executor와 어떻게 상호작용하는지는 Phase 1의 범위 밖이다.

---

## 22. Phase 2로 이어지는 질문

Phase 1은 하나의 자연스러운 질문으로 수렴한다:

**"Blocking Programming Model을 유지하면서 platform thread 비용만 제거하면 어떻게 되는가?"**

Phase 1에서 확인한 세 가지가 이 질문을 정당화한다: (1) E는 capacity까지 완벽한 latency를 제공하지만
그 대가가 계속 늘어나는 platform thread/RSS다(§14), (2) A는 CallerRunsPolicy로 Tomcat platform
thread까지 잠식한다(§15), (3) `HttpURLConnection`의 blocking read는 deadline 이후에도 platform
worker를 붙잡을 수 있다(§16) — 세 문제 모두 "platform thread가 유한 자원이고 blocking 동안 계속
점유된다"는 동일한 근본 원인에서 나온다.

**Phase 2 설계**: Java 21 Platform Thread vs Java 21 Virtual Thread를, 동일 JDK · 동일 Spring ·
동일 HTTP Client · 동일 Mock LLM · 동일 workload 조건에서 비교한다 — Executor 설정만 바꾸는 것이
아니라 **동시성 모델 자체**를 바꿨을 때, Phase 1이 관찰한 "overload 비용이 다른 형태로 이동한다"는
현상이 사라지는지, 아니면 (예: pinning 등으로) 또 다른 형태로 이동할 뿐인지를 검증한다.

---

## 23. Portfolio Summary

### A. README용 (5~8줄)

> Java 8 Spring MVC + `ThreadPoolExecutor` 기반 LLM 스트리밍 Gateway의 concurrency 한계를 실측한
> Benchmark Lab. Bounded-queue/Abort, SynchronousQueue/Thread-expansion, CallerRuns 세 Executor
> 전략을 constant-arrival-rate 부하(1~12 req/s)로 24회 반복 측정해, "blocking I/O를 유지하는 한
> overload 비용은 사라지지 않고 Queue Wait/Reject/Thread 증가/Caller Thread 전파 중 다른 형태로
> 이동한다"는 결론을 정량적으로 검증했다. Docker VM 시계 drift, Prometheus anonymous volume 재사용
> 등 실제로 발생한 측정 인프라 문제를 원인 규명 후 재실행해 신뢰성을 확보했다. 다음 Phase에서는 이
> 결론을 Java 21 Virtual Thread와 비교한다.

### B. 면접 설명용 스크립트 (1~2분)

> "LLM 스트리밍 API를 Java 8 Spring MVC로 구현할 때, request thread는 비동기로 반환되지만 실제
> upstream 호출을 담당하는 executor의 platform thread는 응답이 끝날 때까지 blocking된다는 문제가
> 있었습니다. 이 프로젝트는 그 executor를 세 가지 방식으로 구성해봤습니다 — 작은 queue에 즉시
> reject하는 방식, queue 없이 thread를 늘리는 방식, queue를 크게 잡고 넘치면 호출자 스레드에서 직접
> 실행하는 방식입니다. 각 방식을 1~12 req/s 부하로, warm-up 2분에 5분씩 세 번 반복해서 24번 측정했더니,
> 세 방식 모두 여유 있을 때는 똑같이 동작하지만 capacity를 넘으면 서로 다른 대가를 치른다는 걸 확인했습니다.
> 하나는 요청을 거절하고, 하나는 스레드와 메모리를 계속 늘리고, 마지막 하나는 reject는 없지만 그 부하를
> Tomcat 자체 스레드로 떠넘겨서 원래 격리하려던 목적을 스스로 깨뜨렸습니다. 그리고 이 셋 중 뭐가
> '정답'이 아니라, blocking I/O 구조를 유지하는 한 overload의 비용은 없어지는 게 아니라 항상 어딘가로
> 옮겨간다는 게 핵심 결론이었습니다. 측정 과정에서 Docker VM 시계가 실제로 어긋나거나 Prometheus가
> 이전 설정 값을 계속 서빙하는 문제도 발견해서, 원인을 찾아 고치고 다시 실행하는 과정 자체도 기록으로
> 남겼습니다. 다음 단계는 이 구조를 그대로 두고 Java 21의 Virtual Thread로 바꾸면 이 costs가 실제로
> 사라지는지 확인하는 겁니다."

---

**Phase 1 완료.**
