# ADR: Phase 3 Absolute Deadline, Timeout, and Cancellation Contract

Status: Accepted (Phase 3 Unit 1 scope; corrected/extended in Unit 2/3 light of implementation
findings — see §0 addendum)
Date: 2026-08-22 (Unit 1), addendum added during Unit 3

## 0. Addendum (Unit 2/3) — upstream completion ≠ request lifecycle completion

Unit 1 froze the absolute-deadline *operator* (§3 below) at the `Flux` level and the
lifecycle/cancellation *contracts* per implementation (§6-§9), but did not explicitly say what this
document says now, having actually built and run both P3-A and P3-B: **upstream completion signals
(HTTP client EOF, Reactor `onComplete`) are not the same instant as "this request may become
`completed`."** Two concrete, measured findings drove this correction:

- **P3-A, Unit 2 Smoke A**: `ChatController.relay()` originally called
  `lifecycle.tryTerminate("completed")` synchronously the instant `HttpURLConnection`'s read loop
  hit EOF. Whatever was still sitting in the (asynchronous) Servlet write buffer — specifically the
  `final` SSE frame, offered microseconds earlier — was discarded by `tryTerminate`'s cleanup before
  it had actually been written. Fixed by `PerStreamWriteChannel.markProducerDone()`: EOF only
  signals "no more frames will be offered"; the write channel itself calls
  `tryTerminate("completed")` once its buffer has actually drained to empty.
