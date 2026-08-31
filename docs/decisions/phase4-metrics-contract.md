# ADR: Phase 4 Metric Contract (Micrometer)

Status: Accepted (Phase 4 Unit 1 scope — contract freeze). **Unit 2 update**: all
framework-provided metric names in §4 have now been CONFIRMED by actually scraping
`/actuator/prometheus` on live M1/M2/M3 instances (Boot 4.1.0 / Micrometer 1.17.0 / Tomcat 11.0.22
/ Reactor Netty 1.3.6) — see `docs/test-results/phase4/unit2-functional/metrics-parity.md` for the
authoritative confirmed table and two Unit-2 findings this ADR did not anticipate: (1)
`reactor_netty_connection_provider_*`/`reactor_netty_http_server_*` require explicit
`.metrics(true, ...)` on both the WebClient side and the embedded server (not on by default even
with Actuator present) — implemented in M3's `WebClientConfig`/`NettyServerMetricsConfig`; (2)
Micrometer exposes an open-FD gauge (`process_files_open_files`/`process_files_max_files`) that §10
did not know about when it proposed an `lsof`-based sampler. §4's text below is retained as the
Unit 1 historical record of what was still unconfirmed at freeze time.
Date: 2026-08-25 (Unit 1), confirmed 2026-08-25 (Unit 2)

## 1. Micrometer로 통일 — 결정

Phase 4 세 모델(M1/M2/M3) 모두 Boot 4.1.0 + Micrometer 1.17.0을 공유한다(Unit 0 실측,
`docs/decisions/phase4-runtime.md` §5). Phase 1/2가 쓴 `io.prometheus:simpleclient`는 Boot 4.1.0
위에서 검증되지 않았고(Unit 0에서 확인하지 않음), Micrometer는 M3(WebFlux) 스파이크에서 실제
빌드/기동이 확인된 유일한 metrics 경로다 — **Phase 4는 Micrometer로 통일한다.**
`io.prometheus:simpleclient`와 Micrometer를 구현별로 섞지 않는다.

Export: `/actuator/prometheus`(Prometheus scrape format), `spring-boot-starter-actuator`를
M1/M2/M3 공통 의존성에 추가한다(Unit 2 구현 범위 — 이번 Unit에서 코드는 만들지 않는다).

## 2. Custom Metric 이름 변환 규칙 (Phase 3에서 실측 확인된 일반 Micrometer 규칙, 재사용)

```java
registry.counter("gateway.requests.started")     // dotted name
registry.timer("gateway.stream.duration")
```

Prometheus 노출 시: `.` → `_`, `Counter`는 `_total` suffix, `Timer`는
`_seconds_{count,sum,max}`(+percentile histogram 설정 시 `_seconds_bucket`) suffix. 이 변환
규칙은 Micrometer의 `PrometheusMeterRegistry` naming convention으로, 특정 Boot/Micrometer patch
버전에 의존하지 않는 안정적인 동작이다(Phase 3, Micrometer 1.9.17에서 실측 확인 —
`docs/decisions/phase3-metrics-contract.md` §2-1). **우리가 직접 정의하는 custom metric의
이름/존재는 이 규칙에 따라 확정적으로 결정되므로, 아래 §3의 custom metric 목록은 지금 freeze한다.**

## 3. Common Custom Metrics — freeze

| 이름(dotted) | 종류 | 라벨 | 의미 |
|---|---|---|---|
| `gateway.requests.started` | Counter | — | §phase4-design.md §9의 application lifecycle start 시점(request body decode 성공 직후) |
| `gateway.requests` | Counter | `outcome` | terminal outcome, 요청당 정확히 1회(§outcome invariant, §5) |
| `gateway.active.requests` | Gauge | — | 아직 terminal되지 않은 요청 수(M1의 queue 대기 포함 — §9 lifecycle 정의와 일치) |
| `gateway.upstream.active` | Gauge | — | Mock LLM과 활성 연결/구독 수 |
| `gateway.bytes.relayed` | Counter | — | client로 relay된 바이트 수(cross-model 정확도 비교 근거로 쓰지 않음 — Phase 3 Unit 5 §8-2와 동일 caveat, M3의 native encoder가 M1/M2의 수동 포맷 문자열과 바이트 단위로 다를 수 있음) |
| `gateway.first.upstream.event.seconds` | Timer | — | 요청 시작 → 첫 upstream 이벤트 수신(diagnostic — §Metrics Authority) |
| `gateway.request.duration.seconds` | Timer | — | 요청 시작 → terminal(diagnostic) |

