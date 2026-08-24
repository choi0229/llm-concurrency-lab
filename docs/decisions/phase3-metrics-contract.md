# ADR: Phase 3 Metric Contract (Micrometer)

Status: Accepted (Phase 3 Unit 1 scope)
Date: 2026-08-22

## 1. Micrometer로 통일 — 결정

Phase 3 세 구현체(P3-A/B/C)가 모두 Boot 2.7.18 + Micrometer 1.9.17을 공유하므로(Unit 0 실측,
`docs/decisions/phase3-version-compatibility.md` §5), Phase 3 내부에서는 **custom metric도
Micrometer `MeterRegistry` 하나로 통일한다.** Phase 1/2가 써온 `io.prometheus.client`
(simpleclient) 방식을 관성으로 복제하지 않는다.

근거:

- Phase 1/2는 simpleclient(`io.prometheus.client.Counter/Gauge/Histogram`,
  `GatewayMetrics.java`)를 직접 썼고, Actuator/Micrometer를 쓰지 않았다 — Phase 3는 Boot
  Actuator + Micrometer가 이미 Reactor Netty client metrics(§2-3)를 자동으로 이 registry에
  올려주므로, custom metric도 같은 registry에 두면 P3-A/B/C 동일 API로 custom/Tomcat/Reactor
  Netty metric을 한 endpoint(`/actuator/prometheus`)에서 함께 노출할 수 있다.
- P3-C는 Tomcat이 없고, P3-A/B는 Reactor Netty client가 없거나(P3-A) 있거나(P3-B) 다르므로,
  구현별로 노출되는 metric 집합이 원래도 다르다 — 그 차이 자체가 서버 모델 차이의 일부라는 점을
  §5 Tomcat metrics에서 다시 명시한다.

## 2. 실측 — Actuator/Prometheus Capability Spike

지시문 §24/§29가 요구한 대로 metric 이름을 추측하지 않고 실제 최소 앱(Boot 2.7.18, MVC+WebClient,
Micrometer 1.9.17, `management.endpoints.web.exposure.include=prometheus,health`,
`server.tomcat.mbeanregistry.enabled=true`)을 기동해 `/actuator/prometheus` 실제 출력을 확인했다.
원본 앱 소스/설정/전체 출력은 `docs/test-results/phase3/unit1-capability-spikes/`
(`SpikeMetricsApplication.java`, `spike-metrics-application.yml`,
`actuator-prometheus-full-output.txt`, `confirmed-metric-names-summary.txt`)에 보존.

### 2-1. Custom Micrometer metric 이름 변환 규칙(실측 확인)

```java
registry.counter("gateway.requests.started")     // dotted name
registry.timer("gateway.stream.duration")
```

Prometheus 노출 결과:

```
gateway_requests_started_total 1.0
gateway_stream_duration_seconds_max 0.010230916
gateway_stream_duration_seconds_count 2.0
gateway_stream_duration_seconds_sum 0.010557124
```

확인된 변환 규칙: `.`→`_`, `Counter`는 `_total` suffix, `Timer`는 `_seconds_{max,count,sum}`
suffix(and, histogram/percentile 설정 시 `_seconds_bucket`도 추가됨 — 이번 spike는 percentile
histogram을 켜지 않았으므로 bucket은 미확인, Unit 2 구현 시 실제 필요한 histogram을 등록한 뒤
재확인한다).

### 2-2. Tomcat metric 이름(P3-A/P3-B, 실측 확인, Tomcat 9.0.83)

```
tomcat_threads_busy_threads{name="http-nio-<port>"}
tomcat_threads_current_threads{name="http-nio-<port>"}
tomcat_threads_config_max_threads{name="http-nio-<port>"}
tomcat_connections_current_connections{name="http-nio-<port>"}
tomcat_connections_config_max_connections{name="http-nio-<port>"}
tomcat_connections_keepalive_current_connections{name="http-nio-<port>"}
tomcat_global_request_seconds_count / _sum / tomcat_global_request_max_seconds{name="http-nio-<port>"}
tomcat_global_error_total{name="http-nio-<port>"}
tomcat_global_sent_bytes_total / tomcat_global_received_bytes_total{name="http-nio-<port>"}
tomcat_servlet_request_seconds_count / _sum{name="dispatcherServlet"}
tomcat_servlet_request_max_seconds{name="dispatcherServlet"}
tomcat_servlet_error_total{name="dispatcherServlet"}
tomcat_sessions_*  (created/expired/rejected/active_current/active_max sessions, alive_max_seconds)
tomcat_cache_hit_total / tomcat_cache_access_total
```

