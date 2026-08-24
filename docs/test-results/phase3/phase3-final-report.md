# Phase 3 Final Report — Blocking MVC vs WebClient vs WebFlux (Java 8, `gateway-mvc-blocking-spring5` / `gateway-mvc-webclient` / `gateway-webflux`)

Status: Final. 27/27 valid Formal runs, 0 invalid, 0 retry. Integrity-reviewed (Unit 7.5) before this
report was written.
Date: 2026-08-24

## 1. Executive Summary

Phase 3는 동일한 Java 8 + Spring Boot 2.7.18 런타임 위에서 세 가지 서버 아키텍처 — P3-A(Spring MVC +
`HttpURLConnection` blocking outbound), P3-B(Spring MVC + WebClient/Reactor Netty non-blocking
outbound), P3-C(Spring WebFlux + WebClient, 완전 reactive) — 를 비교했다. 두 개의 통제된 pairwise
실험(Experiment A = P3-A vs P3-B, Experiment B = P3-B vs P3-C)으로 설계해, 매번 정확히 하나의
변수만 바꿨다: outbound HTTP 실행 모델(A), 그다음 서버 응답 모델(B).

동일한 downstream admission ceiling(`CHAT_ADMISSION_LIMIT=50`) 아래, R3(안정)/R7(근접 과부하)/
R10(명백한 과부하) 세 부하 × 3회 반복 = 27 valid Formal run으로 측정한 결과: **completion
throughput, rejection rate, client TTFC/stream duration은 세 구현체에서 거의 동일**했다. 반면
**JVM platform-thread measurement-window peak는 80(P3-A) → 49(P3-B) → 37(P3-C)로 단계적으로,
그리고 모든 부하 구간에서 일관되게 감소**했다(A→B −38.75%, B→C −24.49%, A→C 전체 −53.75%). 그러나
**CPU/RSS는 이 thread 절감과 같은 방향으로 움직이지 않았다** — A→B에서 CPU는 오히려 약 27~32%
증가했고 RSS peak도 세 부하 모두 증가했다; B→C에서 CPU는 소폭(3~8%) 감소했지만 RSS는 부하에 따라
방향이 갈렸다.

이 결과가 말하는 것은 "Reactive가 무조건 더 빠르다"가 아니다. **동일한 capacity/latency 조건에서
thread-per-request 모델을 걷어낼수록 JVM이 유지하는 platform-thread 수는 확실히, 그리고 예측
가능하게 줄어들지만, 그 절감이 CPU/메모리 절감으로 자동 연결되지는 않는다**는 것이 Phase 3의 핵심
결과다. Phase 2(Virtual Thread)가 이미 관찰한 "thread 절감과 CPU/RSS 절감은 별개"라는 패턴이,
동시성 모델의 완전히 다른 층위(Servlet/Reactive 서버 모델 자체)에서도 재현됐다.

## 2. Research Questions

`docs/decisions/phase3-version-compatibility.md` §0에서 확정한 상위 질문:

> 현재 업무 환경(Java 8 + Spring Framework 4.x)에서 Java 8은 유지하면서 Spring 세대만 현실적으로
> 올렸을 때(Spring 5.3.x/Boot 2.7.x), WebClient/WebFlux를 적용할 수 있는가 — 적용한다면 무엇이
> 달라지는가?

이를 `docs/test-plan/phase3-design.md` §1이 두 개의 독립 실험으로 분리했다:

| 질문 | 실험 근거 | 최종 답 |
|---|---|---|
| 같은 Servlet 응답 경로 위에서 outbound만 blocking→non-blocking으로 바꾸면 무엇이 바뀌는가? (Experiment A) | P3-A vs P3-B, 27-run 중 18-run(R3/R7/R10 × 3), §16-24 | throughput/latency는 불변, platform threads −38.75%, CPU/RSS는 오히려 증가(§23) |
| Servlet 스택 자체를 걷어내고 완전 reactive로 가면 추가로 무엇이 바뀌는가? (Experiment B) | P3-B vs P3-C, 동일 18-run 중 다른 짝, §16-24 | throughput/latency는 불변, platform threads 추가 −24.49%, CPU는 소폭 감소, RSS는 부하 의존적(§24) |
| 두 실험을 합쳤을 때 전체 계단(A→C)은 어떤 그림인가? | §18 | thread 80→37(−53.75%), 그러나 이것이 "capacity가 늘었다"는 뜻은 아님 — capacity는 admission ceiling과 downstream service time이 결정 |

Phase 3는 P3-A/B/C를 "어느 것이 최고인가"로 줄 세우지 않는다 — A vs C의 직접 비교는 이 두 pairwise
실험의 합성으로만 해석하며, 단독 3-way leaderboard 주장은 어디에도 하지 않는다
(`docs/test-plan/phase3-formal-protocol.md` §1).

## 3. Three Architectures

| | 서버 모델 | Outbound client | Write path |
|---|---|---|---|
| **P3-A** | Spring MVC (Tomcat, Servlet `AsyncContext`) | `HttpURLConnection` (blocking) | `PrintWriter`, 전용 blocking outbound worker pool 필요 |
| **P3-B** | Spring MVC (Tomcat, Servlet `AsyncContext`) | `WebClient` (Reactor Netty, non-blocking) | `PrintWriter`, blocking outbound worker 없음 |
| **P3-C** | Spring WebFlux (Reactor Netty server) | `WebClient` (Reactor Netty, non-blocking) | 완전 reactive `Flux` 응답, Servlet 없음 |

