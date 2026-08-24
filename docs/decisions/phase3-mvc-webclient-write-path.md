# ADR: Phase 3 P3-A/P3-B Servlet Write Path

Status: Accepted (Phase 3 Unit 1 scope)
Date: 2026-08-22

## 1. AsyncContext + PrintWriter를 유지하는 이유

P3-A/P3-B는 둘 다 `SseEmitter`로 전환하지 않고 **Servlet `AsyncContext` + `PrintWriter`**를
유지한다.

근거:

1. **Phase 1/2와의 연속성.** `gateway-mvc-java21/.../chat/ChatController.java`가 이미 이 경로로
   검증됐다(§3-1의 relay 코드). Experiment A(P3-A vs P3-B)의 유일한 통제 변수는 "outbound client가
   blocking이냐 non-blocking이냐"여야 한다 — write path 자체를 `SseEmitter`로 바꾸면 그 축도 함께
   바뀌어 실험이 오염된다.
2. **write 실행 방식을 P3-A/B가 동일하게 통제할 수 있어야 한다.** `SseEmitter`는 내부적으로 자체
   스레드/콜백 모델을 갖고 있어 "공통 bounded write executor 위에서 stream별 serialization"이라는
   Unit 1의 설계(§3)를 직접 얹기 어렵다. `PrintWriter` + 직접 관리하는 executor 조합이 이 설계를
   그대로 구현할 수 있는 최소 구성이다.

## 2. 공통 구조

```
upstream event
  → per-request serialized write channel (request-local bounded queue)
  → shared bounded Servlet write executor
  → PrintWriter
  → Front
```

- **P3-A**: `HttpURLConnection` blocking read worker(§4 blocking outbound executor) →
  `writer.write(line)`을 직접 호출하지 않고 → 공통 write channel에 enqueue.
- **P3-B**: `WebClient` Reactor Netty callback(event-loop) → 공통 write channel에 enqueue
  (event-loop에서 `PrintWriter`를 직접 만지지 않음, §3).

Experiment A에서 의도적으로 다른 것은 정확히 하나: **P3-A는 blocking outbound worker가 필요하고,
P3-B는 없다.** write channel/write executor 자체는 P3-A/B 완전히 동일한 구현·동일한 설정값을
공유한다(같은 클래스, 같은 Bean 구성).

## 3. Reactor Event-loop 보호 규칙 (P3-B)

P3-B에서 **절대 금지**:

- WebClient Reactor Netty event-loop 스레드에서 `PrintWriter.write()`를 직접 호출
- event-loop 스레드에서 blocking wait 후 Servlet write

WebClient의 `Flux<DataBuffer>`/이벤트 콜백(예: `.doOnNext`, `.subscribe()`의 `onNext` 등)에서
event-loop 스레드가 수행해도 되는 작업은 다음으로 한정한다:

- upstream chunk → frame/event 변환(byte[]/String 파싱, SSE 라인 분리)
- request-local bounded queue에 **non-blocking `offer()`**
- lifecycle signal(첫 청크 도달, 완료, 에러) 전달

실제 `PrintWriter.write()`는 반드시 공통 Servlet write executor(§5) 스레드에서 실행한다. 이
경계는 코드 리뷰로 강제한다 — event-loop 스레드에서 직접 Servlet API를 호출하는 코드가 있으면
Unit 2 구현 리뷰에서 reject 대상이다.

**Unit 3 addendum — "lifecycle signal 전달"도 그 내부 구현이 이 경계를 지켜야 한다.** 위 허용
목록의 "lifecycle signal 전달"(예: `PerStreamWriteChannel.markProducerDone()` 호출)은 호출
자체는 빠른 non-blocking 작업이라 event-loop 스레드에서 허용되지만, Unit 3 Smoke A에서 실제로
발견된 문제: 그 메서드 내부가 "buffer가 이미 비어있으면 즉시 `RequestLifecycle.tryTerminate()`를
inline으로 실행"하는 fast path를 가지고 있었고, `tryTerminate()`의 cleanup은
`AsyncContext.complete()`(Servlet 컨테이너 오퍼레이션)를 호출한다 — 즉 겉보기엔 "signal
전달"이지만 내부적으로 Servlet-side 작업이 event-loop 스레드에서 실행되는 경계 위반이 실제로
발생했다(`docs/test-results/phase3/unit3-p3b-functional/SUMMARY.md`, 실제 로그의 thread name으로
확인). `PrintWriter.write()` 자체는 호출되지 않았으므로 correctness bug는 아니었지만, 이 ADR의
경계 규칙 취지에는 어긋난다. P3-B의 `PerStreamWriteChannel.markProducerDone()`은 이 fast path를
공통 write executor로 dispatch하도록 수정했다(P3-A의 동일 클래스는 이 fast path를 Reactor
스레드에서 호출받을 일이 자체가 없으므로 수정 불필요 — 두 구현의 유일한 코드 차이,
`docs/test-results/phase3/unit3-p3b-functional/diff-PerStreamWriteChannel.txt` 참고). 결론:
"lifecycle signal"을 event-loop에서 허용할 때는 그 신호의 즉시-처리 fast path가 Servlet API를
건드리지 않는지까지 확인해야 한다 — 시그널 자체의 이름만으로는 안전을 보장하지 못한다.