**주의**: 이 metric들은 `server.tomcat.mbeanregistry.enabled=true`가 없으면 노출되지 않을 수
있다(Boot 2.7의 `TomcatMetricsBinder`가 Tomcat 내부 JMX-registerable 객체에 의존) — 이번 spike는
이 설정을 명시적으로 켠 상태에서 확인했다. Unit 2 구현 시 P3-A/B의 `application.yml`에 이 설정을
포함해야 한다.

### 2-3. Reactor Netty client / connection-pool metric 이름(P3-B/P3-C, 실측 확인, Reactor Netty
1.0.39)

WebClient에 `HttpClient.create(provider).metrics(true, ...)` + `ConnectionProvider.metrics(true)`를
켠 상태로 확인:

```
reactor_netty_connection_provider_active_connections{id,name,remote_address}
reactor_netty_connection_provider_idle_connections{id,name,remote_address}
reactor_netty_connection_provider_pending_connections{id,name,remote_address}
reactor_netty_connection_provider_total_connections{id,name,remote_address}
reactor_netty_connection_provider_max_connections{id,name,remote_address}
reactor_netty_connection_provider_max_pending_connections{id,name,remote_address}

reactor_netty_http_client_connect_time_seconds_{count,sum,max}{remote_address,status}
reactor_netty_http_client_response_time_seconds_{count,sum,max}{method,remote_address,status,uri}
reactor_netty_http_client_data_sent_time_seconds_{count,sum,max}{method,remote_address,uri}
reactor_netty_http_client_data_received_time_seconds_{count,sum,max}{method,remote_address,status,uri}
reactor_netty_http_client_data_sent_bytes_{count,sum,max}{remote_address,uri}
reactor_netty_http_client_data_received_bytes_{count,sum,max}{remote_address,uri}

reactor_netty_eventloop_pending_tasks{name}
reactor_netty_bytebuf_allocator_*{id,type}   (풀 버퍼 할당자 진단용, Phase 3 핵심 비교 대상 아님)
```

**`reactor_netty_connection_provider_pending_connections`가 §admission-connection-pool ADR §3-1의
invariant("pending≈0")를 실제로 검증할 metric이다** — `name` 라벨이 `ConnectionProvider.builder(
"...")`에 준 pool 이름과 일치한다(spike에서 `name="spike-webclient-pool"`로 확인).

`uri` 라벨: spike에서 `/one`(실제 요청 경로)뿐 아니라 `uri="http"`인 행도 함께 관찰됐다 — 이는
Reactor Netty가 초기 프로토콜 협상/저수준 채널 단계 데이터를 별도로 태깅하는 것으로 보이며, Phase
3 dashboard에서는 실제 요청 경로(`/chat/stream` 대응, 여기서는 Mock LLM 쪽 `/mock/stream`)로
필터링해서 읽어야 한다.

### 2-4. Reactor Netty **server** metrics(P3-C 전용) — 이번 spike에서 미확인

이번 capability spike는 MVC+WebClient 구성으로 서버는 Tomcat이었다 — Reactor Netty embedded
**server** metric(`reactor_netty_http_server_*`류)은 이번 spike에서 관찰되지 않았다.
**추측으로 이름을 freeze하지 않는다** — Unit 2에서 P3-C의 최소 WebFlux 앱을 띄운 뒤 별도로 확인한다.

## 3. Common Metric 목록(논리 이름, freeze)