- **P3-B, Unit 3 Smoke A**: the same `markProducerDone()` pattern was ported, but exposed a second,
  P3-B-specific version of the same class of problem — see `docs/decisions/
  phase3-mvc-webclient-write-path.md` §6's addendum and `docs/test-results/phase3/
  unit3-p3b-functional/SUMMARY.md`.

**Consequence for this ADR's contracts, made explicit here:** the shared `ScheduledExecutorService`
watchdog (§9) is authoritative and is armed from admission until `RequestLifecycle.tryTerminate()`
actually runs — for *any* outcome, including `completed`. Neither a WebClient `Flux`'s own
`onComplete` signal nor the `AbsoluteDeadline` operator's internal cancellation of the upstream
subscription (§3 below) cancels the watchdog by themselves; only `tryTerminate()`'s own cleanup
does (it cancels the `ScheduledFuture` it was registered under, §9). Concretely: if upstream
completes well before the deadline but the Servlet write executor is slow to actually drain the
last buffered frame, the watchdog is still live and will correctly report `timeout` rather than
letting a late `completed` win — verified as a permanent regression test,
`DeadlineVsWriteDrainRaceTest` in `gateway-mvc-webclient`. This was always the design intent of
having `tryTerminate()` alone own the deadline-task cancellation (rather than, say, cancelling it
from inside a `doOnComplete` on the Reactor chain), but Unit 1 did not call this out explicitly —
this addendum does. Phase 1/2 are not modified by this correction; it only clarifies Phase 3's own
contract based on what was actually built.

## 1. Absolute Total Deadline 의미론

세 구현체 모두 동일한 timeout 의미론을 따른다 — Phase 1/2
`docs/decisions/timeout-semantics.md`의 "단일 absolute deadline" 결정을 그대로 승계한다:

```
startNanos = System.nanoTime()          // 요청 수신 직후, admission 이전
deadlineNanos = startNanos + TOTAL_TIMEOUT
```

이 deadline은 요청의 **전체 lifetime**에 적용한다 — admission, connect, first chunk wait,
inter-chunk wait, upstream streaming, final client write까지 전부. "마지막 event 이후 N초 idle"이
아니라 "이 요청은 시작 후 최대 N초"다.

## 2. `Flux.timeout(Duration)`을 그대로 쓰지 않는 이유

Reactor Core 공식 API 문서(및 `docs/decisions/phase3-version-compatibility.md` §9에서 이미 위험
으로 기록) 확인 결과: `Flux.timeout(Duration timeout)`은 **직전 emission**(또는 첫 아이템이면
구독 시점)으로부터 주어진 Duration 안에 다음 아이템이 없으면 발생하는 **idle/per-signal
timeout**이며, 매 emission마다 타이머가 리셋된다. 이는 §1의 absolute total deadline과 동일하지
않다 — Mock LLM이 `chunkIntervalMs`마다 계속 chunk를 보내는 한 `Flux.timeout(Duration)`은 영원히
발동하지 않을 수 있다. 따라서 P3-B/P3-C는 `CHAT_TOTAL_TIMEOUT_MS` 구현에 이 오퍼레이터를 그대로
쓰지 않는다.

## 3. Absolute Deadline Operator — 실제 코드 검증 (Reactor Core 3.4.34)

지시문 §13이 요구한 대로, 후보를 추측으로 고르지 않고 **실행 가능한 코드로 검증**했다. 4가지
lifecycle 요구사항을 모두 만족해야 한다:

1. subscription 이후 이전 onNext 발생과 무관하게 원래 deadline 시각에 반드시 timeout
2. normal completion 시 timer cancel(leak 없음)
3. error 시 timer cancel
4. client cancel 시 upstream + timer 모두 cancel
5. deadline 시 upstream subscription cancel(leak 없음)

원본 소스/실행 로그는 전부 `docs/test-results/phase3/unit1-capability-spikes/`에 보존했다.

### 3-1. 후보 A — `Flux.merge(data, deadlineErrorMono.flux())` — **기각(버그 발견)**

```java
Mono<Integer> deadlineSignal = Mono.delay(deadline).flatMap(t -> Mono.error(new TimeoutException()));
Flux<Integer> merged = Flux.merge(data, deadlineSignal.flux());
```

`Flux.merge`는 **병합된 모든 소스가 완료되어야** 다운스트림에 완료를 전달한다. `data`가 정상
완료해도 `deadlineSignal`은 여전히 자기 타이머를 기다리는 중이므로, 병합된 Flux는 완료되지
않고 **원래 deadline 시각까지 계속 열려 있다가 결국 타임아웃 에러로 끝난다** — 즉 이미 30ms만에
정상 완료된 요청이 500ms 뒤 timeout 에러로 잘못 보고된다.

실측(`absolute-deadline-candidate-A-merge-result.txt`):

```
[case1 normal-completion] items=3 sawError=true  timerCancelled=true  dataCancelled=false
```

`sawError=true`가 이 버그의 증거다 — 정상 완료(3개 아이템, 데이터 소스는 20ms 간격으로 총
60ms만에 끝남)인데도 최종적으로 에러가 관찰됐다(500ms deadline 타이머가 다 돼서야 종료).
**이 후보는 기각한다.**

### 3-2. 후보 B — `data.takeUntilOther(deadlineErrorMono)` — **기각(리소스 누수 발견)**

```java
Mono<Integer> deadlineSignal = Mono.delay(deadline).flatMap(t -> Mono.error(new TimeoutException()));
Flux<Integer> merged = data.takeUntilOther(deadlineSignal);
```

case1(정상 완료)은 고쳐진다(`sawError=false`, 82ms만에 완료) — `takeUntilOther`는 `data`가 먼저
끝나면 `deadlineSignal` 쪽을 즉시 cancel한다. 문제는 **반대 방향**이다: `deadlineSignal`이
**onError로** 종료될 때, `takeUntilOther`는 `data`(main source)를 cancel하지 않는다(onNext/
onComplete로 종료될 때만 main을 cancel하는 것으로 보임).

실측(`absolute-deadline-candidate-B-takeUntilOther-result.txt` +
`DeadlineSpike-B-leak-proof.java`의 raw tick 카운터):

```
[case2 deadline-fires-first] dataCancelledAt+1150ms=false
rawTicks at termination (~300ms): 3
rawTicks after +2000ms more: 23   <- 계속 증가 = 업스트림이 취소되지 않고 계속 실행됨
```

즉 deadline이 발동해 다운스트림에는 `TimeoutException`이 정상적으로 전달되지만, 업스트림
`Flux.interval`(Mock LLM 스트림에 해당)의 구독은 **살아남아 무한히 계속 실행된다** — 이건
지시문 §13의 "deadline 시 upstream subscription cancel" + "timer leak 없음" 요구사항을 직접
위반하는, WebClient/Mock LLM 실제 연결로 치면 **connection leak**이 되는 심각한 결함이다.
**이 후보도 기각한다.**

### 3-3. 후보 C — `takeUntilOther(completion-only signal)` + `concatWith(deferred error-if-flag)` — **채택**

후보 B의 결함 원인(= "other가 error로 끝나면 main을 cancel 안 함")을 피하기 위해, "other"를
**절대 error로 끝내지 않고 항상 completion으로 끝내며**, 실제 `TimeoutException`은 별도 단계에서
주입한다:

```java
static Flux<Integer> withAbsoluteDeadline(Flux<Integer> data, Duration deadline) {
    AtomicBoolean deadlineFired = new AtomicBoolean(false);
    Mono<Void> deadlineCompletionSignal = Mono.delay(deadline)
            .doOnNext(t -> deadlineFired.set(true))
            .then();                                   // Mono<Void>, 항상 COMPLETE (never error)
    return data.takeUntilOther(deadlineCompletionSignal) // other가 complete로 끝나므로 main도 cancel됨
            .concatWith(Flux.defer(() -> deadlineFired.get()
                    ? Flux.error(new TimeoutException("absolute-deadline-exceeded"))
                    : Flux.empty()));                    // deadline이 아니었으면 아무 것도 추가 안 함
}
```

4개 케이스 전부 통과(`absolute-deadline-candidate-C-final-result.txt`):

```
[case1 normal-completion]     items=3 sawError=false elapsedMs=82   (정상 완료가 timeout으로 오염되지 않음)
[case2 deadline-fires-first]  items=3 error=TimeoutException:...    rawTicksAtTermination=3 rawTicksAfter+2000ms=3  LEAK=false
[case3 downstream-cancel]     dataCancelled=true                    (client cancel 시 upstream도 cancel됨)
[case4 upstream-error-first]  error=IllegalStateException:...       (원본 에러가 그대로 전파됨, 안 삼켜짐)
```

**Phase 3 P3-B/P3-C는 이 패턴(후보 C)을 absolute deadline operator로 채택한다.** 핵심 원리:
"deadline 신호"를 절대 `Mono.error()`로 다운스트림까지 직접 흘려보내지 않고, 반드시
`takeUntilOther`가 "cancel-main-and-complete"로 처리하는 **completion 경로**를 타게 만든 뒤,
실제 에러는 그 뒤에 별도로 이어붙인다. Reactor의 표준 조합 연산자를 조합할 때는 "each operator의
공식 계약을 개별적으로 확인하지 않으면 두 개를 합쳤을 때 조용히 리소스가 새는 조합이 나올 수
있다"는 것이 이번 실측의 핵심 교훈이며, 이는 실제로 실행해보지 않았다면 코드 리뷰만으로는
발견하기 어려운 종류의 결함이었다.

### 3-4. P3-B/P3-C 적용 시 주의사항

- `data`는 Mock LLM SSE 응답 `Flux`(WebClient의 `bodyToFlux`/`.map()` 체인)가 된다. 위 패턴을
  그대로 그 위에 씌운다.
- `deadlineFired`/`AtomicBoolean`은 request마다 독립적이어야 한다(요청 간 공유 상태 금지) —
  request-scoped 지역 변수로 캡처.
- 정확한 outcome 문자열(`timeout` 등)은 `deadlineFired.get()`이 true일 때만 부여한다. 이 값이
  false인데 다른 이유로 에러가 난 경우(§4)와 outcome을 절대 혼동하지 않는다.

## 4. P3-A Absolute Deadline (Blocking)

P3-A는 Reactor를 쓰지 않으므로 §3의 오퍼레이터는 적용되지 않는다. 대신 Phase 1/2가 이미 채택한
"remaining-budget clamp + watchdog" 조합을 그대로 승계한다(`docs/decisions/timeout-semantics.md`,
`ChatController.relay()`):

- 단순히 `setReadTimeout(TOTAL_TIMEOUT)` 하나로 끝내지 않는다 — 여러 번의 `readLine()` 호출이
  반복되면 매 read마다 timeout budget이 다시 주어지기 때문(HttpURLConnection의 read timeout은
  상대 시간이지 절대 deadline이 아님, `docs/decisions/timeout-semantics.md`가 이미 실측으로
  경고한 race).
- 대신:
  1. **remaining-budget clamp**: connect/read timeout을 매번 `min(설정값, deadline까지 남은
     시간)`으로 계산해서 건다(`MockLlmClient.openStream(String, long remainingMs)`가 이미 이
     패턴, `.../upstream/MockLlmClient.java:43-64`) — P3-A도 동일하게 적용.
  2. **per-line deadline 재확인**: `readLine()`으로 한 줄 받을 때마다 `System.nanoTime() >
     absoluteDeadlineNanos`를 재확인한다(`ChatController.relay()` 259-264행과 동일 패턴).
  3. **watchdog(safety backstop)**: deadline 시각에 `HttpURLConnection.disconnect()`를 호출해
     blocking 중인 `readLine()`을 강제로 깨우는 별도 워커(§5 AsyncContext timeout 정책과 같은
     스레드가 이 역할을 겸할 수 있다 — 별도 스레드를 새로 만들 필요는 없다, 이미 §5의
     safety watchdog이 deadline+margin에 발동하도록 설계되므로 여기서 추가 감시자를 따로 두지
     않는다). blocking worker의 interrupt/cancel이 가능하면 함께 수행한다.

세 구현체(P3-A/B/C) 모두 "user-visible absolute lifetime"이 동일해야 한다 — §1의 deadline
계산식(시작 시각 + TOTAL_TIMEOUT)은 P3-A/B/C 전부 같은 값(`CHAT_TOTAL_TIMEOUT_MS`, 동일 env var)
을 공유한다.

## 5. AsyncContext Timeout Policy (P3-A/P3-B)

Primary deadline은 §1의 application absolute deadline **하나**뿐이다. `AsyncContext.setTimeout()`
(Servlet container 자체 timeout)이 이것과 경쟁하면 안 된다 — Phase 1이 실제로 겪은 race(container
timeout과 자체 deadline이 서로 다른 기준점을 잴 때 발생, `docs/decisions/timeout-semantics.md`
"문제" 절)를 Phase 3에서 반복하지 않는다.

**채택**: Application deadline = authoritative, `AsyncContext.setTimeout()` = deadline보다
충분히 긴 **safety watchdog**.

```
AsyncContext.setTimeout(CHAT_TOTAL_TIMEOUT_MS + SAFETY_MARGIN_MS)
```

`AsyncContext`의 `onTimeout`이 실제로 발동하면, 이는 "정상 timeout 흐름"이 아니라 **"safety
watchdog activated"** — 즉 애플리케이션 레벨 absolute deadline 체크가 어떤 이유로든 제때 발동하지
못했다는 신호이므로, 별도 anomaly/metric으로 취급한다(`gateway_watchdog_activated_total`류,
정확한 이름은 metrics-contract ADR). Phase 1/2의 `timeout_before_start`/`deadline_exceeded`
outcome과 같은 자리에 넣지 않는다 — 이 신호가 관찰되면 그 자체가 조사 대상 버그다.

`SAFETY_MARGIN_MS`의 정확한 값은 Unit 5 이전에 확정한다(§write-path ADR §5-2와 동일한 "숫자는
근거 확보 후 freeze, Formal 시작 후 변경 금지" 원칙).

## 6. Cancellation Contract — P3-A

Client disconnect 감지 경로:

- `PrintWriter.checkError()`(write 시도 후 확인, Phase 1/2와 동일 패턴,
  `ChatController.relay()`:252-257)
- `AsyncListener.onError`(Servlet container가 알려주는 별도 경로, Phase 1/2와 동일하게 같은
  outcome bucket으로 취급 — `ChatController.java`:135-149의 주석 "Servlet onError predominantly
  fires for the same class of problem PrintWriter.checkError() detects... treated as the same
  outcome bucket"을 승계)

감지 시 처리 순서(정확히 한 번, idempotent):

```
terminal CAS(outcome=client_disconnect)
  → HttpURLConnection.disconnect()
  → blocking worker interrupt/cancel(가능한 경우)
  → pending servlet write discard(§write-path ADR §4-2 terminal 전이)
  → admission permit release(§admission-connection-pool ADR §1-3)
  → AsyncContext complete/cleanup
