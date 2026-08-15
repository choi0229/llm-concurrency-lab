# Unit 5 착수 전 0-3: Absolute Deadline vs Blocking `readLine()` 검증

Status: 검증 완료 — **한계 확인됨, Phase 1 Baseline의 알려진 제약으로 기록**
Date: 2026-08-11

## 배경

`docs/decisions/timeout-semantics.md`가 채택한 absolute deadline 설계는 `AsyncContext.setTimeout()`과
자체 deadline 체크를 같은 기준점·같은 길이로 맞췄다. 하지만 실제 upstream I/O는
`HttpURLConnection.getInputStream()`/`BufferedReader.readLine()`라는 **blocking** 호출이고, 그 타임아웃
(`Socket.setSoTimeout()`, `MockLlmClient`가 `remainingMs`로 clamp해서 설정)은 absolute deadline이 아니라
**매 read 호출마다 리셋되는 상대 시간**이다. 이 문서는 "AsyncContext가 이미 타임아웃됐는데도 platform
worker thread가 blocking read 때문에 오래 남아있는가"를 실제로 재현해 확인한다.

## 재현 방법

`mock-llm-fastapi`에 진단 전용 파라미터 `stallAfterChunk`/`stallMs`를 추가(`app/main.py`) — 몇 개
chunk는 정상 간격으로 보내고, 그 이후 한 번은 훨씬 긴 간격(stall)을 두도록 지정할 수 있다.

Gateway 설정(`docker-compose.override.yml`, 일회성 — Screening/정식 Benchmark에는 쓰지 않음):

```
CHAT_TOTAL_TIMEOUT_MS=10000    # 10s — 짧게 잡아 수동 검증을 빠르게 반복하기 위함
CHAT_READ_TIMEOUT_MS=300000    # 5min — remaining-budget clamp가 실제 바인딩 제약이 되도록 충분히 크게
CHAT_CONNECT_TIMEOUT_MS=3000   # 기본값, 이 테스트와 무관
CHAT_CORE_POOL_SIZE=10 / CHAT_MAX_POOL_SIZE=10 / CHAT_QUEUE_CAPACITY=100 / CHAT_REJECTION_POLICY=ABORT  # 기본값
```

요청 (단발, 동시성 1):

```json
{"firstChunkDelayMs":500,"chunkIntervalMs":1000,"chunkCount":6,"chunkSizeBytes":16,
 "maxDurationMs":120000,"stallAfterChunk":3,"stallMs":30000}
```

즉 chunk 1/2/3을 각각 ~0.5s/1.5s/2.5s에 정상적으로 보내고, chunk 4를 보내기 전 30초간 정지한다 —
`CHAT_TOTAL_TIMEOUT_MS=10000`보다 훨씬 길다.

관찰 방법: `gateway:8080/metrics`를 0.5s 간격으로 poll하며 `gateway_async_active_requests`와
`gateway_executor_active_threads`를 기록, 동시에 Gateway/Mock LLM 컨테이너 로그(타임스탬프 포함)를 수집.

## 결과 (실측, 2026-08-11)

| 시각(요청 시작 기준) | 이벤트 |
|---|---|
| t=0.00s | 요청 시작 |
| t≈0.5s / 1.5s / 2.5s | chunk 1/2/3 정상 수신 (Java 쪽 `readLine()`이 매번 성공 → SO_TIMEOUT이 그때마다 리셋) |
| **t≈10.32s** | `AsyncContext.onTimeout` 발동 (`CHAT_TOTAL_TIMEOUT_MS=10000`, 로그: `async timeout after 10000ms`) → 클라이언트에게는 이 시점에 이미 응답이 종료됨. `gateway_async_active_requests`는 이 시점 직후 폴링에서 0으로 관측됨(t=10.4s 폴링) |
| **t≈12.55s** | Java worker thread가 `readLine()`에서 `SocketTimeoutException: Read timed out`을 잡음 (로그: `upstream failure (read_timeout): Read timed out`) — 이 순간까지 `gateway_executor_active_threads`는 계속 1을 유지하다가, 이 직후 폴링(t=12.71s)에서야 0으로 떨어짐 |
| t≈12.55s | Mock LLM 로그: `client disconnected (stream closed by server)` — Java worker의 `finally` 블록이 `connection.disconnect()`를 호출한 바로 그 순간에야 upstream 연결이 끊김 |

**Platform worker thread가 AsyncContext 타임아웃 이후로도 약 2.2초(2.235503s) 더 blocking read 상태로
남아있었다.** 이 구간 동안:

- 클라이언트는 이미 최종 응답(타임아웃 에러)을 받은 상태
- `gateway_async_active_requests`는 0 (Servlet 레벨에서는 "요청 없음")
- 그러나 `gateway_executor_active_threads`는 여전히 1 — 이 스레드는 새 요청을 받을 수 없는 상태로,
  이미 끝난 요청의 뒷정리를 위해 blocking read에 묶여 있었음
- upstream(Mock LLM)과의 TCP 연결도 이 순간까지 열려 있었음 — Gateway가 먼저 끊지 않았음

## 원인

`MockLlmClient.openStream()`이 `connection.setReadTimeout(effectiveReadTimeout)`으로 설정하는 값은
**요청 시작 시점 1회** `remainingMs`(그 시점 기준 absolute deadline까지 남은 시간)로 clamp된 것이지만,
`Socket`/`HttpURLConnection`의 read timeout은 **매 개별 read 호출마다 그 값만큼 다시 카운트**한다(JDK
표준 동작 — absolute deadline이 아니라 idle-per-read timeout). 이 테스트에서는 chunk 1/2/3이 t≈2.5s까지
정상 수신되며 매번 SO_TIMEOUT을 리셋시켰고, 그 마지막 리셋(t≈2.5s) + `effectiveReadTimeout`(≈10s, 요청
시작 시점 기준 remainingMs) ≈ t≈12.5s에 소켓 타임아웃이 발동했다 — 이는 실제 absolute deadline(t≈10.0s)
보다 약 2.5s 늦다. 즉 **상류에서 chunk가 조금이라도 계속 오는 한, remaining-budget clamp가 있어도 실제
차단 해제 시점은 absolute deadline보다 "마지막 성공한 read 이후 경과 시간"만큼 계속 뒤로 밀릴 수 있다.**

## Phase 1 Baseline의 한계로 기록

이것은 버그라기보다 **`HttpURLConnection`(JDK 표준, blocking I/O)을 그대로 쓰는 Baseline 아키텍처
자체의 구조적 한계**다 — Servlet AsyncContext의 논리적 타임아웃과, 하위 blocking 소켓의 read timeout은
서로 다른 메커니즘이라 정확히 동기화될 수 없다. `CHAT_READ_TIMEOUT_MS`를 매우 작게(예: 1s) 잡으면 drift를
줄일 수 있지만 완전히 없앨 수는 없고, 오히려 정상적인 chunk 간격이 그보다 길면 정상 스트림도 끊어버리는
trade-off가 생긴다. 이 Baseline(`gateway-mvc-executor-java8`)에서는 이 한계를 그대로 감수하고 기록하는
것으로 처리한다 — Executor Matrix Screening(Unit 5)의 결과 해석 시, "AsyncContext 관점의 timeout 시점"과
"실제 platform worker/스레드가 반환되는 시점" 사이에 항상 이런 지연이 있을 수 있음을 감안한다. 특히 Config
A(CallerRunsPolicy)처럼 caller thread 자체가 blocking read에 묶이는 구성에서는, 이 지연이 Tomcat request
thread 하나를 그만큼 더 오래 점유한다는 뜻이므로 더 중요하다.

## 검토했으나 채택하지 않은 대안: `AsyncListener.onTimeout`에서 연결 강제 disconnect

`onTimeout`이 현재 요청의 `HttpURLConnection`을 알고 있다면 `connection.disconnect()`를 호출해 blocking
read를 즉시 깨울 수 있다(다른 스레드에서 `disconnect()`를 부르면 그 소켓에서 블로킹 중이던 read가
`SocketException`으로 즉시 풀려나는 것은 `HttpURLConnection`의 표준 동작). 이번 검증 목적(현상 확인)에는
필요하지 않아 실제로 적용하지 않았다 — `ChatController.stream()`이 `AsyncListener`를 등록하는 시점에는
아직 `connection`이 존재하지 않고(Executor 작업이 나중에 만듦), 이를 위해서는 `AsyncRequestState`에
`volatile HttpURLConnection` 참조를 추가하고 `relay()`가 connection을 연 직후 그 필드를 채우는 구조 변경이
필요하다 — race(즉 `onTimeout`이 그 필드를 읽는 순간과 `relay()`가 쓰는 순간이 겹치는 경우)를 안전하게
처리해야 하므로 Phase 1 범위를 넘어서는 별도 설계/검증이 필요하다고 판단해 이번 0-3에서는 적용을
보류한다. Unit 5 Screening 결과에서 이 drift가 무시할 수 없는 수준으로 나타나면(특히 Config A) 재검토한다.