| 이름 | 종류 | 라벨 | 의미 |
|---|---|---|---|
| `gateway.requests.started` | Counter | — | admission 시도 전, 요청 수신 시 |
| `gateway.requests` | Counter | `outcome` | terminal outcome, exactly-once(phase3-design.md §5) |
| `gateway.active.streams` | Gauge | — | STREAMING 상태인 요청 수 |
| `gateway.admission.active` | Gauge | — | 현재 보유 중인 admission permit 수 |
| `gateway.admission.rejected` | Counter | — | admission tryAcquire 실패 |
| `gateway.upstream.active` | Gauge | — | Mock LLM과 활성 연결/구독 수 |
| `gateway.upstream.cancel` | Counter | — | Gateway가 **이미 시작된** upstream 작업/connection/subscription에 cancellation을 요청한 횟수(deadline/disconnect/overflow/upstream_error 경로 포함). `rejected`는 upstream이 애초에 시작되지 않으므로 제외(Unit 5.5 §0에서 P3-A/B의 구현 오류를 실측으로 발견·수정 — 원래 "completed가 아닌 모든 outcome"에서 증가하던 것을 "completed와 rejected가 아닌 outcome"으로 정정, `docs/test-results/phase3/unit5.5-diagnostics/upstream-cancel-fix-regression/SUMMARY.md` 참고) |
| `gateway.client.disconnect` | Counter | — | client disconnect로 종료된 요청 |
| `gateway.timeout` | Counter | — | absolute deadline 초과로 종료된 요청 |
| `gateway.write.overflow` | Counter | — | write-path ADR §6의 overflow |
| `gateway.watchdog.activated` | Counter | — | timeout-cancellation ADR §5의 AsyncContext safety watchdog 실제 발동(anomaly) |
| `gateway.bytes.relayed` | Counter | — | client로 relay된 바이트 수 |
| `gateway.ttfc` (server 측 관측명은 `gateway.ttfb`) | Timer | — | 요청 수신 → 첫 chunk relay(Phase 1/2 `gateway_ttfb_seconds`와 동일 정의 승계) |
| `gateway.stream.duration` | Timer | — | 요청 수신 → terminal(Phase 1/2에는 없던 지표, Phase 3 신규) |

이름은 Micrometer dotted 표기(§2-1 변환 규칙에 따라 Prometheus에서는 `gateway_requests_started_total`
등으로 노출됨). `outcome` 라벨 값 집합은 phase3-design.md §5의 7종(`completed`, `rejected`,
`timeout`, `upstream_error`, `client_disconnect`, `write_overflow`, `internal_error`)과 동일.

### 3-1. High-cardinality 태그 금지

`requestId`/`url`/`remote_address`(client IP) 등 요청마다 달라지는 값을 **custom metric의 태그로
넣지 않는다.** §2-3에서 확인한 `reactor_netty_*` metric들이 `remote_address`/`uri` 태그를 갖는
것은 Reactor Netty 자체 구현이며(Mock LLM 하나만 upstream이므로 이 경우 cardinality는 낮게
유지된다), Phase 3가 새로 추가하는 custom metric에는 이런 라벨을 붙이지 않는다.

## 4. Implementation-specific Metric 목록

### 4-1. Servlet Write Metrics — P3-A/P3-B 공통(§write-path ADR)

| 이름 | 종류 | 의미 |
|---|---|---|
| `servlet.write.executor.active` | Gauge | write executor 활성 스레드 수 |
| `servlet.write.executor.pool.size` | Gauge | write executor pool 크기 |
| `servlet.write.executor.queue.depth` | Gauge | write executor 큐 잔량 |
| `servlet.write.executor.rejected` | Counter | write executor 제출 거부 총합 |
| `servlet.write.stream.buffered.frames` | Gauge | 현재 모든 stream의 pending frame 합계(per-stream이 아니라 합산 gauge — per-stream은 high-cardinality가 되므로 태그로 만들지 않는다) |
| `servlet.write.overflow` | Counter | §write-path ADR §6 |
| `servlet.write.duration` | Timer | 한 write task(drain 1회 write)의 소요 시간 |
| `servlet.write.queue.wait` | Timer | drain task가 executor에 제출된 뒤 실제 시작까지의 대기 시간 |

이 metric들이 있어야 "P3-B에서 outbound blocking worker는 사라졌지만 Servlet write thread 비용은
어디에 남는지"를 설명할 수 있다(Experiment A의 핵심 관찰 대상, phase3-design.md §1).

### 4-2. P3-A Blocking Outbound Metrics(§admission-connection-pool ADR §2)

