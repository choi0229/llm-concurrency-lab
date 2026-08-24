# Phase 3 Portfolio Summary — LLM Concurrency Lab

Source: `docs/test-results/phase3/phase3-final-report.md`. 이 문서는 그 보고서의 요약을 재사용
가능한 형태로 분리한 것이다 — 새 주장을 추가하지 않는다. 숫자는 전부
`docs/test-results/phase3/unit7-formal/aggregate-result.json`(27 valid native macOS ARM64 run,
canary 제외) 기준 median 값이다.

## A. README용 (8~12줄)

동일한 Java 8 + Spring Boot 2.7.18 런타임 위에서 Spring MVC + `HttpURLConnection`(blocking
outbound, P3-A), Spring MVC + WebClient(non-blocking outbound, P3-B), Spring WebFlux + WebClient
(완전 reactive, P3-C) 세 아키텍처를 비교한 Benchmark. Outbound 실행 모델(Experiment A: P3-A vs
P3-B)과 서버 응답 모델(Experiment B: P3-B vs P3-C)을 각각 하나의 변수만 바꾸는 pairwise 실험으로
분리해, R3(안정)/R7(근접 과부하)/R10(과부하) × 3회 반복 = 27 valid Formal run을 측정했다. 동일
admission ceiling(=50) 아래 completion throughput·rejection rate·client TTFC/stream duration은
세 구현에서 거의 동일했지만, JVM platform-thread measurement-window peak는 80(P3-A) →
49(P3-B) → 37(P3-C)로 단계적으로, 그리고 모든 부하 구간에서 일관되게 감소했다(약 -38.75% /
-24.49% / 전체 -53.75%). 다만 이 thread 절감이 CPU/RSS 절감으로 이어지지는 않았다 — P3-A→P3-B에서
CPU는 오히려 27~32% 늘었고 RSS peak도 세 부하 모두 증가했으며, P3-B→P3-C에서 CPU는 소폭
감소했지만 RSS는 부하에 따라 방향이 갈렸다. "Reactive가 무조건 빠르다"가 아니라 "thread 모델을
걷어낼수록 platform-thread footprint는 확실히 줄지만 CPU/메모리 절감은 자동으로 따라오지 않는다"는
것이 핵심 결과다.

## B. 이력서 / 포트폴리오 Bullet (3~5개)

- Java 8 동일 런타임에서 MVC+Blocking → MVC+WebClient → WebFlux+WebClient를 27회 Formal
  Benchmark(3 config × 3 load × 3 repeat)로 비교해, 처리량/지연을 유지하면서 JVM platform-thread
  peak를 80→49→37개로 단계적으로(-38.75%, 추가 -24.49%, 전체 -53.75%) 감소시켰고, CPU/RSS
  trade-off까지 실측해 동시성 모델 선택 기준을 정리.
- Outbound HTTP 실행 모델(blocking→WebClient)과 서버 응답 모델(Servlet→WebFlux) 변화를 pairwise
  통제 실험으로 분리 설계해, 각 변화가 platform-thread footprint에 정확히 얼마씩 기여하는지
  귀속시킴 — 단순 A/B/C leaderboard가 아니라 두 개의 독립 변수 실험으로 결과를 해석.
- Platform-thread 절감이 CPU/RSS 절감으로 자동 연결되지 않는다는 반직관적 결과를 발견(A→B에서
  thread -38.75%인데 CPU +27~32%, RSS도 세 부하 모두 증가) — "실행 모델을 바꾸면 모든 자원이
  줄어든다"는 단순화된 결론을 데이터로 반박.
- Admission 의미론이 세 아키텍처에서 실제로 같은 물리적 자원을 보호하는지 감사 — Slow Client
  diagnostic으로 WebFlux의 admission permit 해제가 client 물리적 수신 완료보다 초 단위로 선행하는
  현상을 발견하고, upstream-call-lifetime 기준으로 admission semantics를 재정의·통일.
- BlockHound instrumentation으로 WebFlux 경로에서 실제 event-loop blocking(`UUID.randomUUID()`
  → `SecureRandom` → `/dev/urandom` 동기 read)을 발견·수정하고, 수정 전후 diagnostic 결과를
  "관측되지 않았다"와 "존재하지 않는다"를 구분해 정확히 보고.

## C. 면접 설명용 스크립트 (1~2분)