P3-A는 Phase 1/2의 `HttpURLConnection` blocking relay 구조(`docs/test-plan/phase3-design.md` §2)를
Java 8 + Boot 2.7.18로 재현한 baseline이다. P3-B는 outbound client만 WebClient로 치환한 것이고,
P3-C는 서버 모델까지 reactive로 바꾼 것이다. 세 구현 모두 `POST /chat/stream` → SSE relay,
`event: {name}\ndata: {payload}\n\n` byte-level 포맷, admission reject 시 `503` +
`{"status":"REJECTED","reason":"executor_saturated"}` 등 client-observable contract는 동일하게
freeze됐다(§4).

이것은 단순한 기술 스택 목록이 아니라 변수 통제 구조다:

- **A → B**: blocking outbound worker pool 제거 (Experiment A의 유일한 통제 변수)
- **B → C**: Servlet write layer(Tomcat, `AsyncContext`, 공유 write executor) 제거 (Experiment B의
  유일한 통제 변수)

## 4. Experiment A / Experiment B — 왜 pairwise인가

**Experiment A** = P3-A vs P3-B. 의도적으로 바뀐 변수: outbound HTTP 실행 모델(blocking →
non-blocking)뿐. Servlet 응답 경로, write executor, admission ceiling, 부하 프로파일은 완전히
동일하게 유지된다(`docs/decisions/phase3-mvc-webclient-write-path.md` §2 — "write channel/write
executor 자체는 P3-A/B 완전히 동일한 구현·동일한 설정값을 공유한다").

**Experiment B** = P3-B vs P3-C. 의도적으로 바뀐 변수: 서버 응답 모델(Servlet AsyncContext →
WebFlux reactive)뿐. Outbound WebClient 구성(`ConnectionProvider`, `LoopResources`)은
byte-identical하게 공유된다(`docs/test-results/phase3/unit5-cross-validation/SUMMARY.md` §12 —
`WebClientConfig.java`가 P3-B/C 사이에 diff 없음).

이 pairwise 구조가 단순 3-way leaderboard보다 중요한 이유: A vs C를 직접 비교하면 "blocking→
non-blocking outbound"와 "Servlet→reactive server"라는 서로 다른 두 변수가 동시에 섞여, 어느 쪽
변화가 어떤 효과를 냈는지 분해할 수 없다. 두 실험을 분리해야만 "무엇이 thread를 줄였는가"를 각
단계별로 귀속시킬 수 있다(§18/§23/§24).

## 5. Environment / Runtime

Phase 3 공통 runtime(`docs/decisions/phase3-version-compatibility.md`, 재확인 근거 §2-§5):

| 구성요소 | 버전 |
|---|---|
| JDK | Azul Zulu 8.96.0.205-CA (`1.8.0_504`, build `1.8.0_504-b01`), macOS aarch64 native |
| Spring Boot | 2.7.18 |
| Spring Framework | 5.3.31 |
| Reactor Core | 3.4.34 |
| Reactor Netty | 1.0.39 |
| Micrometer | 1.9.17 |
| Tomcat (P3-A/B만) | 9.0.83 |
| Gradle | 7.6.6 |
| k6 / xk6-sse | v1.8.0 (go1.26.6, darwin/arm64) / v0.1.11 |

Host: macOS arm64 native, Docker Desktop/Rosetta/QEMU 전부 사용하지 않음(Phase 2 native macOS
채택 원칙 승계, `docs/decisions/phase2-formal-native-macos-environment.md`). JDK 바이너리
SHA-256, Gradle wrapper SHA-256, 실제 resolve된 dependency 버전은 모두 실측으로 검증됐다(추측이
아님, `docs/decisions/phase3-version-compatibility.md` §2/§3/§5) — 매 Formal run의
`environment.json`에도 다시 기록됐다.

## 6. Why Java 8

이 조합(Java 8 + Boot 2.7.18/Spring 5.3.31)은 **2026년 기준 신규 production 스택 추천이 아니다.**
Boot 2.7은 이미 OSS 지원이 종료됐고(2023-11), Boot 3.x/Spring 6+가 현재 세대다.

Phase 3 연구 질문은 애초에 "최신 스택을 추천하라"가 아니라:

> "현재 업무 환경(Java 8 + Spring Framework 4.x)에서 **Java 8은 유지하면서 Spring 세대만 현실적으로
> 올렸을 때**, WebClient/WebFlux를 도입하면 어떤 구조적 변화가 생기는가?"

였다(`docs/decisions/phase3-version-compatibility.md` §0). 즉 이 runtime은 "권장 스택"이 아니라
**Java8을 벗어날 수 없는 조직이 실제로 취할 수 있는 최소 변경 경로를 재현하는 compatibility
benchmark runtime**이다. Phase 2(Java 21 / Spring Boot 4.x)와는 의도적으로 다른, 별도의 실험
축이다.

## 7. Functional Correctness Before Performance

성능을 재기 전에, Unit 2~5는 세 구현체가 동일한 semantics를 실제로 지키는지 코드와 실행으로
검증했다. 발견/수정된 correctness 이슈(전부 실측, 세부는 각 Unit SUMMARY):

- **final SSE frame loss (P3-A)**: `HttpURLConnection` EOF 즉시 `tryTerminate("completed")`를
  부르면, 아직 write channel에 남아있던 마지막 `final` SSE frame이 실제로 쓰이기 전에 폐기됐다.
  `PerStreamWriteChannel.markProducerDone()`으로 "EOF = 더 이상 offer 없음"과 "실제 terminal
  전이 = 버퍼가 실제로 다 drain된 시점"을 분리해 수정(`docs/decisions/phase3-timeout-cancellation.md`
  §0 addendum).
