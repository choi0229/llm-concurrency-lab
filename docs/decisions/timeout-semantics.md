# ADR: CHAT_TOTAL_TIMEOUT_MS Semantics — Single Absolute Deadline

Status: Accepted (Phase 1 scope)
Date: 2026-08-11

## 문제

Unit 4-2에서 `CHAT_TOTAL_TIMEOUT_MS`("총 타임아웃")라는 이름과 실제 동작이 불일치했다:

- `AsyncContext.setTimeout(totalTimeoutMs)`는 `request.startAsync()` 호출 시점(요청 수신 직후)부터 카운트된다.
- 그러나 `relay()` 내부의 자체 deadline 체크는 **Executor 작업이 실제로 실행되기 시작한 시점**부터 새로
  `totalTimeoutMs`를 카운트했다 — Queue Wait 시간이 전혀 반영되지 않았다.

그 결과 Queue Wait가 길어지는 상황(VUS=100 Screening)에서 컨테이너의 AsyncContext 타임아웃(진짜 "요청
수신부터 총 60초")과 `relay()`의 자체 타임아웃(실행 시작부터 60초, 사실상 훨씬 뒤에 만료)이 서로 다른
시점을 기준으로 동작해 — AsyncContext가 먼저 타임아웃되어 응답이 완료 처리된 뒤에도, Executor 큐에 남아있던
작업이 뒤늦게 실행되며 이미 완료된 Response 객체를 건드리는 race가 발생했다.

## 결정: 두 개의 독립 타임아웃 대신 하나의 absolute deadline을 사용한다

**채택: 단일 `CHAT_TOTAL_TIMEOUT_MS` + absolute deadline.** `CHAT_REQUEST_TIMEOUT_MS` /
`CHAT_UPSTREAM_EXECUTION_TIMEOUT_MS`로 분리하는 대안은 기각했다.

근거:

1. 원 설계 문서(섹션 12)가 이미 `CHAT_TOTAL_TIMEOUT_MS` 하나로 명명했고, 실무에서 이 값의 의미는 "Front가
   응답을 기다릴 수 있는 전체 시간"이다. Front 입장에서는 지연이 Queue Wait 때문인지 Mock LLM 때문인지
   구분할 이유가 없다 — 전체 지연이 SLA를 넘는지만 중요하다.
2. Queue Wait 예산과 Upstream 실행 예산을 별도로 두고 싶어지는 유일한 이유는 "그 둘을 독립적으로
   실험하고 싶을 때"인데, Phase 1의 연구 질문(H1-a/H1-b/H1-c)은 오히려 **Queue Wait가 전체 지연에 얼마나
   기여하는지를 관찰하는 것**이 목적이므로, 하나의 총 deadline 아래서 Queue Wait와 Upstream 실행 시간이
   서로 예산을 갉아먹는 관계로 두는 것이 더 정확한 모델이다.
3. 구현 복잡도도 더 낮다 — 시계가 하나면 race를 원천적으로 줄이는 로직(아래)도 하나면 충분하다.

## 구현

```
requestReceivedNanos = System.nanoTime()          // stream(), Executor 제출 전
absoluteDeadlineNanos = requestReceivedNanos + CHAT_TOTAL_TIMEOUT_MS

// relay() 진입 시 (Executor 작업 실행 시작 시점, Queue Wait 이후)
remaining = absoluteDeadlineNanos - now
if (state.isReleased() || remaining <= 0) {
    gateway_stale_task_skipped_total++
    return   // upstream 호출을 아예 시작하지 않는다
}

// Mock LLM 호출: connect/read timeout을 remaining으로 clamp
effectiveConnectTimeout = min(CHAT_CONNECT_TIMEOUT_MS, remaining)
effectiveReadTimeout    = min(CHAT_READ_TIMEOUT_MS, remaining)

// 응답 relay 루프에서도 매 라인마다 absoluteDeadlineNanos 기준으로 재확인
```

`AsyncContext.setTimeout(totalTimeoutMs)`와 `absoluteDeadlineNanos`는 이제 **같은 기준점(요청 수신
시점)에서 같은 길이**를 재므로, 컨테이너의 async timeout과 우리 자체 deadline이 사실상 동시에 만료된다 —
"Queue에서 너무 오래 기다린 작업은 애초에 upstream을 호출하지 않는다"는 목표가 이름 그대로 동작한다.

## 남은 race와 그 처리

컨테이너의 `onTimeout` 콜백과 워커 스레드의 `remaining <= 0` 체크는 **서로 다른 스레드에서 독립적으로
동작**하므로, 아주 좁은 시간창에서는 여전히 다음이 가능하다: 워커가 `remaining > 0`으로 체크를 통과해
upstream 호출/relay를 시작한 직후, 컨테이너의 timeout이 먼저 발동해 AsyncContext를 완료시키는 경우.
이 경우 워커가 이미 recycle된 Response에 접근하며 예외(관찰된 바로는 `IllegalStateException`이 아니라
Tomcat 내부 `NullPointerException`)가 발생할 수 있다 — 이건 사전 체크만으로 100% 제거할 수 없는, Servlet
비동기 모델 자체의 근본적인 race이다. 이 잔여 race의 처리 방침은 `docs/decisions/version-compatibility.md`가
아니라 코드 주석 및 `ChatController`에 남긴 `catch (RuntimeException e)` 방어 코드로 다룬다 — 자세한 내용은
아래 "RuntimeException 처리 방침" 참고.

## RuntimeException 처리 방침

`catch (RuntimeException e)`를 "race를 정상적으로 처리하는 주 수단"으로 쓰지 않는다. 위 사전 체크
(`state.isReleased()` + `remaining <= 0`)가 **1차 방어선**으로 대부분의 stale task를 upstream 호출 전에
걸러낸다. `catch (RuntimeException e)`는 그 사전 체크로도 못 막는, 실행 도중(mid-relay) 발생하는 좁은 race만
잡기 위한 **최후 방어선(defensive backstop)**이다.

이 backstop에 걸린 예외를 무조건 "stale task"로 단정하지 않는다 — 잡은 시점에 `state.isReleased()`를 다시
확인해서:

- `true`면 (AsyncContext가 실제로 이미 완료됐다는 증거가 있음) → `gateway_stale_task_skipped_total`
- `false`면 (AsyncContext는 아직 살아있는데 다른 이유로 RuntimeException이 났다는 뜻) →
  **별도 지표 `gateway_unexpected_runtime_error_total`**로 기록하고 `ERROR` 레벨 + 전체 스택트레이스로 로깅한다.
  이건 진짜 애플리케이션 버그일 수 있으므로 timeout/stale race와 절대 같은 지표에 섞지 않는다.