| 이름 | 종류 | 의미 |
|---|---|---|
| `outbound.blocking.executor.active` | Gauge | |
| `outbound.blocking.executor.pool.size` | Gauge | |
| `outbound.blocking.executor.largest.pool.size` | Gauge | |
| `outbound.blocking.executor.rejected` | Counter | |
| `outbound.blocking.executor.queue.depth` | Gauge | `SynchronousQueue` 채택 시 의미상 항상 0에 가깝다(§admission-connection-pool ADR §2-3 — 실제 큐잉이 구조적으로 발생하지 않음). 이 gauge를 없애지 않고 유지하는 이유는 "0이어야 정상"이라는 것 자체가 invariant이기 때문 — 0이 아닌 값이 관찰되면 설정 오류 신호. |
| `outbound.blocking.task.duration` | Timer | 개별 blocking HttpURLConnection 호출 소요 시간 |

### 4-3. Reactor-specific Metrics — P3-B/P3-C(§2-3, §2-4)

`reactor_netty_connection_provider_*`, `reactor_netty_http_client_*`는 Reactor Netty가 자동으로
등록하므로 Phase 3가 별도로 만들 필요가 없다 — Micrometer global registry에 연결만 되어 있으면
된다(`management.metrics.use-global-registry=true`, Boot 기본값, spike에서 명시적으로 켜서 확인).
P3-C의 server-side reactor netty metric(`reactor_netty_http_server_*` 예상, §2-4에서 미확인)은
Unit 2에서 실제 이름을 확인한 뒤 이 표에 추가한다.

### 4-4. Tomcat Metrics — P3-A/P3-B(§2-2)

§2-2에 나열한 이름을 그대로 사용한다. **P3-C에는 Tomcat이 없으므로 이 metric 자체가 존재하지
않는다** — 이는 실수로 빠뜨린 것이 아니라 server model 차이(Experiment B)의 일부다. 존재하지
않는 metric 이름을 P3-C용으로 만들어내지 않는다.

## 5. Histogram/Timer Bucket

P3-A/B/C에서 **반드시 동일한** bucket 경계를 쓴다 — cross-implementation 비교의 전제조건(Phase 2
Unit 6.7이 이미 겪은 "bucket 정밀도가 다르면 p95/p99가 조용히 틀어진다"는 교훈,
docker_desktop_clock_drift/prometheus_histogram_bucket_precision 메모리 참고). 정확한 bucket
경계값은 Phase 2 `GatewayMetrics.FINE_LATENCY_BUCKETS`(0.001s~60s, 38개 경계)를 출발점으로
재사용할지, Micrometer의 `distributionStatisticConfig` 방식(백분위수 직접 계산 vs classic
histogram)으로 바꿀지는 Unit 2 구현 시 확정한다 — Micrometer는 Prometheus Histogram(`.sla()`
classic bucket) 방식과 client-side percentile(`.publishPercentiles()`) 방식을 모두 지원하므로,
Unit 1에서는 "**classic histogram bucket 방식을 채택하고 Phase 2와 동일 경계값을 재사용하는
쪽으로 기울되, 정확한 배열은 Unit 2에서 실측 재확인한다**"까지만 freeze한다(숫자 자체는 §write-
path ADR §5-2/§admission-connection-pool ADR §3-5와 같은 원칙 — Unit 5 이전 확정, Formal 시작 후
변경 금지).

## 6. Metric Invariants(Formal 전 반드시 검증)

**Unit 6 amendment**: `gateway_admission_active`의 정확한 semantic(permit acquire/release 시점)은
`docs/decisions/phase3-admission-semantics-unification.md`에서 확정했다 — upstream 호출
lifetime을 보호하는 gauge이며, `gateway_active_streams`(response lifecycle이 terminal되지
않은 요청 수)와는 별개 개념이다. 아래 postflight invariant(모든 gauge가 0이어야 한다는 것) 자체는
이 semantic 변경으로 바뀌지 않는다 — drain이 다 끝난 뒤에는 여전히 모두 0이어야 한다. 다만
**steady-state 중간에는** `gateway_admission_active == 0 && gateway_active_streams > 0`이 정상
상태일 수 있다(예: 느린 client에게 아직 draining 중이지만 Mock LLM upstream은 이미 끝난 경우) —
이를 leak이나 invariant 위반으로 오판하지 않는다.

```
0 <= gateway_admission_active <= admission_ceiling

postflight (drain 완료 후):
  gateway_active_streams == 0
  gateway_upstream_active == 0
  gateway_admission_active == 0

P3-A만:
  outbound_blocking_executor_active == 0

P3-A/B:
  servlet_write_executor_active == 0
  servlet_write_executor_queue_depth == 0
  servlet_write_stream_buffered_frames == 0

P3-B/C:
  reactor_netty_connection_provider_pending_connections == 0
```