- **upstream EOF ≠ client write completion**: 위와 같은 문제가 P3-B에서도 형태를 바꿔 재발
  — event-loop 스레드에서 `markProducerDone()`의 fast-path가 inline으로
  `RequestLifecycle.tryTerminate()`(Servlet API 호출 포함)를 실행하는 경계 위반이 실제로 관찰됐다.
  공통 write executor로 dispatch하도록 수정(`docs/decisions/phase3-mvc-webclient-write-path.md`
  §3).
- **absolute deadline 의미론**: `Flux.timeout(Duration)`이 절대 deadline이 아니라 idle timeout임을
  실측으로 확인, 3개 오퍼레이터 후보를 실제 실행해 2개에서 실제 버그(정상 완료가 timeout으로
  오염됨 / deadline 발동 후 upstream subscription이 새지 않고 무한히 계속 실행됨)를 발견하고
  세 번째(`takeUntilOther` + `concatWith`) 조합을 채택(`docs/decisions/phase3-timeout-cancellation.md`
  §3).
- **WebClient cancellation**: client disconnect/deadline 발생 시 WebClient `Disposable.dispose()`가
  upstream Mock LLM 구독까지 정상 전파됨을 확인(§7).
- **WebFlux handler-model mismatch**: `@RestController` + `Mono<ServerResponse>` 조합이
  `ServerResponseResultHandler`로 라우팅되지 않아 뷰 이름 해석으로 fallback되는 실제 통합 문제를
  발견 — Spring Framework 결함이 아니라 어노테이션 모델과 `ServerResponse` 타입의 사용 오류였고,
  WebFlux의 네이티브 `RouterFunction` 모델로 전환해 해결(`docs/test-results/phase3/
  unit4-p3c-functional/SUMMARY.md`).
- **request lifecycle start parity**: P3-C의 원래 코드는 `startNanos`/admission/deadline을
  `request.bodyToMono(...)` 구독(비동기) **전에** 계산해, body decode가 동기적으로 먼저 끝나는
  P3-A/B와 기준점이 어긋났다. Lifecycle 시작 전체를 `flatMap(requestBody -> ...)` 내부로 이동해
  정렬(Unit 5 §1, `docs/test-plan/phase3-design.md` §5-1).
- **terminal exactly-once**: 세 구현체 모두 permit 반환 + outcome 기록을 요청당 하나의 CAS 가드로
  통과시키는 idempotent 패턴을 공통 적용(Phase 1/2 `AsyncRequestState.releaseOnce()` 원형 승계,
  `docs/decisions/phase3-admission-connection-pool.md` §1-3/§9).
- **upstream_cancel metric semantic parity**: P3-A/B가 `rejected`(upstream 자체가 시작조차 안 된
  경우)까지 `gateway_upstream_cancel_total`에 잘못 포함시키던 조건식 버그를 수정, P3-C와 동일
  semantic으로 정렬(Unit 5.5 §0).

핵심 메시지: **성능을 재기 전에 세 구현체의 client-observable semantics를 실제로 동일하게 맞췄다.**
이 correctness 작업이 없었다면 이후 Formal 수치 비교 자체가 무의미했을 것이다.

## 8. Admission Semantics

Phase 3에서 가장 중요한 설계 결정 중 하나. Canonical 정의(`docs/decisions/
phase3-admission-semantics-unification.md`):

```
gateway_admission_active = 현재 Mock LLM upstream-call capacity permit을 보유 중인 요청 수
permit acquire: upstream 호출/구독 시작 직전
permit release: upstream 정상 완료 / upstream 에러 / upstream cancel(deadline 또는 client
                disconnect로 인한 전파) 중 최초 발생 시점
```

이는 `gateway_active_streams`(응답 lifecycle이 terminal에 도달하지 않은 요청 수)와 다른 개념이다
— `admission_active == 0 && active_streams > 0`은 정상 상태일 수 있다(느린 client에게 아직
draining 중이지만 upstream은 이미 끝난 경우).

이 재정의가 필요했던 이유: Unit 5.5 Slow Client diagnostic에서 P3-C(WebFlux)는 `terminal:
outcome=completed`(그리고 원래의 permit 해제 시점)가 client가 실제로 데이터 수신을 마친 시점보다
**초 단위로** 먼저 발생하는 반면(60-chunk: 169ms vs 9.32s), P3-A/B는 permit을 Servlet write
drain-complete까지 유지했다 — 즉 동일한 `CHAT_ADMISSION_LIMIT` 값이 세 구현체에서 다른 물리적
자원을 의미하고 있었다. Unit 6에서 이를 "upstream-call-lifetime 한정" 정의로 통일한 뒤에야
Screening/Formal의 admission ceiling=50이 세 구현체에서 같은 것을 의미한다고 말할 수 있게 됐다.

## 9. Timeout / Cancellation Findings

- **P3-A**: absolute deadline 발동 후에도 blocking `HttpURLConnection` read worker가 즉시 회수되지
  않고, remaining-budget clamp + per-line deadline 재확인 + watchdog(`disconnect()` 강제 호출)
  조합으로만 정리된다 — 순수 blocking I/O는 구조적으로 즉각적인 interrupt를 보장할 수 없다는
  한계가 설계에 반영돼 있다(`docs/decisions/phase3-timeout-cancellation.md` §4).
- **P3-B/P3-C**: WebClient cancellation(`Disposable.dispose()`)이 upstream Mock LLM 구독까지
  prompt하게 전파된다(§7/§8).