## 4. Per-request Serialized Write Channel

### 4-1. 요구사항

같은 stream의 chunk1/chunk2/chunk3이 여러 write executor 스레드에서 동시에 처리되거나 순서가
뒤바뀌면 안 된다. 요구사항(원 지시 §5 그대로):

- 동일 stream write ordering 보장
- 동시에 `PrintWriter.write()` 실행 금지(같은 stream에 대해)
- shared executor 위에서 stream별 serialization
- request별 buffering은 bounded — unbounded queue 금지
- `completion` 이후 enqueue 금지
- `timeout` 이후 enqueue 금지
- `disconnect` 이후 pending chunk 폐기
- `error` 이후 pending chunk 폐기

**금지**: 한 stream이 enqueue할 때마다 무조건 새 독립 write task를 병렬 submit해서 ordering을
scheduler 우연에 맡기는 구조. shared executor의 worker 스레드 여러 개가 같은 stream의 서로 다른
chunk를 동시에 집어갈 수 있는 구조는 그 자체로 이 요구사항을 위반한다.

### 4-2. 구조: request-local bounded queue + single-drain-at-a-time state + shared bounded executor

```
class PerStreamWriteChannel {
    ArrayDeque<Frame> buffer;              // bounded, capacity = SERVLET_PER_STREAM_BUFFER_CAPACITY
    AtomicBoolean draining;                // single-drain-at-a-time gate
    volatile boolean terminal;             // completion/timeout/disconnect/error 이후 true
    ...
}
```

**offer(frame) — upstream 콜백/워커 스레드에서 호출(event-loop 포함, non-blocking이어야 함):**

1. `terminal == true`면 즉시 drop(로그만, enqueue 안 함) — completion/timeout 이후 enqueue 금지
   요구사항을 여기서 만족.
2. buffer에 non-blocking으로 추가 시도. 가득 찼으면 **overflow**(§6)로 처리하고 이 채널을
   terminal로 전이.
3. `draining.compareAndSet(false, true)`가 성공한 경우에만 이 스레드가 "drain task"를 공통
   Servlet write executor에 **정확히 하나** 제출한다. 이미 draining 중이면(false로 실패) 아무 것도
   submit하지 않는다 — 이미 도는 drain task가 새로 추가된 frame까지 마저 처리하고 끝난다.

**drain task — 공통 write executor 스레드에서 실행:**

1. buffer에서 frame을 하나 뺀다.
2. `PrintWriter.write()` + 필요 시 `flush()`(§3-3의 SSE 이벤트 경계 = 빈 줄에서 flush, Phase 1/2
   relay 규칙 승계).
3. buffer가 비었으면 `draining.set(false)`하고 task 종료. 이 사이에 새 frame이 offer()로 들어와
   `draining`을 다시 true로 못 만들면(이미 우리가 아직 false로 안 바꿨으므로) → 반드시
   **buffer 재확인 → 비어있지 않으면 draining 상태 유지, false로 내리기 전에 다시 확인**하는
   전형적인 "drain loop" 패턴(compare 후 재검사)으로 lost-wakeup을 방지한다: `draining.set(false)`
   직후 buffer가 비어있지 않다면 다시 `compareAndSet(false, true)`를 시도해 drain을 재개한다.
4. 한 drain task는 buffer가 빌 때까지 **같은 스레드가 순서대로** 계속 write한다(스레드 자체를
   바꾸지 않음) — 이것이 "동시에 PrintWriter.write() 실행 금지" + "ordering 보장"을 동시에
   만족시키는 핵심이다. 단, 이 draining이 무기한 이어져 다른 stream의 write task를 executor에서
   굶기지 않도록, 한 drain task가 처리하는 frame 수 또는 누적 시간에 상한을 둘지는 Unit 2 구현
   시점에 실측으로 정한다(현재는 "필요할 수 있는 안전장치"로만 기록).

