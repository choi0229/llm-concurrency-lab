# llm-concurrency-lab

LLM 스트리밍 Gateway의 concurrency/Executor 전략을 실측 Benchmark로 비교하는 연구용 랩.
Java 8 Spring MVC Baseline으로 시작해(Phase 1), 이후 Virtual Thread 등 다른 동시성 모델과
비교한다(Phase 2+).

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