이 항목들은 functional smoke 관찰이며, Formal performance 수치처럼 사용하지 않는다 — 구조적
finding으로만 취급한다.

## 10. BlockHound Diagnostic (Secondary)

Unit 5.5 D1, **diagnostic 전용 — Screening/Formal 어디에도 BlockHound를 켠 채 측정하지 않았다**
(instrumentation overhead가 측정치를 오염시키기 때문).

- Positive control: 동일 reactor-core 3.4.34/Zulu 8 위에서 `Schedulers.parallel()`에 의도적
  `Thread.sleep()` → `BlockingOperationError` 정상 검출. **PASS.**
- P3-B: 실제 Spring context/WebClient/`ConnectionProvider`로 Normal SSE 요청 실행 —
  BlockHound-observable violation이 이 run에서는 관찰되지 않았다.
- P3-C: 첫 실행에서 실제 violation 발견 — `UUID.randomUUID()` → `SecureRandom` →
  `/dev/urandom` 동기 read가 `reactor-http-nio-*` event-loop 스레드에서 실행됨. Java 8
  `SecureRandom`의 기본 구현이 원인. `ThreadLocalRandom` 기반 requestId 생성으로 교체 후 동일
  diagnostic에서 재관찰되지 않음(clean).

**정확한 표현**: "tested production path에서 BlockHound가 실제 event-loop blocking을 하나
발견했고, 수정 후 동일 diagnostic에서 재관찰되지 않았다." "WebFlux에는 blocking이 없다"는 주장은
하지 않는다 — 이 diagnostic이 테스트한 특정 경로에서 특정 violation을 찾아 고쳤다는 것이 실측된
전부다.

## 11. Slow Client Diagnostic (Secondary)

Unit 5.5 D2, Primary Formal과 분리해서 취급한다 — Screening/Formal 트래픽에 Slow Client 워크로드는
포함되지 않는다.

- **P3-B**: bounded per-stream buffer(32 frame) + write executor 조합. Overflow 시나리오(60×64KB,
  버퍼 용량 초과)에서 `write_overflow`가 ~270ms 내에 정상 발동(fail-fast). Sustained 시나리오
  (20×64KB, 용량 이내)에서는 `servlet_write_duration_seconds_max` = 0.605s까지 관찰돼, 느린
  client의 TCP backpressure가 blocking `PrintWriter.write()+flush()`에 그대로 전파됨을 확인.
- **P3-C**: 이런 버퍼 자체가 없다 — 동일 overflow-equivalent workload가 실패 없이 끝까지 전달됐다
  (client-observed 9.32s). 하지만 `terminal: outcome=completed`(및 당시의 admission permit
  release)가 request 시작 169ms 후에 발생 — client는 아직 9.32초 동안 데이터를 받고 있었다. 이
  finding이 §8의 admission semantics 재정의로 직접 이어졌다.

Slow Client 수치는 Primary Formal 성능 순위로 절대 사용하지 않는다 — 이 섹션의 숫자(9.32s, 0.605s
등)는 §16-24의 Formal Normal-workload 수치와 다른 workload/조건에서 나온 것이며 서로 비교
대상이 아니다.

## 12. Screening (Unit 6)

Rate sweep(R3/R6/R7/R8/R10, 25s pilot)으로 첫 rejection 지점을 확인 — R6(0% 거절)과 R7(~8.5% 거절
전 구현 공통) 사이에서 admission ceiling(50)과 upstream service time(~7.8s)에서 나온 rough
capacity estimate(≈6.4 req/s)와 일치하는 지점을 확인했다. 이로부터 Formal 세 부하점을 선정:

| Candidate | Rate | 근거 |
|---|---|---|
| Stable | R3 | 모든 구현에서 0% 거절, ceiling보다 충분히 낮음 |
| Near-capacity | R7 | 모든 구현에서 일관되게 ~8.5% 거절 — capacity 경계 근접 |
| Over-capacity | R10 | 모든 구현에서 ~35% 거절 — 명백한 overload |

**Screening RSS는 wrong-PID bug로 INVALID였다** — sampler가 `nohup`/`env` 체인 뒤 `$!`로 잡은
PID가 실제 실행 중인 Gateway 프로세스와 일치하지 않아 항상 ~1.5MB라는 비현실적인 값을 냈다(thread
count/CPU는 live `/actuator/prometheus`에서 읽으므로 영향 없음). Formal은 `pgrep -f <jar
basename>`로 health-check 이후 PID를 재확인하는 방식으로 이 버그를 수정했고, Formal Canary(§13)
에서 별도로 재검증했다. **Screening RSS 수치는 Formal RSS와 절대 섞지 않는다.**

## 13. Benchmark Integrity

Phase 2 native macOS harness의 교훈을 승계·확장했다(`docs/decisions/
phase2-formal-native-macos-environment.md`, `docs/test-plan/phase3-formal-protocol.md`):

- Native ARM64 전용, Docker Desktop/Rosetta/QEMU 전부 배제(Mac Docker Desktop이 Phase 2에서 이미
  environment stall로 rejected됨).
- Run마다 fresh Gateway JVM + fresh Mock LLM 프로세스 + fresh in-JVM Micrometer registry — 이전
  run의 상태가 전혀 이어지지 않음.
- Continuous warm-up(120s)→measurement(300s) 단일 k6 프로세스(Phase 2의 double-clock-read 버그
  재발 방지, 동일 `measurement_start_epoch_s` 값을 harness가 그대로 읽어씀).
