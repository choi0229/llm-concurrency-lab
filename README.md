# llm-concurrency-lab

LLM 스트리밍(SSE) Gateway의 concurrency 전략을 실측 Benchmark로 비교하는 연구용 랩.
Java 8 Spring MVC Baseline으로 시작해(Phase 1), Virtual Thread(Phase 2)·WebClient/WebFlux(Phase 3)로
질문을 확장하고, 세 아키텍처의 실제 scalability boundary를 두 축으로 측정한다(Phase 4).

## Phase 1 → 4 Research Journey (완료 / Frozen)

> **P1 Executor** — Async Servlet 뒤 `ThreadPoolExecutor`에서 overload는 어디로 가는가? → 사라지지
> 않고 queue wait / 503 reject / platform-thread 증가 / caller-thread 전파로 이동. **queue wait이
> client latency를 가장 먼저 무너뜨림**(동일 core/max, queue 유무만 다른 두 config에서 TTFC ~9.6배).
>
> **P2 Virtual Thread** — 같은 blocking 코드를 VT로 처리하면? → JVM platform-thread 수가 부하와
> **분리**됨(peak −30~52%). 하지만 **RSS/CPU는 따라오지 않음**(CPU +18~27%). admission control은 여전히
> 필요.
>
> **P3 WebClient/WebFlux** — thread-per-request를 걷어내면 모든 resource가 좋아지나? → platform
> threads **80→49→37**(−53.75%), throughput/latency 불변. 하지만 **CPU/RSS는 또 따라오지 않음**
> (A→B CPU +27~32%).
>
> **P4 Scalability Boundary** — 그래서 실제 경계는? → **Platform Thread+Queue는 경계를 발견**
> (Closed MSC [50,56] / MRC [350,400], Open MSAR [6,7] req/s). **Virtual Thread / WebFlux는 single-host
> control-valid 범위 내에서 경계 미발견**(Closed ≥1120, Open ≥168 req/s, 둘 다 CONTROL-CENSORED;
> M2 vs M3 ranking INCONCLUSIVE) — Virtual Thread 한계처럼 보이던 실패를 shared-host ephemeral-port
> confound로 규명해 architecture 한계와 benchmark 한계를 분리.

- **전체 프로젝트 Final Report**: [`docs/test-results/llm-concurrency-lab-final-report.md`](docs/test-results/llm-concurrency-lab-final-report.md)
- **전체 프로젝트 Portfolio Summary**: [`docs/portfolio/llm-concurrency-lab-summary.md`](docs/portfolio/llm-concurrency-lab-summary.md)
- 각 Phase의 상세 결과는 아래 Phase별 섹션 참조. **Phase마다 runtime이 다르므로**(Java 8/1.5.22 →
  Java 21/3.x → Java 8/2.7.18 → Java 21/4.1.0) Phase 간 절대 수치를 직접 비교하지 않고 방향·패턴만
  비교한다.

## Phase 1 — Baseline (`gateway-mvc-executor-java8`) — 완료 (Frozen)

Servlet AsyncContext + 전용 `ThreadPoolExecutor` + `HttpURLConnection`(blocking) 구조에서,
Bounded Queue/Abort(B) · SynchronousQueue/Thread-expansion(E) · Large Queue/CallerRuns(A) 세
Executor 전략이 overload 시 어떤 비용을 치르는지 constant-arrival-rate 부하(1~12 req/s)로 24회
반복 측정했다.

- **Final Report**: [`docs/test-results/phase1/phase1-final-report.md`](docs/test-results/phase1/phase1-final-report.md)
- **Portfolio Summary**(README/면접용 요약): [`docs/portfolio/phase1-summary.md`](docs/portfolio/phase1-summary.md)
- **Canonical dataset**: [`docs/test-results/phase1/unit6-formal-benchmark-results.md`](docs/test-results/phase1/unit6-formal-benchmark-results.md)
  — 이 문서가 Phase 1 공식 수치의 유일한 source of truth (24 formal runs + 2 calibration-only runs,
  원본 raw 데이터: `docs/test-results/phase1/unit6/_aggregated.json`)
- **Histogram schema freeze (Phase 2용)**: [`docs/decisions/monitoring-baseline.md`](docs/decisions/monitoring-baseline.md) §6

**Phase 1 Runtime / Benchmark 버전** (근거: `docs/decisions/version-compatibility.md`,
`docs/decisions/load-generator.md`):

| 구성요소 | 버전 |
|---|---|
| JDK | Eclipse Temurin 8 (`8-jdk-jammy`/`8-jre-jammy`, linux/arm64) |
| Spring Boot / Framework | 1.5.22.RELEASE / 4.3.25.RELEASE (Embedded Tomcat 8.5.43) |
| Gradle | 3.5.1 |
| Mock LLM | FastAPI 0.141.1, uvicorn 0.52.1, Python 3.12.13 |
| Load generator | k6 v1.8.0 + xk6-sse v0.1.11(정적 링크 커스텀 바이너리) |
| Monitoring | Prometheus v3.13.2, scrape interval 5s |

