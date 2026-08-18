# ADR: Request Outcome Accounting — `gateway_request_outcome_total`

Status: Accepted (Phase 1 scope, pre-Unit 5)
Date: 2026-08-11

## 문제

Unit 4-3 reconciliation에서 실제로 발견된 문제: k6가 관찰한 21건의 실패 클라이언트 중, 당시 존재하던
failure counter(`gateway_upstream_failure_total` + `gateway_stale_task_skipped_total` +
`gateway_unexpected_runtime_error_total` + `gateway_client_disconnect_total`)를 모두 더해도 12건밖에
설명되지 않았다 — `gateway_relay_deadline_exceeded_total`이 아직 존재하지 않아 나머지 9건이 어느 metric에도
잡히지 않았기 때문이다. 이 사건이 보여주는 근본 문제는: **개별 diagnostic counter를 계속 추가하는 방식으로는
"빠짐없이 세고 있다"는 것을 보장할 수 없다** — 새 종료 경로가 추가될 때마다 그 경로를 위한 counter를 잊지
않고 추가해야 하는데, 그 사실을 확인할 방법이 사후 reconciliation(수동으로 숫자를 맞춰보는 것)뿐이었다.

## 결정: 단일 authoritative outcome metric + exactly-once 보장

`gateway_request_outcome_total{outcome}` 하나를 **Benchmark 결과 accounting의 유일한 source of
truth**로 둔다. 기존 diagnostic counter(`gateway_upstream_failure_total{reason}`,
`gateway_stale_task_skipped_total`, `gateway_client_disconnect_total`,
`gateway_unexpected_runtime_error_total`, `gateway_relay_deadline_exceeded_total`,
`gateway_executor_rejected_total`, `gateway_async_timeout_total`)는 전부 유지한다 — 이들은 "왜"를
설명하는 diagnostic 용도이지 "몇 건"을 세는 accounting 용도가 아니다. 서로 다른 코드 경로가 같은 요청에
대해 여러 diagnostic counter를 중복으로 증가시킬 수 있다(예: onTimeout이 이겼는데도 워커 스레드가 뒤늦게
`SocketTimeoutException`을 잡아 `upstream_failure{reason=read_timeout}`도 증가시키는 경우 — 실제로
Unit 5 착수 전 절대 deadline 검증에서 관찰됨, 아래 참고). 이건 diagnostic counter로서는 정상이고 의도된
동작이다 — 그래서 accounting을 diagnostic counter의 합으로 하면 안 된다.

### Outcome 값 (고정 8종)

| outcome | 의미 | 승자가 되는 코드 경로 |
|---|---|---|
| `completed` | Mock LLM이 자연스럽게 스트림을 끝냄(EOF) | `relay()` while-loop 정상 종료 |
| `rejected` | Executor가 admission 자체를 거부(AbortPolicy) | `stream()`의 `RejectedExecutionException` catch |
| `timeout_before_start` | AsyncContext가 이미 타임아웃됐는데 워커가 아직 시작도 안 했거나 시작 직후 자체 deadline 체크에 걸림 | `AsyncListener.onTimeout`, 또는 `relay()` 진입부 stale 체크 |
| `deadline_exceeded` | 최소 한 chunk는 이미 보냈는데 relay 도중 absolute deadline을 넘김(또는 그 mid-relay race를 RuntimeException으로 감지) | `relay()`의 per-line deadline 체크, 또는 `state.isReleased()==true`인 RuntimeException catch |
| `upstream_timeout` | Mock LLM과의 connect 또는 read가 SocketTimeoutException으로 실패 | `relay()`의 `SocketTimeoutException` catch (connect-phase/read-phase 둘 다) |
| `upstream_error` | Mock LLM과의 통신이 timeout이 아닌 다른 IOException으로 실패 | `relay()`의 `IOException` catch (connect-phase/read-phase 둘 다) |
| `client_disconnect` | 클라이언트로의 쓰기가 실패(연결 끊김) | `PrintWriter.checkError()`, 또는 `AsyncListener.onError` |
| `unexpected_error` | 위 어느 경로로도 설명 안 되는 RuntimeException, 또는 `AsyncListener.onComplete`가 release race를 이겨버린 경우(정상 흐름에서는 발생하면 안 됨) | `relay()`의 `RuntimeException` catch(`state.isReleased()==false`), 또는 `AsyncListener.onComplete` |