- Client cohort invariant(`measurement_iterations_started_total == completed + rejected +
  failed_mid_stream + failed_no_event`) 매 run 검증.
- `dropped_iterations == 0` 매 run 검증(k6 자체 provisioning 부족 신호).
- RSS sampler PID를 `pgrep -f <jar basename>`로 health-check 이후 재확인(Formal Canary에서 최초
  검증, §Screening RSS bug의 직접 대응책).
- Counter plateau/log-gap 20초 초과 탐지(`counter-plateau-check.json`) — 27개 run 전부
  "no gaps >20s detected".
- Postflight: 모든 gauge(admission/active-streams/upstream-active + implementation-specific)
  0으로 복귀 확인.
- **27/27 valid, 0 invalid, 0 retry.**

**Canary(`p3a-r3-canary`)는 canonical dataset에 포함되지 않는다** — pipeline 검증 목적으로만
실행됐고, `aggregate_phase3_formal.py`가 파일명 패턴(`p3[abc]-r(3|7|10)-run[123]`)으로 자동
제외한다.

## 14. Formal Matrix

```
3 configs (P3-A, P3-B, P3-C) x 3 loads (R3, R7, R10) x 3 repeats = 27 valid measured runs
```

실행 순서는 config rotation(A,B,C / B,C,A / C,A,B)으로 어떤 config도 매 repeat에서 항상 먼저/나중에
실행되지 않도록 설계됐다(`docs/test-plan/phase3-formal-protocol.md` §4):

```
Repeat 1 (A,B,C):  A-R3 B-R3 C-R3 / A-R7 B-R7 C-R7 / A-R10 B-R10 C-R10
Repeat 2 (B,C,A):  B-R3 C-R3 A-R3 / B-R7 C-R7 A-R7 / B-R10 C-R10 A-R10
Repeat 3 (C,A,B):  C-R3 A-R3 B-R3 / C-R7 A-R7 B-R7 / C-R10 A-R10 B-R10
```

Unit 7.5 Integrity Review가 `scripts/aggregate_phase3_formal.py`를 raw 27-run 위에서 read-only로
재실행해 실제 `process_start` 순서가 위 frozen 순서와 정확히 일치함을 재확인했다.

## 15. Canonical Quantitative Table

Source of truth: **`docs/test-results/phase3/unit7-formal/aggregate-result.json`**
(`scripts/aggregate_phase3_formal.py`, per-cell n=3 median/min/max/CV + Experiment A/B pairwise
diff). 아래 수치는 이 파일에서 직접 읽은 것이며 기억으로 재계산하지 않았다.

| Load | Config | Throughput (req/s) | Rejection | TTFC p50/p95/p99 (s) | Duration p50/p95/p99 (s) | Threads | CPU (avg cores) | RSS peak (MB) |
|---|---|---|---|---|---|---|---|---|
| R3 | A | 3.000 | 0.0% | 1.004 / 1.006 / 1.006 | 7.869 / 7.878 / 7.882 | 80 | 0.0262 | 461.3 |
| R3 | B | 3.000 | 0.0% | 1.005 / 1.007 / 1.008 | 7.870 / 7.879 / 7.884 | 49 | 0.0340 | 504.4 |
| R3 | C | 3.000 | 0.0% | 1.005 / 1.007 / 1.007 | 7.870 / 7.880 / 7.883 | 37 | 0.0329 | 521.3 |
| R7 | A | 6.260 | 10.66% | 1.003 / 1.005 / 1.006 | 7.863 / 7.876 / 7.881 | 80 | 0.0423 | 503.5 |
| R7 | B | 6.267 | 10.61% | 1.004 / 1.006 / 1.007 | 7.864 / 7.877 / 7.884 | 49 | 0.0560 | 529.5 |
| R7 | C | 6.260 | 10.67% | 1.004 / 1.006 / 1.007 | 7.863 / 7.877 / 7.882 | 37 | 0.0522 | 468.5 |
| R10 | A | 6.327 | 36.72% | 1.003 / 1.005 / 1.006 | 7.859 / 7.884 / 7.889 | 80 | 0.0445 | 503.5 |
| R10 | B | 6.337 | 36.72% | 1.004 / 1.006 / 1.007 | 7.860 / 7.884 / 7.889 | 49 | 0.0564 | 535.6 |
| R10 | C | 6.327 | 36.73% | 1.004 / 1.006 / 1.007 | 7.861 / 7.885 / 7.890 | 37 | 0.0517 | 478.2 |

Threads의 CV는 모든 cell에서 0(3회 반복 전부 동일값) — 이 metric의 신뢰도가 가장 높다. Throughput/
rejection CV는 0.03~0.4% 수준으로 매우 낮다. CPU CV는 0.4~3.4%. RSS만 일부 cell에서 CV
14~26%로 높다(§20).

## 16. Throughput / Rejection

R3: 3.0 req/s, 거절 0% — 세 구현 동일. R7: 약 6.26 req/s, 거절 약 10.6~10.7% — 세 구현 간 차이
0.1pp 미만. R10: 약 6.33 req/s, 거절 약 36.7% — 세 구현 간 차이 0.02pp 미만.

정확한 해석: **"동일한 downstream admission ceiling(=50)을 적용한 조건에서, 세 구현의
completion/rejection curve가 실제로 거의 동일하게 재현됐다."** 동일 ceiling이 유사한 결과를
예상하게 만드는 강한 구조적 조건이긴 하지만, implementation overhead·에러 처리·숨은 큐 차이로
curve가 갈라질 가능성은 이론상 남아 있었다 — 실측이 그 가능성을 배제하고 유사성을 확인해준
것이지, "설계상 당연한 결과"로 가정하고 넘어간 것이 아니다.