> **Phase 1은 freeze됐다.** 코드/결과/문서를 더 이상 수정하거나 추가 formal benchmark를 수행하지
> 않는다 — 위 canonical 문서가 최종본이다.

## Phase 2 — Platform Thread vs Virtual Thread (`gateway-mvc-java21`) — 완료 (Frozen)

Phase 1 결론("blocking I/O를 유지하는 한 overload 비용은 사라지지 않고 다른 형태로 이동한다")을
바탕으로, 동일 Java 21 환경에서 Chat Executor 하나만 Platform `ThreadPoolExecutor`(P-E)와 Virtual
Thread(VT-Limited, `Semaphore(50)` admission gate로 동일 concurrency ceiling 유지)로 바꿔 R3/R6/R8 ×
3회 반복 = **18 valid run**(Primary Formal)을 측정하고, 이어서 JFR `jdk.VirtualThreadPinned`
diagnostic과 VT-Unlimited(admission gate 없는 구성) screening을 Secondary로 추가 실행했다.

- **Final Report**: [`docs/test-results/phase2/phase2-final-report.md`](docs/test-results/phase2/phase2-final-report.md)
- **Portfolio Summary**(README/이력서/면접용 요약): [`docs/portfolio/phase2-summary.md`](docs/portfolio/phase2-summary.md)
- **Primary canonical dataset**: `docs/test-results/phase2/unit6-native/{pe,vtl}-{r3,r6,r8}-run{1,2,3}/`
  (18 valid runs, raw), [`_aggregated.json`](docs/test-results/phase2/unit6-native/_aggregated.json)
  (median/min/max/CV, `scripts/aggregate_phase2_native_formal.py`) — 이 aggregate가 Phase 2 Primary
  결과의 공식 수치 source of truth이며 Secondary 결과 반영 후에도 수정되지 않았다. Mac + Docker
  Desktop discarded attempt(`docs/test-results/phase2/unit6/discarded/`)와 native macOS Functional
  Smoke/RSS 검증/Stability Canary run은 **canonical dataset이 아니다**(harness 검증 전용, Formal
  통계에 포함되지 않음).
- **Secondary raw 결과**: `docs/test-results/phase2/secondary/jfr-pinning-diagnostic/`(B-0/B-1/B-2),
  `docs/test-results/phase2/secondary/vt-unlimited-screening/`(A-1/A-2) — 각 조건 1회 관찰이며 Primary
  18-run과 같은 반복측정 통계가 아니다.
- **환경 설계**: Mac + Docker Desktop 환경 탈락 및 그 근거
  [`docs/decisions/phase2-formal-linux-environment.md`](docs/decisions/phase2-formal-linux-environment.md),
  최종 채택된 native macOS ARM64(Docker-free) 환경
  [`docs/decisions/phase2-formal-native-macos-environment.md`](docs/decisions/phase2-formal-native-macos-environment.md).
- 설계 문서: [`docs/test-plan/phase2-design.md`](docs/test-plan/phase2-design.md),
  [`docs/test-plan/phase2-formal-protocol.md`](docs/test-plan/phase2-formal-protocol.md). Java 21/
  Spring Boot 4.x 버전: `docs/decisions/version-compatibility.md` §6.

**Primary 핵심 결과**: 동일 admission ceiling(=50)에서 completion throughput/rejection/TTFC는 반복
측정 범위에서 실질적 차이를 확인하기 어려웠으나, JVM platform thread peak는 근접/과부하 구간(R6/R8)에서
Virtual Thread가 약 50~52% 낮았다 — 다만 이 절감이 RSS/CPU 절감으로 일관되게 이어지지는 않았다(상세는
Final Report §16-24).

**Secondary 핵심 결과**: JFR positive control(intentional pinning 5/5 검출)로 detector pipeline을
검증한 뒤, 실제 Gateway의 정상 SSE·~30초 blocking read 두 workload 모두에서 `jdk.VirtualThreadPinned`가
관측되지 않았다(NOT OBSERVED UNDER TESTED CONDITIONS). Admission gate를 없앤 VT-Unlimited는 Gateway
자신을 500 concurrent까지 clean하게 유지했지만 downstream(Mock) capacity 자체는 늘리지 못했다 —
admission 없이는 초과 부하가 downstream waiting queue로 이동해 tail latency가 크게 늘어났다(최대
16.7s/23.6s). **Virtual Thread는 downstream capacity 기반 admission control을 대체하지 않는다**(상세는
Final Report §7/§8/§25).