**terminal 전이(completion/timeout/disconnect/error, 정확히 한 번):**

1. `terminal`을 true로 설정(idempotent, CAS).
2. buffer에 남은 pending frame을 폐기(clear).
3. 이미 진행 중인 drain task는 자연 종료되도록 둔다(강제 중단하지 않음 — 진행 중인
   `PrintWriter.write()` 호출을 중간에 끊지 않는다).

### 4-3. 이 구조가 요구사항을 만족하는 이유

- **ordering**: 한 stream의 buffer는 FIFO이고, 그 buffer를 소비하는 drain task는 항상 한 번에
  하나만 존재한다(`draining` 게이트) — 여러 worker가 동시에 같은 stream을 drain할 수 없다.
- **동시 write 금지**: 위와 동일한 이유로 같은 stream에 대해 두 스레드가 동시에
  `PrintWriter.write()`를 호출할 수 없다.
- **bounded**: buffer capacity가 고정(`SERVLET_PER_STREAM_BUFFER_CAPACITY`), 초과 시 offer가
  아니라 overflow 경로로 빠진다(non-blocking).
- **completion/timeout 이후 enqueue 금지**: `terminal` 플래그를 offer() 진입점에서 제일 먼저
  검사.
- **disconnect/error 이후 pending 폐기**: terminal 전이 시 buffer clear.

## 5. Shared Servlet Write Executor

### 5-1. Config contract (이름만 freeze, 숫자는 Unit 5 전 확정)

```
SERVLET_WRITE_POOL_SIZE
SERVLET_WRITE_QUEUE_CAPACITY
SERVLET_PER_STREAM_BUFFER_CAPACITY
SERVLET_WRITE_REJECTION_POLICY
```

(기존 `EnvUtil.getInt/getString` 패턴 승계 — `gateway-mvc-java21/.../config/EnvUtil.java`.)

### 5-2. 의미론(freeze)

- **bounded**: `SERVLET_WRITE_POOL_SIZE`(core=max, Phase 2 P-E 패턴과 동일하게 core/max를 분리하지
  않는다 — §admission-connection-pool ADR §1 참고) + `SERVLET_WRITE_QUEUE_CAPACITY`(고정 크기
  `ArrayBlockingQueue`, unbounded 금지).
- **P3-A/P3-B 동일 설정**: 같은 클래스, 같은 env var, 같은 기본값. Experiment A의 통제 변수를
  지키기 위한 필수 조건.
- **Abort/Fast-fail 계열**: `SERVLET_WRITE_REJECTION_POLICY`는 `ThreadPoolExecutor.AbortPolicy`
  계열(제출 즉시 실패 반환, caller-runs/blocking 계열 금지) — drain task 제출이 거부되면 그
  stream을 즉시 write-overflow와 동일하게 terminal 처리한다(§6).
- **측정 가능**: queue wait time, active/queue/rejected 카운트를 모두 계측 가능해야 한다(구체
  metric 이름은 `phase3-metrics-contract.md`).
- **Formal 시작 후 변경 금지**: 정확한 pool size/queue capacity 숫자는 Unit 5 functional
  screening 전에 실측 근거(예: 동시 활성 stream 수 × 평균 write 빈도)로 확정하고, Formal
  benchmark 시작 이후에는 바꾸지 않는다.

## 6. Write Buffer Overflow Semantics

request-local write buffer(§4)가 가득 찼을 때, offer를 호출한 스레드(Reactor event-loop 또는
P3-A의 blocking outbound worker)는 **무기한 대기하지 않는다** — offer는 항상 non-blocking이다.

overflow 발생 시(offer가 "buffer full" 결과를 반환한 순간, fail-fast):

1. 해당 stream을 terminal outcome=`write_overflow`로 전이(§lifecycle state machine, exactly-once
   CAS).
2. upstream cancel/disconnect: P3-A는 `HttpURLConnection.disconnect()`, P3-B는 WebClient
   `Subscription`/`Disposable.dispose()`.
3. pending frame discard(§4-2 terminal 전이).
4. admission permit 반환(§admission-connection-pool ADR).
5. metric 기록: `write_overflow`(정확한 metric 이름은 metrics-contract ADR).

이 정책은 P3-A/P3-B 동일하게 적용한다 — write executor/write channel 자체가 공유 구현이므로
자연히 동일해진다.