## 17. Client Latency

Authoritative source: k6 client-side TTFC / total stream duration, `completed` outcome only
(`docs/decisions/phase3-metrics-contract.md` §8-1). 세 구현 차이는 모든 load/percentile에서 5ms
미만 — 실질적으로 구분 불가능하다.

서버 측 `gateway_first_chunk_relay_seconds`/`gateway_stream_duration_seconds`는 diagnostic
전용이다 — P3-A/B는 write executor가 `PrintWriter.write()+flush()`를 실제로 실행한 직후를 재고,
P3-C는 response `Flux`의 `.doOnNext()`(WebFlux 인코더/Reactor Netty 쓰기 이전)를 잰다. 물리적
경계가 구조적으로 다르므로 cross-implementation 비교에는 쓰지 않는다(Unit 5 §7/§8).

## 18. Platform Thread — Main Finding

Phase 3에서 가장 신뢰도 높고 가장 중요한 정량 결과. `platform_threads_window_peak`
(measurement window 내 `jvm_threads_live_threads`의 max, JVM 전체 live platform-thread peak — 특정
executor thread만이 아니다):

```
A = 80    B = 49    C = 37     (R3/R7/R10 전부 동일, CV=0)

A -> B: -31  (-38.75%)
B -> C: -12  (-24.49%)
A -> C: -43  (-53.75%)   [두 pairwise 실험의 합성 수치, 단독 A vs C 주장 아님]
```

세 부하 구간 모두에서 완전히 동일한 절대값·상대 비율이 재현됐다 — 부하(요청 동시성)와 무관하게
구조적 thread 절감이라는 뜻이다.

## 19. CPU

`cpu_avg_cores = mean(process_cpu_usage) * available_processors`(Micrometer gauge 기반, §21
correction — `process_cpu_seconds_total`이 이 스택에서 노출되지 않아 채택된 보정 공식,
`docs/test-plan/phase3-formal-protocol.md` §21).

```
A -> B: R3 +29.5%   R7 +32.2%   R10 +26.9%   (세 부하 모두 증가)
B -> C: R3  -3.0%   R7  -6.8%   R10  -8.4%   (세 부하 모두 소폭 감소)
```

절대 CPU 사용량 자체는 매우 낮다(0.026~0.056 core, 10-core 머신에서). 원인 attribution은 이번
Phase 3의 범위 밖이다 — "event-loop busy-polling 때문에", "Reactor scheduling overhead 때문에"
같은 JFR/profiler 없이 확정하는 표현은 쓰지 않는다. 가능한 설명은 §28 Future Work의 가설로만
남긴다.

## 20. RSS

RSS는 CPU/threads와 별도 섹션으로 다룬다 — heap이 아니라 OS-level resident physical memory이며,
`ps -o rss=`로 1Hz 샘플링한 measurement-window peak의 median이다.

```
A -> B: R3 +9.3%   R7 +5.2%   R10 +6.4%    (세 부하 모두 증가)
B -> C: R3 +3.3%   R7 -11.5%  R10 -10.7%   (R3는 증가, R7/R10은 감소)
```

**CV caveat**: RSS peak의 cell별 CV는 B-R3 23.8%, C-R7 25.6%, C-R10 24.8%, A-R10 17.3%로
threads(CV=0)나 CPU(CV<3.4%)보다 훨씬 크다. 따라서 위 %는 방향성으로만 해석해야 하며, 특히
R3의 A→B(+9.3%)나 B→C(+3.3%)처럼 한 자릿수 %대 차이는 run-to-run 변동과 뚜렷이 구분되지 않을 수
있다 — "Reactive가 memory를 줄인다"는 결론은 이 데이터로 내리지 않는다.

## 21. Heap / GC

Heap peak(secondary, `jvm_memory_used_bytes{area="heap"}`)는 cell마다 158~250MB 범위이고 CV가
2~57%로 매우 높아(특히 B/C 다수 cell) 정밀한 cross-implementation 비교 대상으로 쓰지 않는다 — RSS
와 마찬가지로 JVM-managed 논리 메모리이고 물리 RSS와 별개로 움직인다(§23 protocol).

GC pause 총합(measurement window, 300s):

```
                R3      R7      R10
A  median(s)   0.015   0.023   0.030
B  median(s)   0.017   0.026   0.029 (max 0.042)
C  median(s)   0.015   0.033   0.038
```

**정정된 문구**: GC pause 총합은 모든 cell에서 수십 ms 수준(약 15~38ms 중앙값, 일부 run은
42ms까지)으로 작았으며, 이번 300s measurement window에서 GC가 주요 병목이라는 증거는 없었다.
("30ms 미만" 같은 hard threshold 주장은 C-R10 median 38ms와 충돌하므로 쓰지 않는다.)

## 22. Implementation-specific Resource

- **P3-A blocking outbound executor**: `active_window_peak` — R3=24, R7=50, R10=50(세 repeat 전부
  동일). `rejected_delta=0` 모든 run. Admission ceiling(50)과 executor max(50)가 일치하므로
  admitted된 요청이 이 executor에서 거부될 수 없다는 §admission-connection-pool ADR §2-3의
  설계 invariant가 실측으로도 위반 없이 확인됐다.
- **P3-A/B Servlet write path**: `active`/`queue_depth`/`buffered_frames` 모두 0~4 사이의 낮은
  값. `write_overflow_delta=0` 모든 run — Normal workload에서 write path가 병목이 되지 않았다는
  뜻이다.