`outcome` 라벨 값 집합(freeze): `completed`, `rejected`, `timeout`, `upstream_error`,
`client_disconnect`, `write_overflow`(M1/M2만 실제 발생 가능 — M3는 §6 참고), `internal_error`.

### 3-1. High-cardinality 태그 금지

`requestId`/`url`/`remote_address` 등 요청마다 달라지는 값을 custom metric 태그로 넣지 않는다
(Phase 3 원칙 계승, `docs/decisions/phase3-metrics-contract.md` §3-1). Reactor Netty/Tomcat이
자체적으로 붙이는 라벨(§4)은 예외로 허용한다(Mock LLM 하나만 upstream이라 cardinality가 낮게
유지됨).

## 4. Framework-provided Metrics — Unit 2 재확인 필요 (지금 freeze하지 않음)

Phase 3가 Micrometer 1.9.17 / Boot 2.7.18 / Tomcat 9.0.83 / Reactor Netty 1.0.39에서 실제
`/actuator/prometheus`를 스크레이프해 확인한 이름들이다(`docs/decisions/
phase3-metrics-contract.md` §2-2, §2-3). Phase 4는 **다른 버전**(Micrometer 1.17.0 / Tomcat
11.0.22 / Reactor Netty 1.3.6)을 쓰므로, 아래는 **Unit 2에서 Phase 4 stack으로 재확인해야 하는
후보(candidate)**이며 이 문서가 지금 확정하는 계약이 아니다. **이름을 추측해서 계약에 넣지
않는다** — Unit 2가 실제 capability spike(Phase 3 Unit 1이 했던 것과 동일한 방식,
`docs/test-results/phase3/unit1-capability-spikes/` 패턴 재사용 후보)로 재확인한 뒤에만 §5/§6/§7
표에 최종 반영한다.

Phase 3에서 확인됐던 후보 이름(재확인 필요):

```
tomcat_threads_busy_threads{name}
tomcat_threads_current_threads{name}
tomcat_threads_config_max_threads{name}
tomcat_connections_current_connections{name}
tomcat_global_request_seconds_{count,sum}{name}

reactor_netty_connection_provider_active_connections{id,name,remote_address}
reactor_netty_connection_provider_idle_connections{id,name,remote_address}
reactor_netty_connection_provider_pending_connections{id,name,remote_address}
reactor_netty_connection_provider_max_connections{id,name,remote_address}

jvm_threads_live_threads / jvm_threads_daemon_threads / jvm_threads_peak_threads
jvm_memory_used_bytes / jvm_memory_max_bytes{area="heap"}
jvm_gc_pause_seconds_{count,sum,max}
system_cpu_usage / process_cpu_usage / system_cpu_count
```

**`reactor_netty_http_server_*`(M3 서버 측)는 Phase 3에서도 확인되지 않았다** — Phase 3 P3-C
스파이크가 이 이름을 관찰한 적이 없으므로(§2-4 "이번 spike에서 미확인"), Phase 4 M3 서버 metric은
Unit 2에서 **처음부터** 확인해야 한다. 완전히 새로운 이름일 수 있다.

**주의(§Unit 0 design brief §20/§46 원칙)**: `process_cpu_seconds_total`(Phase 1/2가 쓴
`io.prometheus:simpleclient`의 `StandardExports` 전용 metric, Linux `/proc` 기반)은 Micrometer
경로에는 존재하지 않는다 — Micrometer의 CPU metric은 `ProcessorMetrics` binder가 노출하는
`process_cpu_usage`(0.0~1.0 비율, cumulative seconds 아님)로 **계산 공식 자체가 다르다**. 이
차이를 인지하지 못한 채 Phase 1/2 방식의 avg-cores 계산식을 그대로 옮기지 않는다 — Unit 2에서
실제 값/semantic을 확인한 뒤 CPU 계산 공식을 정의한다.

## 5. M1-specific Metrics — freeze(logical name) / Tomcat 이름은 §4 재확인 대상