terminal outcome counter(`gateway_requests{outcome}`)는 요청당 정확히 1회 증가(phase3-design.md
§5, Phase 1/2 `docs/decisions/request-outcome-accounting.md`의 exactly-once invariant를
Micrometer 버전으로 승계):

```
gateway_requests_started_total == sum(gateway_requests_total{outcome=*})
```

이 두 invariant는 Unit 5 functional screening부터 매 run 종료 후 검증한다(Phase 2
`scripts/verify-outcome-invariant.sh`와 동일한 역할을 하는 스크립트를 Unit 2 이후 Prometheus
HTTP API 대상으로 재작성 — Micrometer/Actuator가 `/actuator/prometheus`로 노출하는 이름
(`gateway_requests_total{outcome="..."}` 등)을 그대로 query 대상으로 쓴다).

## 7. Correlation ID는 metric 태그로 사용하지 않는다

phase3-design.md §3-4의 correlation ID(Gateway 자체 `requestId`)는 로그 전용이며, 어떤 metric의
라벨로도 사용하지 않는다(§3-1 high-cardinality 금지 원칙과 동일 이유).

## 8. Unit 5 — Server Latency Metric Boundary Audit 및 Client-side Authoritative 결정

Unit 5 cross-validation에서 세 구현체의 실제 코드를 확인한 결과(추측 아님),
`gateway_first_chunk_relay_seconds`/`gateway_stream_duration_seconds`의 **물리적 경계가 구조적으로
다르다**:

| | `recordFirstChunkIfNeeded()` 호출 위치 | 실제 의미 |
|---|---|---|
| P3-A/B | `PerStreamWriteChannel.doWrite()` — `writer.write(frame); writer.flush();` **실행 직후** | "Servlet write executor가 첫 프레임을 PrintWriter에 실제로 쓴 시점" |
| P3-C | `ChatController`의 response Flux `.doOnNext()` — WebFlux 자체 SSE encoder/Reactor Netty channel write **이전** | "첫 SSE event가 response Flux에 emit된 시점(그 뒤 WebFlux/Netty 인코딩·쓰기 단계가 아직 남아있음)" |

즉 P3-A/B는 "우리 실행 경로의 마지막 쓰기 동작 직후"를 재는 반면, P3-C는 "그보다 한 단계 이른"
시점을 잰다 — 두 그룹 다 실제 client socket에 bytes가 나간 시점은 아니다(PrintWriter/Reactor
Netty 모두 네트워크 전달을 보장하지 않음, Unit 2 §15/Unit 4 문서에서 이미 명시). 이 구조적 차이를
없애기 위해 P3-C에 인위적인 write executor/buffer를 추가하거나 P3-A/B의 lifecycle을 바꾸지
않는다(Unit 5 §18 — 그렇게 하면 Experiment B 자체가 왜곡된다).

`gateway_stream_duration_seconds`도 동일한 구조적 차이를 갖는다: P3-A/B는 "upstream complete →
남은 Servlet write drain → 최종 write → terminal"이고, P3-C는 "response Publisher가 스스로
terminal 신호를 낸 시점"이다 — 물리적 completion boundary가 동일하다고 가정하지 않는다.

### 8-1. 결정: Formal authoritative latency source = client-side (k6)

**Primary(Formal 비교의 authoritative source):**

- client TTFC (xk6-sse 등 client가 관찰한 first event 수신 시각)
- client total stream duration (client가 관찰한 stream 종료 시각)

**Server-side (`gateway_first_chunk_relay_seconds`, `gateway_stream_duration_seconds`)는
implementation diagnostic로만 사용한다** — cross-implementation(P3-A vs B vs C) 성능 비교의
근거로 쓰지 않는다. 같은 구현체 내부에서(예: P3-A의 run-to-run 변화 추적) diagnostic 용도로는
계속 유효하다.

### 8-2. `gateway_bytes_relayed_total` 계산 방식 audit

실제 코드 확인 결과:

