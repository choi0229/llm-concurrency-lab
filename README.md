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

## Phase 2 — 설계 완료 (구현 전)

Phase 1 결론("blocking I/O를 유지하는 한 overload 비용은 사라지지 않고 다른 형태로 이동한다")을
바탕으로, 동일 Java 21 환경에서 Platform `ThreadPoolExecutor`와 `VirtualThreadPerTaskExecutor`를
동일 outbound concurrency ceiling(50) 하에서 비교한다. 설계 문서(최종, 코드 미작성):
[`docs/test-plan/phase2-design.md`](docs/test-plan/phase2-design.md). Java 21/Spring Boot 4.x
버전 후보: `docs/decisions/version-compatibility.md` §6.