"Phase 1, 2는 Java 8과 21에서 Executor 전략과 Virtual Thread를 비교했는데, Phase 3는 질문을
바꿨습니다 — 스레드 모델이 아니라 서버 아키텍처 자체를 바꾸면 뭐가 달라지는가. Java 8은 그대로
두고, Spring MVC에 blocking HTTP 클라이언트를 쓰는 구조에서 시작해서, outbound만 WebClient로
바꾼 버전, 그리고 서버 자체를 WebFlux reactive로 바꾼 버전까지 세 단계를 만들었습니다. 중요한
건 한 번에 하나씩만 바꿨다는 겁니다 — outbound 클라이언트만 바꾼 실험 하나, 서버 모델만 바꾼
실험 하나, 이렇게 두 개의 독립된 pairwise 실험으로 쪼갰어요. 그래야 뭐가 어떤 효과를 냈는지 헷갈리지
않으니까요.

동일한 admission 제한(50개) 아래에서 27번 Formal 측정을 돌렸더니, 처리량이나 지연 시간은 세
구현 모두 거의 똑같았습니다. 근데 JVM이 유지하는 platform thread 수는 확실히 달랐어요 — blocking
버전이 80개, WebClient로 바꾸니까 49개, WebFlux까지 가니까 37개로, 부하 크기와 상관없이 항상
같은 비율로 줄었습니다. 여기까지는 예상대로였는데, 진짜 흥미로운 발견은 그 다음이었습니다 — 스레드
수는 줄었는데 CPU는 오히려 늘었어요, 첫 번째 단계에서 27~32%나. 메모리도 마찬가지로 늘었고요.
Phase 2에서 Virtual Thread 봤을 때도 비슷한 패턴을 봤었는데, 이번엔 완전히 다른 계층 — 스레드
모델이 아니라 서버 실행 모델 — 에서도 똑같은 패턴이 재현된 겁니다.

그래서 결론은 'reactive가 무조건 더 낫다'가 아니라, '실행 모델을 바꾸면 JVM이 유지하는 thread
footprint는 확실히 줄일 수 있지만, 그게 CPU나 메모리 절감을 보장하지는 않는다'는 겁니다. 그리고
측정을 시작하기 전에 correctness 작업도 꽤 했어요 — 예를 들어 WebFlux에서 admission 허가가
실제로 클라이언트가 데이터를 다 받기 몇 초 전에 풀려버리는 걸 발견해서, admission이 정확히 뭘
보호하는 자원인지 세 구현 모두 같은 기준으로 다시 정의했습니다. 그리고 BlockHound로 WebFlux의
event-loop 스레드에서 실제로 blocking 호출(UUID 생성 로직)이 발생하는 것도 하나 잡아서 고쳤고요.
성능을 재기 전에 세 구현이 진짜 같은 것을 하고 있는지부터 확인한 게 이번 Phase의 절반이었습니다."

## 핵심 정량 수치 (근거: `phase3-final-report.md`)

### Platform threads (27 valid run, median, CV=0 — 모든 부하에서 완전히 동일)

- **A=80 → B=49 → C=37**
- A→B: **-31 (-38.75%)**
- B→C: **-12 (-24.49%)**
- A→C 전체(보조 수치, 두 pairwise 실험의 합성): 80→37, 약 **-53.75%**

### Throughput / Rejection / Latency — 세 구현 거의 동일

- R3: 3.0 req/s, reject 0%
- R7: 약 6.26 req/s, reject 약 10.6~10.7%
- R10: 약 6.33 req/s, reject 약 36.7%
- Client TTFC/stream duration: 모든 load/percentile에서 구현 간 차이 5ms 미만

### CPU / RSS — trade-off, 일관된 절감 아님

- CPU: A→B **+27~32% 증가**, B→C **-3~8% 감소**(절대값은 0.026~0.056 core로 낮음)
- RSS peak(median): A→B 세 부하 모두 **증가**(+5~9%), B→C는 **R3 증가(+3.3%) / R7·R10 감소
  (-11~12%)**로 방향이 갈림 — 일부 cell CV 14~26%로 높아 정밀한 효과로 과장하지 않는다.
- GC pause 총합: 300s window에서 모든 cell 수십 ms 수준(15~38ms median), 병목 증거 없음.

과장 금지: 위 CPU/RSS 숫자는 원인이 아니라 관측치다 — root-cause profiling(JFR/async-profiler)은
Phase 3 범위에서 의도적으로 제외했고(Unit 8, Deferred/Optional Future Work), "Reactive가 메모리를
줄인다"거나 "WebClient가 CPU를 더 쓴다는 게 이벤트 루프 오버헤드 때문"이라는 원인 설명은 이
Phase의 결론에 포함하지 않는다.