```

## 7. Cancellation Contract — P3-B

```
Servlet write path에서 감지(§6과 동일한 checkError/onError 경로)
  → terminal CAS(outcome=client_disconnect)
  → WebClient Subscription/Disposable cancel
  → Reactor Netty upstream request cancel(위 cancel의 자연스러운 결과)
  → write queue discard
  → admission permit release
  → AsyncContext cleanup
```

WebClient subscription(`Disposable`)의 참조는 request lifecycle 객체(예: §write-path ADR의
`PerStreamWriteChannel`과 나란히 두는 request-scoped state)가 안전하게 소유해야 한다 — 동시에
여러 스레드(write executor, Reactor event-loop, Servlet 콜백)에서 접근 가능하므로 이 참조 자체도
CAS/volatile로 보호한다.

cancel이 실제 Mock LLM까지 전파되는지(TCP 연결이 실제로 끊기는지)는 Unit 1의 scope가 아니다 —
Unit 5에서 실측 검증한다(k6 sse-verification-test.js의 intentional-disconnect 패턴,
phase3-design.md §3-6 참고).

## 8. Cancellation Contract — P3-C

WebFlux는 Front disconnect → downstream response `Flux` CANCEL → WebClient upstream subscription
cancellation이 자연스럽게 전파되는 구조를 유지한다 — 이것이 P3-C가 정상 경로에서 별도의 명시적
disconnect-detection 코드를 필요로 하지 않는 이유이자, Experiment B(phase3-design.md §1)가
측정하려는 차이의 일부다.

`doFinally(SignalType)`로 `COMPLETE`/`ERROR`/`CANCEL` 세 가지를 구분하되, **중복 metric/outcome을
기록하지 않는다** — `doFinally`는 §3의 absolute-deadline 오퍼레이터가 만들어내는 신호(정상 완료,
`TimeoutException`, 원본 upstream 에러, 다운스트림 cancel)를 모두 통과시켜 받으므로, 그 신호
종류에 따라 정확히 하나의 outcome만 기록하도록 매핑한다:

| `SignalType` | 그 시점 상태 | outcome |
|---|---|---|
| `ON_COMPLETE` | `deadlineFired==false`로 정상 종료 | `completed` |
| `ON_ERROR`, 에러가 `TimeoutException`(§3에서 주입한 것) | | `timeout` |
| `ON_ERROR`, 에러가 그 외(upstream 실패) | | `upstream_error` |
| `CANCEL` | client가 먼저 끊음 | `client_disconnect` |

Admission permit release도 이 `doFinally` 안에서 **정확히 한 번** 수행한다(§admission-
connection-pool ADR §1-3의 요청당 하나뿐인 CAS 가드를 reactive 코드에서도 동일하게 사용).

## 9. Idempotent Terminal Cleanup — 공통 요구사항 재확인

P3-A/B/C 세 구현체 모두, terminal cleanup(permit release + outcome metric 기록 + 리소스 정리)은
"요청당 정확히 하나의 CAS 가드를 통과한 호출만 실행"하는 패턴이어야 한다 — Phase 1/2
`AsyncRequestState.releaseOnce()`가 원형이다. 이 가드가 없으면 §6/§7의 여러 감지 경로(예:
`checkError()`와 `AsyncListener.onError`가 동시에 발동)가 permit을 이중 반환하거나 outcome
metric을 이중 계상할 수 있다 — Phase 1/2가 이미 이 문제를 실측으로 확인했다
(`docs/decisions/request-outcome-accounting.md` "diagnostic counter의 이중 계상이 실제로 관찰된
사례").