- **P3-B/C Reactor Netty connection pool**: `pending_connections_window_peak = 0` 모든 run —
  connection pool이 hidden limiter가 됐다는 증거 없음(§Admission Connection Pool ADR §3-1
  invariant 충족).

## 23. Experiment A — Final Answer (P3-A → P3-B)

Blocking `HttpURLConnection` → WebClient 전환 결과:

- throughput/rejection: 거의 동일(§16)
- client TTFC/stream duration: 거의 동일, 모든 percentile에서 5ms 미만 차이(§17)
- platform threads: 80 → 49, **−38.75%**, 모든 부하 구간에서 동일 폭(§18)
- CPU: 오히려 **+27~32% 증가**(§19)
- RSS peak: median 기준 세 부하 모두 **증가**(+5~9%, 단 §20의 CV caveat 적용)

**"WebClient가 더 빠르다"는 결론이 아니다.** 처리량과 지연은 admission ceiling과 downstream
service time에 의해 이미 결정돼 있고, blocking outbound worker의 유무가 그것을 바꾸지 않았다.
실제로 바뀐 것은 **JVM이 유지하는 platform-thread footprint**뿐이다 — 그 대신 CPU/메모리 비용이
줄지 않고 오히려 소폭 늘었다는 trade-off가 함께 관찰됐다.

## 24. Experiment B — Final Answer (P3-B → P3-C)

WebClient outbound는 그대로 두고 Servlet 응답 계층(Tomcat, `AsyncContext`, write executor)을
제거한 결과:

- throughput/rejection: 거의 동일(§16)
- client latency: 거의 동일(§17)
- platform threads: 49 → 37, **추가 −24.49%**, 모든 부하 구간에서 동일 폭(§18)
- CPU: **소폭 감소**(−3~8%)(§19)
- RSS peak: **부하에 따라 방향이 갈림** — R3는 증가(+3.3%), R7/R10은 감소(−11~12%), 다수 cell CV
  높음(§20)

**"WebFlux가 모든 자원을 줄였다"는 결론이 아니다.** 가장 명확하고 일관된 이득은 thread footprint의
추가 감소뿐이며, CPU 감소는 소폭이고 RSS는 방향조차 일관되지 않는다.

## 25. H3 Hypothesis Verdicts

Frozen wording(`docs/test-plan/phase3-formal-protocol.md` §28)을 그대로 인용하고 판정한다.

- **H3-a**: "P3-B's `platform_threads_window_peak` is lower than P3-A's at the same load." →
  **SUPPORTED**. 49 < 80, 모든 부하에서.
- **H3-b**: "P3-A and P3-B have similar completion/rejection capacity at the same load, under the
  same (now-unified) admission/downstream conditions." → **SUPPORTED**. Throughput 차이 <0.2%,
  rejection 차이 <0.1pp(§16).
- **H3-c**: "P3-C's `platform_threads_window_peak` is lower than P3-B's at the same load." →
  **SUPPORTED**. 37 < 49, 모든 부하에서.
- **H3-d**: "P3-B and P3-C have similar completion/rejection capacity at the same load, under the
  same admission/downstream conditions." → **SUPPORTED**. Throughput/rejection 차이 §16과 동일
  수준으로 근접.
- **H3-e**: "Client TTFC and stream duration on the Normal workload do not differ substantially
  across the three implementations at a given load." → **SUPPORTED**. 모든 load/percentile에서
  차이 5ms 미만(§17).
- **H3-f**: "A change in execution model ... does not necessarily reduce CPU or RSS — each is
  reported as measured, not assumed to move in the 'obviously better' direction." →
  **SUPPORTED**. A→B에서 CPU +27~32%, RSS +5~9% 증가가 실측됐다(§19/§20) — execution model
  변화가 "당연히 더 나은" 방향으로 자원을 줄이지 않음을 데이터가 직접 보여준다.

## 26. What Phase 3 Did NOT Prove

다음 주장은 이 Phase 3 데이터로 하지 않는다:

- WebFlux가 항상 MVC보다 빠르다.
- WebClient가 항상 CPU 효율적이다.
- Reactive가 항상 메모리를 줄인다.
- 이 결과가 모든 workload(다른 payload 크기, 다른 chunk 패턴, 다른 admission ceiling)에서 동일하게
  재현된다.
- 실제 LLM/GPU downstream(Mock LLM이 아닌)에서도 동일한 결과가 나온다.
- Slow Client diagnostic 결과가 Formal Normal-workload 결과와 같은 의미다 — 두 데이터셋은 서로 다른
  workload에서 나왔고 섞지 않는다.
- CPU/RSS 차이의 root cause가 규명됐다 — §19/§20에서 명시했듯 원인 attribution은 하지 않았다(§28).

## 27. Limitations

- Mock LLM(`mock-llm-fastapi`), 실제 LLM/GPU downstream 아님.
- 고정된 Normal SSE workload(`firstChunkDelayMs=1000, chunkIntervalMs=200, chunkCount=35,
  chunkSizeBytes=64`) — 다른 payload 프로파일은 테스트하지 않음.
- Admission ceiling=50 고정 — 다른 ceiling에서의 거동은 미측정.
- Native macOS ARM64, single host — 다른 OS/아키텍처에서 재현되는지 확인 안 됨.
- Cell당 3회 반복, 300s measurement — threads/throughput은 CV가 매우 낮아 신뢰도 높지만, RSS/heap
  처럼 CV가 큰 metric은 더 많은 반복이 있어야 정밀한 결론이 가능하다.