> **Phase 2는 freeze됐다.** 코드/결과/문서를 더 이상 수정하거나 추가 Benchmark·JFR diagnostic·
> VT-Unlimited run을 수행하지 않는다 — 위 Final Report/Portfolio Summary가 최종본이다.

## Phase 3 — Blocking MVC vs WebClient vs WebFlux (`gateway-mvc-blocking-spring5` / `gateway-mvc-webclient` / `gateway-webflux`) — 완료 (Frozen)

동일한 Java 8 + Spring Boot 2.7.18 런타임 위에서 Spring MVC + `HttpURLConnection`(blocking
outbound, P3-A), Spring MVC + WebClient(non-blocking outbound, P3-B), Spring WebFlux + WebClient
(완전 reactive, P3-C) 세 아키텍처를 비교했다. Outbound 실행 모델 변경(Experiment A: P3-A vs P3-B)과
서버 응답 모델 변경(Experiment B: P3-B vs P3-C)을 각각 하나의 변수만 바꾸는 pairwise 실험으로
분리해, R3(안정)/R7(근접 과부하)/R10(과부하) × 3회 반복 = **27 valid Formal run**을 측정했다.

- **Final Report**: [`docs/test-results/phase3/phase3-final-report.md`](docs/test-results/phase3/phase3-final-report.md)
- **Portfolio Summary**(README/이력서/면접용 요약): [`docs/portfolio/phase3-summary.md`](docs/portfolio/phase3-summary.md)
- **Canonical dataset**: `docs/test-results/phase3/unit7-formal/<27 canonical run dirs>/`(raw,
  canary 제외), canonical aggregate:
  [`docs/test-results/phase3/unit7-formal/aggregate-result.json`](docs/test-results/phase3/unit7-formal/aggregate-result.json)
  (`scripts/aggregate_phase3_formal.py`) — Phase 3 Primary 결과의 공식 수치 source of truth.
- **설계/결정 문서**: [`docs/test-plan/phase3-design.md`](docs/test-plan/phase3-design.md),
  [`docs/test-plan/phase3-formal-protocol.md`](docs/test-plan/phase3-formal-protocol.md),
  `docs/decisions/phase3-*.md`(7개 ADR). Java 8/Spring Boot 2.7.18 런타임 채택 근거:
  `docs/decisions/phase3-version-compatibility.md`.

**핵심 결과**: 동일 admission ceiling(=50)에서 completion throughput/rejection rate/client
TTFC·stream duration은 세 구현에서 거의 동일했으나, JVM platform-thread measurement-window
peak는 80(P3-A) → 49(P3-B) → 37(P3-C)로 단계적으로, 모든 부하 구간에서 일관되게 감소했다(약
-38.75% / 추가 -24.49% / 전체 -53.75%). 다만 이 thread 절감은 CPU/RSS 절감으로 이어지지 않았다 —
P3-A→P3-B에서 CPU는 오히려 27~32% 증가했고 RSS peak도 세 부하 모두 증가했으며, P3-B→P3-C에서
CPU는 소폭 감소했지만 RSS는 부하에 따라 방향이 갈렸다(상세는 Final Report §16-25).

**Unit 8(JFR/async-profiler 기반 CPU/RSS root-cause profiling)은 Optional Future Work로
남겨졌다** — Phase 3 필수 범위에서 제외됐으며 자동 실행되지 않는다(Final Report §28).

> **Phase 3는 freeze됐다.** 코드/결과/문서를 더 이상 수정하거나 추가 Formal benchmark를 수행하지
> 않는다 — 위 Final Report/Portfolio Summary가 최종본이다. Unit 8 등 향후 profiling을 진행하더라도
> Phase 3 canonical raw/aggregate/Final Report는 덮어쓰지 않고 별도 경로로 분리 저장한다.

## Phase 4 — Concurrency-Model Scalability Boundaries (`gateway-phase4-platform-queue` / `gateway-phase4-virtual-thread` / `gateway-phase4-webflux`) — 완료 (Frozen)

동일한 Java 21(Temurin 21.0.11+10) + Spring Boot 4.1.0 런타임 / native macOS ARM64 host / 고정
SSE workload 위에서 세 아키텍처의 **실제 scalability boundary**를 두 축으로 측정했다:

- **M1** — Platform thread + fixed `ThreadPoolExecutor`(50) + bounded `ArrayBlockingQueue`(500) +
  blocking `HttpURLConnection`
- **M2** — Virtual thread per request + M1과 byte-identical한 blocking `HttpURLConnection`
- **M3** — Spring WebFlux + Reactor Netty `WebClient`(pooled, non-blocking)