### Exactly-once 보장 메커니즘

`AsyncRequestState`가 요청 하나당 하나뿐인 CAS(`AtomicBoolean released`)를 갖고,
`releaseOnce(String outcome, Runnable releaseAction)`로만 outcome을 기록한다. 이 요청의 terminal
state를 처음 확정짓는 호출만 `wasFirst=true`를 받고, 그 호출의 `releaseAction` 안에서만
`gateway_request_outcome_total{outcome}.inc()`가 실행된다. 이후 같은 요청에 대해 다른 코드 경로가 몇 번을
더 시도해도(`onTimeout`과 `relay()`의 finally가 거의 동시에 도착하는 경우 등) 그 호출들은 전부
`wasFirst=false`를 받고 아무 metric도 증가시키지 않는다 — "여러 코드 경로가 같은 요청을 종료시키려 경쟁할
수 있다"는 사실 자체는 막지 않되, **metric에 반영되는 것은 그중 정확히 하나**로 강제한다.

기존 diagnostic counter들은 이 CAS와 무관하게 계속 무조건 증가한다(위에서 설명한 대로 의도된 동작).

## Accepted의 정의

```
gateway_request_received_total       // stream() 진입 시, executor 제출 이전에 무조건 1 증가
gateway_request_outcome_total{rejected}  // chatExecutor.execute()가 RejectedExecutionException을 던진 경우

accepted := gateway_request_received_total - gateway_request_outcome_total{outcome="rejected"}
```

즉 **accepted = "chatExecutor.execute()가 예외 없이 리턴한 요청"** — 실제로 pool worker가 실행했는지
(`mode=pool`) 아니면 CallerRunsPolicy로 caller thread에서 즉시 실행됐는지(`mode=caller`)는 accepted 여부와
무관하다. 둘 다 "Executor가 이 작업을 받아들였다"는 점에서 동일하게 accepted다.

## 검증할 invariant (매 run 종료 후, drain 완료 시점에)

```
1. 아무것도 안 사라짐:
   gateway_request_received_total == sum(gateway_request_outcome_total)  # 모든 outcome label 합

2. accepted 정의가 실제로 성립:
   accepted == sum(gateway_request_outcome_total excluding outcome="rejected")
   (1번이 성립하면 산술적으로 항상 성립 — 그래도 별도로 확인해서 "rejected가 pre-accept로 정확히
   분리되어 있다"는 가정 자체가 깨지지 않았는지 재확인한다.)
```

두 invariant 모두 `scripts/verify-outcome-invariant.sh`로 Prometheus HTTP API를 통해 자동 검증한다.
Unit 5의 모든 Screening run, Unit 6의 모든 정식 Benchmark run 종료 후(drain 완료 후) 반드시 실행한다.
위반 시 그 run의 결과는 신뢰할 수 없는 것으로 간주하고 재실행한다.

## 관련: diagnostic counter의 "이중 계상"이 실제로 관찰된 사례

Unit 5 착수 전 absolute deadline 검증(0-3, `docs/test-results/phase1/absolute-deadline-blocking-read.md`)
에서, `AsyncContext.onTimeout`이 `timeout_before_start`로 이미 요청을 종료시킨 뒤에도 워커 스레드가
`readLine()`에서 나중에 `SocketTimeoutException`을 던져 `gateway_upstream_failure_total{reason=read_timeout}`
을 추가로 증가시키는 경우가 실제로 관찰됐다 — `HttpURLConnection`의 read timeout이 absolute deadline이
아니라 매 read 호출마다 리셋되는 상대 시간이기 때문(자세한 내용은 해당 문서 참고). 이 diagnostic counter는
의도대로 "이 코드 경로가 실행됐다"는 사실을 정확히 기록한 것이고, `gateway_request_outcome_total`은 위
exactly-once 메커니즘 덕분에 이 요청을 여전히 `timeout_before_start` 단 하나로만 집계한다 — 이 설계가
필요한 이유를 실제로 증명한 사례다.