| 이름(dotted) | 종류 | 의미 |
|---|---|---|
| `executor.active` | Gauge | `ThreadPoolExecutor.getActiveCount()` |
| `executor.pool.size` | Gauge | `getPoolSize()`(고정 50이므로 사실상 상수, invariant 확인용) |
| `executor.queue.depth` | Gauge | `getQueue().size()`(`ArrayBlockingQueue`, §phase4-design.md §4-1) |
| `executor.queue.capacity` | Gauge | 상수 500(§phase4-design.md §4-3), invariant 확인용 |
| `executor.rejected` | Counter | `AbortPolicy` 발동 횟수 |
| `executor.queue.wait.seconds` | Timer | task가 queue에 제출된 뒤 실제 worker에서 시작될 때까지의 대기 시간 — H4-b의 핵심 근거 |
| `executor.task.duration.seconds` | Timer | worker가 task를 실제로 수행한 시간(`HttpURLConnection` 호출+SSE relay) |

Tomcat 관련(`tomcat_threads_busy_threads` 등)은 §4 candidate를 Unit 2에서 재확인 후 추가.

## 6. M2-specific Metrics — freeze(logical name)

| 이름(dotted) | 종류 | 의미 |
|---|---|---|
| `virtual.tasks.active` | Gauge | 현재 실행 중인 virtual thread task 수(request당 1개) |
| `virtual.tasks.started` | Counter | virtual thread task 생성 총합 |

JVM live platform thread는 §4의 `jvm_threads_live_threads`(candidate, Unit 2 확인 후) 공통
metric을 그대로 사용한다 — M2 전용 metric으로 중복 정의하지 않는다. Virtual task terminal
outcome은 `gateway.requests{outcome}`(§3)과 중복하지 않도록 별도 outcome counter를 만들지
않는다. **JFR는 Phase 4 Primary에 포함하지 않는다**(design brief 원칙 계승).

## 7. M3-specific Metrics — policy only, 이름은 Unit 2 runtime 확인 후 확정

실제 `/actuator/prometheus` 출력에 **존재하는 metric만** 계약에 추가한다(§4). 후보 의미(이름은
미확정):

- client(WebClient) active connections / idle connections / pending acquire connections
- server(Reactor Netty) connections

M3에는 application write queue가 없으므로 `write_overflow` outcome이나 `executor.*` 계열
metric을 인위적으로 만들지 않는다 — 없는 failure mode를 억지로 만들지 않는다(§phase4-design.md
§7 원칙과 동일).

## 8. Metrics Authority — Primary vs Diagnostic

- **Primary capacity 지표**: MSC, MRC, MSAR(`docs/decisions/phase4-scalability-definition.md`).
- **Primary client SLO**: k6 TTFC, k6 total stream duration(client-side authoritative,
  `phase4-scalability-definition.md` §1).
- **Primary system 지표**: platform threads(§4 candidate), CPU(§4 candidate, 공식 재확인 후),
  RSS(§9 native sampler), FD(§10).
- **Secondary**: heap, GC, architecture-specific metric(executor/virtual-task/reactor 세부
  지표) — boundary 원인 설명(H4-e)에는 쓰이지만 SLO 판정에는 쓰이지 않는다.
- **Server internal latency timer(`gateway.first.upstream.event.seconds`,
  `gateway.request.duration.seconds`)는 diagnostic only** — cross-model 성능 비교의 근거로
  쓰지 않는다(Phase 3 Unit 5 §8-1과 동일 결정, 서로 다른 architecture가 서로 다른 물리적
  경계에서 이 값을 측정할 가능성이 높기 때문 — Unit 2 구현 후 실제로 다른지 확인).

## 9. RSS Policy

Native macOS process RSS를 authoritative memory metric으로 사용한다(Micrometer/Prometheus의
`process_resident_memory_bytes`는 Linux `/proc` 전용이라 macOS에서 노출되지 않음 — Phase 2가 이미
확인한 사실, `docs/decisions/phase2-formal-native-macos-environment.md` §1-1, Phase 4에도 동일하게
적용).

Phase 2/3에서 검증한 패턴(Gateway PID를 실제로 resolve한 뒤 `ps -o rss= -p <pid>` 주기 샘플링)을
재사용 후보로 채택한다. JVM heap과 RSS는 구분되는 지표로 유지한다(heap은 §4 candidate
`jvm_memory_used_bytes`, RSS는 OS-level 전체 process footprint). **PID match는 Unit 4 harness
Canary에서 반드시 검증한다.**