- P3-A/B: `PerStreamWriteChannel.doWrite()`에서 `metrics.bytesRelayed.increment(frame.length())` —
  `frame`은 실제로 `PrintWriter`에 쓴(따라서 실제 wire에 나간) 정확한 문자열
  (`"event: <name>\ndata: <payload>\n\n"`, 콜론 뒤 공백 포함)의 `String.length()`.
- P3-C: `ChatController`의 `.doOnNext()`에서
  `metrics.bytesRelayed.increment(SseFrameFormatter.format(sse).length())` — `SseFrameFormatter`는
  **실제 응답 바디에는 쓰이지 않는** 진단 전용 재구성 문자열이며, 같은 형식
  (`"event: <name>\ndata: <payload>\n\n"`, 콜론 뒤 공백 포함)을 사용한다. 그러나 P3-C의 **실제
  wire 출력**은 WebFlux 자체 인코더가 만들며 콜론 뒤 공백이 없다(`"event:<name>\ndata:<payload>\n\n"`,
  Unit 4 Smoke A에서 실측 확인) — 즉 `gateway_bytes_relayed_total`의 계산 **공식**은 세 구현체가
  동일하지만(같은 포맷 문자열의 길이), P3-C에서는 그 공식이 **실제 wire bytes와 정확히 일치하지
  않는다**(event당 2바이트씩 실제보다 많이 계산됨) — P3-A/B는 정확히 일치한다.

**결론**: `gateway_bytes_relayed_total`을 cross-implementation 성능/정확도 metric으로 사용하지
않는다 — data-integrity 확인(0이 아님, 대략적인 규모)이나 같은 구현체 내부 diagnostic 용도로만
사용한다. 이 차이를 없애기 위해 P3-C의 encoding 구조(WebFlux 네이티브 encoder 사용)를 바꾸지
않는다(Unit 3에서 이미 결정한 "P3-B처럼 문자열을 재구성해 raw write하지 않는다" 원칙을 유지).

### 8-3. F5(upstream connection failure) client-visible 차이 — 기록, 수정 안 함

Unit 5 F5 scenario(Mock LLM을 listen 중이 아닌 로컬 포트로 지정, connection refused) 실측:

| | HTTP status | body |
|---|---|---|
| P3-A/B | `200 OK` | 빈 body(스트림이 아무 이벤트 없이 즉시 종료) |
| P3-C | `500 Internal Server Error` | Boot 기본 에러 JSON(`{"status":500,"error":"Internal Server Error",...}`) |

원인: P3-A/B는 admission 성공 직후 `response.setContentType("text/event-stream")` +
`AsyncContext` 시작으로 응답이 사실상 즉시 커밋되므로(Servlet 모델), 이후 upstream 연결 실패가
HTTP status를 바꿀 수 없다 — 이미 200으로 커밋된 뒤라 빈 스트림으로 끝날 뿐이다. P3-C는 reactive
chain이 실제로 subscribe되어 최초 신호(성공 또는 에러)가 나올 때까지 응답 committal이 자연스럽게
지연되므로, subscribe 직후 발생하는 connection-refused가 WebFlux의 기본 에러 처리로 흘러가
실제 HTTP 500 에러 응답이 된다.

이 차이는 두 아키텍처의 근본적인 차이(Servlet 조기 commit vs reactive lazy commit)를 그대로
반영하는 것이며, 셋 중 하나가 "틀린" 것이 아니다 — 맞추기 위해 어느 쪽도 수정하지 않는다(Unit 5
§12/§28). Client-visible contract 비교표(`docs/test-results/phase3/unit5-cross-validation/
contract-matrix.md`)에 그대로 기록한다.

### 8-4. F3(admission reject) Content-Type 차이 — 기록, 수정 안 함

같은 이유로, admission reject 응답의 `Content-Type` 헤더도 다르다: P3-A/B는
`text/event-stream;charset=UTF-8`(admission 체크 이전에 이미 설정된 값이 그대로 남음 — Phase 1/2
원본 코드부터 이어진 동작), P3-C는 `application/json`(reject 분기에서 명시적으로 설정). 두 경우
모두 **body 자체와 HTTP status(503)는 동일**하다 — Content-Type 헤더만 다르다. Unit 1이 freeze한
계약은 status/body이지 이 헤더가 아니므로 위반이 아니며, Phase 1/2 원본 동작을 보존하기 위해
P3-A/B를 수정하지 않는다.