**Research question**: "누가 가장 빠른가"가 아니라 — (Closed) 동시에 몇 개의 streaming stream을
SLO·reliability를 유지하며 *보유*할 수 있는가, (Open) 어느 arrival rate까지 backlog 누적 없이
*지속*할 수 있는가. Closed와 Open은 하나의 숫자로 합치지 않는다.

- **Final Report**: [`docs/test-results/phase4/phase4-final-report.md`](docs/test-results/phase4/phase4-final-report.md) (30 sections)
- **Portfolio Summary**: [`docs/portfolio/phase4-summary.md`](docs/portfolio/phase4-summary.md)
- **Canonical datasets**:
  - Closed Formal (N≤640): `docs/test-results/phase4/unit6-closed-formal/` (`formal-aggregate.json`)
  - Extended Closed Formal (N=1120, Phase 4.1): `docs/test-results/phase4/unit6.7-extended-closed-formal/`
  - Open Screening: `docs/test-results/phase4/unit8-open-screening/` (`UNIT8.3-COMPLETION.md`)
  - Open control recalibration: `docs/test-results/phase4/unit8.2-single-host-safe-open-max-recalibration/`
  - Open Formal: `docs/test-results/phase4/unit9-open-formal/` (`formal-aggregate.json`)
- **설계/결정 문서**: [`docs/test-plan/phase4-design.md`](docs/test-plan/phase4-design.md) §13(H4-a~g),
  [`docs/test-plan/phase4-open-formal-protocol.md`](docs/test-plan/phase4-open-formal-protocol.md),
  [`docs/decisions/phase4-open-single-host-ephemeral-headroom.md`](docs/decisions/phase4-open-single-host-ephemeral-headroom.md),
  `docs/decisions/phase4-*.md`.

**핵심 결과**:

| model | Closed (concurrent streams) | Open (arrival rate) |
|---|---|---|
| **M1 (PT+queue)** | Formal-confirmed **MSC [50,56]**, **MRC [350,400]** | Formal-confirmed **MSAR [6,7] req/s** |
| **M2 (Virtual Thread)** | **MSC/MRC ≥ 1120 — CONTROL-CENSORED** | **MSAR ≥ 168 req/s — CONTROL-CENSORED** |
| **M3 (WebFlux)** | **MSC/MRC ≥ 1120 — CONTROL-CENSORED** | **MSAR ≥ 168 req/s — CONTROL-CENSORED** |

- **M2 vs M3 exact ceiling ranking: INCONCLUSIVE** — 둘 다 test rig에 의해 censored됨. `168 req/s`는
  단일-host benchmark rig의 transport-safe 상한(`SAFE_OPEN_MAX_SINGLE_HOST_FINAL`)이며 **architecture
  한계가 아니다.**
- M1의 boundary는 **queue-wait latency**: R=7에서 모든 요청이 rejection 없이 완료되지만 backlog가
  누적되며 TTFC·stream duration p95가 ~40s·~48s로 무너진다(SLO fail). Executor capacity와
  service-quality boundary는 같지 않다.
- **Measurement-integrity 과정 자체가 결과의 일부**: (1) M2 R=160 "VT 실패"가 실제로는 Tomcat
  `maxConnections` 기본값 binding, (2) xk6-sse v0.1.11이 `sse.open()`마다 새 transport를 만들고
  닫지 않아 connection population이 ~8배 부풀려짐(`client.close()` fix로 ~10.5k→~1.28k), (3) R=256
  실패가 k6와 Gateway가 같은 host의 16384-port ephemeral pool을 공유해 발생한 **shared-host
  transport confound**(A∪B ≈ 16362, A∩B ≈ 1)로 확인 — model 결과에서 제외.
- Extended Closed(Phase 4.1): `SAFE_CLOSED_MAX_EXTENDED = 1120`까지 M2/M3 모두 GREEN, boundary
  미발견.

**H4-a~g 가설 판정**(Final Report §21): H4-a NOT SUPPORTED, H4-b SUPPORTED, H4-c NOT SUPPORTED,
H4-d SUPPORTED(characterised), H4-e NOT EVALUABLE(model boundary 미도달), H4-f PARTIALLY SUPPORTED,
H4-g NOT SUPPORTED.

**Optional Future Work**(자동 실행 안 함): Phase 4.2 — 별도 LoadGen host 기반 Extended Open
Boundary(single-host ephemeral-port confound 제거, M2/M3 실제 Open ceiling 탐색; 별도 environment
epoch, 현재 결과를 대체하지 않음); JFR/async-profiler 기반 CPU/RSS root-cause profiling.

> **Phase 4는 freeze됐다.** canonical raw/aggregate/Final Report/Portfolio Summary가 최종본이며 더
> 이상 수정하지 않는다. Phase 4.2·profiling을 진행하더라도 별도 경로로 분리 저장하고 자동 시작하지
> 않는다. Phase 1~3 섹션의 의미는 변경되지 않는다.