- CPU metric은 `process_cpu_usage`(gauge) 기반 보정 공식 — `process_cpu_seconds_total`(cumulative
  counter)이 이 스택에서 노출되지 않아 채택된 대안이다(§19).
- 실제 production 네트워크(다중 홉, 실제 인터넷 지연)가 아니라 localhost(`127.0.0.1`) 통신.
- Java 8 + Boot 2.7 compatibility runtime — 최신 스택(Boot 3.x/Spring 6+, Java 21+)에서의 결과가
  아니다(§6).
- Slow Client는 diagnostic 전용이며 Formal 성능 수치가 아니다(§11).
- CPU/RSS 증가·변화의 root-cause profiling(JFR/async-profiler)은 이번 Phase 3에서 수행하지 않았다
  (§28).

## 28. Future Work — Unit 8 (Deferred / Optional)

Unit 7.5 Integrity Review 이후, Unit 8(JFR/async-profiler 기반 CPU/RSS root-cause profiling)은
**Phase 3 필수 범위에서 제외하고 Deferred / Optional Future Work로 기록한다.** 근거:

- 27/27 Formal run valid, raw/aggregate consistency 검증 완료.
- Experiment A/B 모두 정량 결과 확보, H3-a~H3-f 전부 판정 가능.
- 미해결 validity anomaly 없음.
- H3-f가 애초에 "execution model 변화가 CPU/RSS를 반드시 줄이지 않는다"를 중립 가설로 이미
  freeze했고(§25), 이번 실측 결과는 그 가설과 정확히 부합한다 — 예측 밖의 이상 현상이 아니라
  애초에 대비된 결과다.
- CPU/RSS의 원인 attribution은 Phase 3 연구 질문(§2)의 필수 조건이 아니다 — "무엇이 달라지는가"를
  실측 보고하는 것이 범위였고, "왜 그런가"의 내부 메커니즘 규명은 별도 연구다.

이번 Final Report는 **검증되지 않은 원인을 추측해서 확정하지 않는다.** JFR/async-profiler로
WebClient/Reactor Netty의 CPU/RSS overhead 원인(event-loop 스케줄링, 버퍼 할당 패턴, GC 압력 등
가능한 가설)을 분석하는 것은 향후 별도 심화 실험으로 남긴다. Phase 4를 임의로 만들지 않는다 — Unit
8은 Phase 3 canonical Formal 결과를 덮어쓰지 않는 별도 future diagnostic path로만 진행될 수 있다.

## 29. Phase 1 → 2 → 3 Story

- **Phase 1**(Java 8, Platform Thread + blocking I/O, 3가지 Executor 전략): overload 비용은
  사라지지 않고 queue/reject/thread 중 어딘가로 반드시 이동한다.
- **Phase 2**(Java 21, 동일 blocking I/O 구조 위에서 Platform Thread vs Virtual Thread):
  Virtual Thread는 platform-thread footprint를 크게(근접/과부하 구간 약 50~52%) 줄였지만, 그
  절감이 RSS/CPU 절감으로 일관되게 이어지지는 않았고, capacity/admission 문제 자체는 해결하지
  못했다 — admission control을 대체하지 않는다.
- **Phase 3**(Java 8 동일 runtime, blocking outbound → WebClient → WebFlux로 execution model
  변경): capacity/latency는 admission ceiling과 downstream service time에 의해 거의 동일하게
  유지됐지만, platform-thread footprint는 80 → 49 → 37로 단계적으로 감소했다. 그리고 Phase 2와
  동일한 패턴 — thread 절감이 CPU/RSS 절감과 자동으로 함께 가지 않는다 — 가 완전히 다른 층위의
  동시성 모델(스레드 모델이 아니라 서버/클라이언트 실행 모델)에서도 재현됐다.

세 Phase를 관통하는 메시지: **동시성 모델(Executor 전략, Virtual Thread, reactive 실행 모델)은
"기다림의 비용"이 어디에 어떤 형태로 나타나는지를 바꿀 수 있지만, downstream capacity 자체를
만들어내지는 않는다.** Phase 1/2/3의 절대 수치는 서로 다른 런타임/부하 프로파일에서 나왔으므로
직접 비교하지 않는다 — 위 메시지는 세 Phase 각각의 독립적인 결론들이 가리키는 공통 방향이다.

## 30. Canonical Artifacts / Freeze

- **Final Report**: 이 문서.
- **Portfolio Summary**: `docs/portfolio/phase3-summary.md`.
- **Canonical raw dataset**: `docs/test-results/phase3/unit7-formal/<27 canonical run dirs>/`
  (canary 제외).
- **Canonical aggregate**: `docs/test-results/phase3/unit7-formal/aggregate-result.json`
  (`scripts/aggregate_phase3_formal.py`).
- **Design/decision 문서**: `docs/test-plan/phase3-design.md`, `phase3-formal-protocol.md`,
  `docs/decisions/phase3-*.md`(7개 ADR).
- **Functional/diagnostic evidence**: `docs/test-results/phase3/unit1-capability-spikes/`
  ~ `unit6-screening/`.

**Phase 3 = COMPLETED / Frozen.** 이후 Formal raw / aggregate / 이 Final Report는 수정하지
않는다. Unit 8(JFR/async-profiler) 등 새로운 profiling을 진행하더라도 Phase 3 canonical Formal
결과를 덮어쓰지 않고, 별도 future diagnostic 경로(`docs/test-results/phase3/` 하위 새 디렉터리)로
분리 저장한다.