## 10. FD Policy

Phase 4에서 새롭게 중요한 Primary resource다(Unit 0 §12 OS 정책과 연결). 각 Gateway process의
open FD count를 측정한다 — `lsof` 또는 동등 macOS 명령 후보. **Sampling overhead를 먼저 확인한다**
— 1Hz가 과하면 더 낮은 frequency를 쓴다. 정확한 방식/주기는 Unit 3 capability calibration에서
확정한다(이번 Unit에서 숫자를 freeze하지 않음).

**Unit 2 amendment**: 실제 `/actuator/prometheus` 확인 결과 Micrometer가 이미
`process_files_open_files`/`process_files_max_files` gauge를 기본 제공한다(FileDescriptorMetrics
binder, Boot Actuator 자동 등록, `docs/test-results/phase4/unit2-functional/metrics-parity.md`에서
세 모델 전부 확인). 이는 §10 작성 시점(Unit 1)에는 몰랐던 사실이다. **Unit 3에서 이 gauge를
1차 후보로 검토**하고, `lsof` 기반 sampler는 이 gauge의 정확도/overhead가 불충분할 때만 보조
수단으로 검토한다 — 무조건 외부 프로세스 sampler를 새로 만들 필요가 없을 수 있다.

## 11. Connection/Socket Policy

OS connection count(`ESTABLISHED`/`TIME_WAIT` 등)를 완벽한 Primary metric으로 만들 필요는 없다
— 고 concurrency에서 diagnostic snapshot 정도로 남긴다. Sampling overhead가 크면 Formal
continuous metric으로 강제하지 않는다.

## 12. CPU Policy — 공식 미확정 (freeze하지 않음)

§4에서 이미 명시한 대로, `process_cpu_usage`(Micrometer `ProcessorMetrics`)의 정확한 semantic과
`availableProcessors()`(이 host: 10, Unit 0 §OS snapshot)와의 관계를 실제 Unit 2 runtime으로
확인한 뒤에만 avg-cores 계산 공식을 정의한다. 이번 Unit에서는 **"존재하지 않는 metric을 가정하지
않는다"는 정책만 freeze**한다(Phase 3 Canary가 겪은 "잘못된 metric 가정" 교훈,
`docker_desktop_clock_drift`/`prometheus_histogram_bucket_precision` 메모리와 동일한 종류의
실수를 반복하지 않기 위함).

## 13. Histogram/Timer Bucket

M1/M2/M3에서 **반드시 동일한** bucket 경계를 쓴다 — cross-model 비교의 전제조건(Phase 2 Unit
6.7의 "bucket 정밀도가 다르면 p95/p99가 조용히 틀어진다" 교훈, 메모리 `prometheus_histogram_
bucket_precision` 참고). 정확한 bucket 배열은 Phase 2의 `GatewayMetrics.FINE_LATENCY_BUCKETS`를
출발점으로 재사용할지, Micrometer `distributionStatisticConfig` 방식으로 바꿀지 **Unit 2 구현
시** 확정한다(Phase 3와 동일 유예 원칙, `phase3-metrics-contract.md` §5) — **Unit 5 screening
시작 전에는 반드시 확정하고, Formal 시작 후 변경하지 않는다.**

## 14. Metric Invariants (Formal 전 반드시 검증, Unit 2 이후 실제 스크립트로 구현)

```
gateway_requests_started_total == sum(gateway_requests_total{outcome=*})   (exactly-once, 요청당 정확히 1회)

postflight (drain 완료 후):
  gateway_active_requests == 0
  gateway_upstream_active == 0

M1만:
  executor_active == 0
  executor_queue_depth == 0

M1/M2 공통(write path):
  <write executor active/queue depth 관련 metric> == 0  (§phase4-design.md §6, 실제 metric 이름은 Unit 2에서 §5/§6 표에 추가)

M3만:
  <reactor_netty_connection_provider_pending_connections 또는 동등 candidate> == 0  (§4 확인 후)
```

## 15. Correlation ID

Correlation ID(Gateway 자체 `requestId`)는 로그 전용이며 어떤 metric의 라벨로도 사용하지
않는다(§3-1과 동일 이유, Phase 3 원칙 계승).
