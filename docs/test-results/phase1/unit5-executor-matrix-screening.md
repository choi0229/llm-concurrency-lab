# Unit 5 — Executor Matrix Screening 결과

Status: Screening 완료 (정식 5분×3회 Benchmark 아님)
Date: 2026-08-11

목적 재확인: 특정 Executor 구성이 "가장 빠르다"를 가리는 것이 아니라, `ThreadPoolExecutor`의
core/max/Queue/RejectionPolicy가 포화 양상을 어떻게 바꾸는지 실측하는 것.

## 0. 공통 조건 (모든 run 동일)

- Mock LLM workload: `load-test-k6/scenarios/01-concurrent-connections.js` 기본값 그대로
  (`CHUNK_COUNT=20`, `CHUNK_INTERVAL_MS=200`, `FIRST_CHUNK_DELAY_MS=1000`, `chunkSizeBytes=64`) —
  성공 시 스트림 길이 ≈ 1000+19×200 ≈ 4.8s
- Gateway timeout: `CHAT_CONNECT_TIMEOUT_MS=3000`, `CHAT_READ_TIMEOUT_MS=30000`,
  `CHAT_TOTAL_TIMEOUT_MS=60000` (docker-compose.yml 기본값과 동일, Executor 설정만 변경)
- CPU/Memory limit: `docker-compose.yml` 그대로 (gateway `cpus:1.0/mem_limit:1g`,
  mock-llm `cpus:2.0/mem_limit:1g`)
- Prometheus scrape interval: 5s (`docs/decisions/monitoring-baseline.md` 고정값)
- k6 이미지: `load-test-k6/Dockerfile`(k6 v1.8.0 + xk6-sse v0.1.11, 이번에 `-o
  experimental-prometheus-rw`로 Client 지표도 Prometheus에 적재 — k6 core 내장 output이라 xk6-sse의
  고정 module 경로와 충돌하지 않음, 실제 실행으로 확인)
- 각 Concurrency(= k6 VUS, closed-model 단발 wave)마다 **mock-llm + gateway 컨테이너를 완전히
  재생성**한 뒤 실행 — 이전 run의 누적 Counter/피크 Gauge가 다음 run에 섞이지 않도록 함
  (`scripts/run-screening.sh`)
- 각 run 종료(drain 완료) 후 `scripts/collect_screening_result.py`로 결과 수집 +
  `docs/decisions/request-outcome-accounting.md`의 invariant 자동 검증

## 1. Executor 구성 (실제 적용된 환경변수)

| config | CHAT_CORE_POOL_SIZE | CHAT_MAX_POOL_SIZE | CHAT_QUEUE_CAPACITY | CHAT_REJECTION_POLICY | Queue 종류(코드상) | Concurrency 목록 |
|---|---|---|---|---|---|---|
| A | 10 | 10 | 100 | CALLER_RUNS | `LinkedBlockingQueue(100)` | 10, 30, 100, 120, 150 |
| B | 10 | 10 | 20 | ABORT | `LinkedBlockingQueue(20)` | 10, 20, 30, 40, 60 |
| D | 10 | 50 | 20 | ABORT | `LinkedBlockingQueue(20)` | 10, 30, 40, 50, 70, 80 |
| E | 10 | 50 | 0 | ABORT | `SynchronousQueue`(`CHAT_QUEUE_CAPACITY<=0`이면 `ChatExecutorConfig`가 자동 선택) | 10, 20, 30, 50, 60 |

## 2. Mock LLM이 병목이 아니었다는 근거

**21개 run 전부에서 `mockllm_waiting_requests`의 run-구간 내 최댓값(peak)이 0이었다** (Table 5). Mock LLM은
`MOCK_LLM_MAX_CONCURRENT_PROCESSING=0`(무제한)으로 고정 운영했고, `cpus:2.0`로 Gateway(`cpus:1.0`)보다
넉넉하게 배정되어 있어 — 이번 Screening에서 관찰된 모든 queueing/rejection/caller-runs 현상은 전적으로
Gateway의 `ThreadPoolExecutor` 설정에서 비롯된 것이며, Mock LLM 쪽 admission control이 결과에 섞이지
않았다.

## 3. 결과 표 (Section 6 요구 컬럼 전부 포함, 5개 표로 분리)

(자세한 원본 데이터: `docs/test-results/phase1/unit5-raw/results.ndjson`, 각 run의 k6 요약:
`docs/test-results/phase1/unit5-raw/*-k6-summary.json`)

### Table 1 — Outcomes (요청 결과 분해, authoritative `gateway_request_outcome_total` 기준)

| config | concurrency | accepted | completed | failed | rejected | timeout | stale (diag) | caller_runs |
|---|---|---|---|---|---|---|---|---|
| A | 10 | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| A | 30 | 30 | 30 | 0 | 0 | 0 | 0 | 0 |
| A | 100 | 100 | 100 | 0 | 0 | 0 | 0 | 0 |
| A | 120 | 120 | 120 | 0 | 0 | 0 | 0 | 10 |
| A | 150 | 150 | 150 | 0 | 0 | 0 | 0 | 40 |
| B | 10 | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| B | 20 | 20 | 20 | 0 | 0 | 0 | 0 | 0 |
| B | 30 | 30 | 30 | 0 | 0 | 0 | 0 | 0 |
| B | 40 | 30 | 30 | 0 | 10 | 0 | 0 | 0 |
| B | 60 | 30 | 30 | 0 | 30 | 0 | 0 | 0 |
| D | 10 | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| D | 30 | 30 | 30 | 0 | 0 | 0 | 0 | 0 |
| D | 40 | 40 | 40 | 0 | 0 | 0 | 0 | 0 |
| D | 50 | 50 | 50 | 0 | 0 | 0 | 0 | 0 |
| D | 70 | 70 | 70 | 0 | 0 | 0 | 0 | 0 |
| D | 80 | 70 | 70 | 0 | 10 | 0 | 0 | 0 |
| E | 10 | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| E | 20 | 20 | 20 | 0 | 0 | 0 | 0 | 0 |
| E | 30 | 30 | 30 | 0 | 0 | 0 | 0 | 0 |
| E | 50 | 50 | 50 | 0 | 0 | 0 | 0 | 0 |
| E | 60 | 50 | 50 | 0 | 10 | 0 | 0 | 0 |

### Table 2 — Executor Saturation (peak, 그 run 구간 내 최댓값)

| config | concurrency | pool_size_peak | active_threads_peak | queue_size_peak | async_active_peak | platform_thread_peak |
|---|---|---|---|---|---|---|
| A | 10 | 10 | 10 | 0 | 10 | 32 |
| A | 30 | 10 | 10 | 20 | 30 | 53 |
| A | 100 | 10 | 10 | 90 | 100 | 104 |
| A | 120 | 10 | 10 | 100 | 120 | 139 |
| A | 150 | 10 | 10 | 100 | 150 | 151 |
| B | 10 | 10 | 10 | 0 | 10 | 32 |
| B | 20 | 10 | 10 | 10 | 20 | 42 |
| B | 30 | 10 | 10 | 20 | 30 | 53 |
| B | 40 | 10 | 10 | 20 | 30 | 63 |
| B | 60 | 10 | 10 | 20 | 30 | 83 |
| D | 10 | 10 | 10 | 0 | 10 | 32 |
| D | 30 | 10 | 10 | 20 | 30 | 52 |
| D | 40 | 20 | 20 | 20 | 40 | 72 |
| D | 50 | 30 | 30 | 20 | 50 | 82 |
| D | 70 | 50 | 50 | 20 | 70 | 117 |
| D | 80 | 50 | 50 | 20 | 70 | 106 |
| E | 10 | 10 | 10 | 0 | 10 | 33 |
| E | 20 | 20 | 20 | 0 | 20 | 53 |
| E | 30 | 30 | 30 | 0 | 30 | 73 |
| E | 50 | 50 | 50 | 0 | 50 | 109 |
| E | 60 | 50 | 50 | 0 | 50 | 98 |

### Table 3 — Latency (초 단위, p50/p95/p99)

| config | concurrency | TTFC p50 | TTFC p95 | TTFC p99 | QueueWait(completed) p50 | p95 | p99 | Gateway TTFB p95 |
|---|---|---|---|---|---|---|---|---|
| A | 10 | 1.04 | 1.04 | 1.04 | – | – | – | – |
| A | 30 | 5.98 | 10.86 | 10.86 | 5.00 | 9.50 | 9.90 | 28.00 |
| A | 100 | 23.17 | 45.09 | 45.11 | 22.50 | 55.50 | 59.10 | 30.00 |
| A | 120 | 23.24 | 50.05 | 50.05 | 25.00 | 56.25 | 59.25 | 30.00 |
| A | 150 | 15.97 | 50.09 | 50.10 | 25.00 | 56.25 | 59.25 | 30.00 |
| B | 10 | 1.05 | 1.05 | 1.05 | – | – | – | 2.42 |
| B | 20 | 3.49 | 5.93 | 5.93 | 3.75 | 4.88 | 4.97 | 9.75 |
| B | 30 | 5.98 | 10.86 | 10.86 | 5.00 | 9.50 | 9.90 | 28.00 |
| B | 40 | 5.95 | 10.82 | 10.82 | 5.00 | 9.50 | 9.90 | 28.00 |
| B | 60 | 6.09 | 10.95 | 10.95 | 5.00 | 9.50 | 9.90 | 27.00 |
| D | 10 | 1.05 | 1.05 | 1.05 | 0.00 | 0.00 | 0.00 | 2.42 |
| D | 30 | 5.94 | 10.80 | 10.80 | 5.00 | 9.50 | 9.90 | 27.00 |
| D | 40 | 3.54 | 6.02 | 6.02 | 3.75 | 4.88 | 4.97 | 9.75 |
| D | 50 | 1.12 | 5.99 | 5.99 | 3.75 | 4.88 | 4.97 | 9.38 |
| D | 70 | 1.23 | 6.10 | 6.10 | 3.75 | 4.88 | 4.97 | 9.12 |
| D | 80 | 1.19 | 6.11 | 6.11 | 7.37 | 9.74 | 9.95 | 9.12 |
| E | 10 | 1.05 | 1.05 | 1.05 | – | – | – | – |
| E | 20 | 1.11 | 1.11 | 1.11 | – | – | – | – |
| E | 30 | 1.13 | 1.13 | 1.13 | – | – | – | 2.42 |
| E | 50 | 1.24 | 1.24 | 1.25 | 0.00 | 0.00 | 0.00 | 2.42 |
| E | 60 | 1.18 | 1.18 | 1.18 | 0.00 | 0.00 | 0.00 | 2.43 |

### Table 4 — Resource (CPU/Memory)

| config | concurrency | CPU (avg core-fraction) | Heap peak (MB) | RSS peak (MB) |
|---|---|---|---|---|
| A | 10 | 1.9% | 19.9 | 132.8 |
| A | 30 | 3.1% | 25.9 | 141.5 |
| A | 100 | 3.4% | 33.8 | 165.0 |
| A | 120 | 3.0% | 38.1 | 173.7 |
| A | 150 | 2.8% | 49.5 | 192.0 |
| B | 10 | 3.9% | 22.6 | 140.9 |
| B | 20 | 2.2% | 22.8 | 138.2 |
| B | 30 | 2.9% | 25.6 | 140.3 |
| B | 40 | 3.7% | 25.0 | 146.4 |
| B | 60 | 3.0% | 26.5 | 151.9 |
| D | 10 | 6.6% | 22.4 | 125.2 |
| D | 30 | 5.7% | 24.9 | 146.5 |
| D | 40 | 4.3% | 25.1 | 153.1 |
| D | 50 | 4.3% | 26.6 | 151.3 |
| D | 70 | 7.7% | 29.4 | 165.7 |
| D | 80 | 9.4% | 31.0 | 163.1 |
| E | 10 | 2.2% | 22.4 | 138.9 |
| E | 20 | 3.0% | 18.7 | 138.7 |
| E | 30 | 5.3% | 25.3 | 143.9 |
| E | 50 | 7.4% | 23.9 | 157.4 |
| E | 60 | 9.3% | 27.1 | 154.0 |

### Table 5 — Mock LLM bottleneck check (모든 run)

| config | concurrency | mock_waiting_peak | mock_bottleneck | invariant_ok |
|---|---|---|---|---|
| A | 10 | 0 | False | True |
| A | 30 | 0 | False | True |
| A | 100 | 0 | False | True |
| A | 120 | 0 | False | True |
| A | 150 | 0 | False | True |
| B | 10 | 0 | False | True |
| B | 20 | 0 | False | True |
| B | 30 | 0 | False | True |
| B | 40 | 0 | False | True |
| B | 60 | 0 | False | True |
| D | 10 | 0 | False | True |
| D | 30 | 0 | False | True |
| D | 40 | 0 | False | True |
| D | 50 | 0 | False | True |
| D | 70 | 0 | False | True |
| D | 80 | 0 | False | True |
| E | 10 | 0 | False | True |
| E | 20 | 0 | False | True |
| E | 30 | 0 | False | True |
| E | 50 | 0 | False | True |
| E | 60 | 0 | False | True |

**"failed" 컬럼 정의**: `upstream_timeout + upstream_error + client_disconnect + unexpected_error`
(authoritative `gateway_request_outcome_total`의 fine-grained outcome을 결합). **"timeout" 컬럼**:
`timeout_before_start + deadline_exceeded`. **"stale"는 diagnostic** `gateway_stale_task_skipped_total`
(위 timeout과 상당 부분 겹치는 진단용 카운터 — `docs/decisions/request-outcome-accounting.md` 참고).
21개 run 전부 `failed=0`, `timeout=0` — 이번 Screening 범위(Concurrency ≤150, `CHAT_TOTAL_TIMEOUT_MS=60s`)
안에서는 진짜 실패나 타임아웃이 발생하지 않았다. Queue Wait failed 계열 표를 넣지 않은 이유도 동일(전부
표본 0).

## 4. 가설 검증

### H1-a — Bounded Queue + core<max: queue가 먼저 차고, 그 다음에야 pool이 는다 (Config D)

Table 2의 D 행에서 그대로 드러난다:

- Concurrency=10 (≤ core): `pool_size_peak=10`, `queue_size_peak=0` — queue를 전혀 안 씀
- Concurrency=30 (core+queue=30 이하): **`pool_size_peak`는 여전히 10** (안 늘어남), `queue_size_peak=20`
  (queue가 꽉 참) — core를 넘는 초과분이 전부 queue로 흡수됨, pool은 그대로
- Concurrency=40 (core+queue=30을 초과): 그제서야 `pool_size_peak=20`으로 증가 — queue가 이미 꽉 찬
  뒤에야 pool이 늘어남
- Concurrency=70: `pool_size_peak=50`(=max), `queue_size_peak=20`(=capacity) — 정확히 "50 running + 20
  queued = 70"으로 최대 수용량에 도달
- Concurrency=80: `accepted=70`, `rejected=10` — 정확히 예상한 "약 70" 수용량을 실측으로 확인

**H1-a 채택.** ThreadPoolExecutor의 공식 문서화된 동작(offer 성공 시 새 스레드를 만들지 않음, offer 실패
시에만 max까지 스레드 증설)이 그대로 관찰됨.

### H1-a' — SynchronousQueue는 queue wait 없이 pool이 더 빨리 증가한다 (D vs E)

같은 core=10/max=50 조건에서 D와 E를 **동일 Concurrency(10, 30)로 직접 비교**:

| Concurrency | config | pool_size_peak | queue_size_peak | TTFC p95 |
|---|---|---|---|---|
| 10 | D | 10 | 0 | 1.05s |
| 10 | E | 10 | 0 | 1.05s |
| 30 | D | **10** | **20** | **10.80s** |
| 30 | E | **30** | **0** | **1.13s** |

Concurrency=10에서는 둘 다 core 이내라 차이가 없다. **Concurrency=30에서 차이가 극명하게 드러난다** — D는
queue에 20개가 쌓여 TTFC p95가 10.8초까지 늘어나는 반면, E는 SynchronousQueue라 저장할 곳이 없으므로
즉시 pool이 30까지 늘어나 TTFC p95가 1.13초로 사실상 queue wait가 없다. E는 Concurrency=50까지도
`queue_size_peak=0`을 유지하며 TTFC p95 1.24초로 일정하다 — max(50)에 도달하기 전까지는 **한 번도
queue에서 기다리지 않는다.**

**H1-a' 채택.** SynchronousQueue는 "저장 없이 즉시 handoff"라는 정의 그대로, capacity(=max) 안에서는
queue wait가 구조적으로 0이다.

### H1-b — Large Queue + CallerRuns: capacity 초과 후 mode="caller" 증가, 실제 Tomcat 스레드에서 실행됨 (Config A)

Table 1의 A 행: Concurrency=120(core+queue=110 초과분 10)에서 `caller_runs=10`, Concurrency=150(초과분
40)에서 `caller_runs=40` — **초과분과 정확히 1:1로 일치**한다(`executor_task_execution_total{mode="caller"}`
로 측정).

**caller 실행이 실제 Tomcat request thread에서 일어났는지**는 thread name을 Prometheus label로 넣지
않고 로그로 확인했다(A, Concurrency=120 재실행 후 gateway 컨테이너 로그의 `relay finished` 라인을 실행
스레드 이름별로 집계):

```
  11 [chat-executor-10]   (풀 워커 스레드, 10개 × 11건 = 110건 — core 10 + queue 100)
  11 [chat-executor-9]
  ... (chat-executor-1~10 각 11건씩, 합 110)
   1 [nio-8080-exec-1]    (Tomcat NIO 요청 처리 스레드 — caller-run)
   1 [io-8080-exec-66]
   1 [io-8080-exec-62]
   ... (서로 다른 Tomcat 스레드 10개, 각 1건씩, 합 10)
```

정확히 110건은 `chat-executor-N`(pool worker)에서, 10건은 서로 다른 Tomcat 스레드(`nio-8080-exec-*`/
`io-8080-exec-*`)에서 실행됐다 — `TaggingCallerRunsPolicy.rejectedExecution()`이 `task.run()`을 호출자
스레드에서 **동기적으로** 실행한다는 설계가 실측으로 확인됐고, 그 호출자가 실제로 Tomcat의 요청 처리
스레드임도 확인됐다. `platform_thread_peak`도 Concurrency=150에서 151까지 올라가는데(Table 2), 이는
CallerRunsPolicy로 인해 그 순간 Tomcat이 150개 커넥션을 동시에 유지하며 그중 40개는 스트림 전체 기간
(최대 ~30-50s) 동안 blocking되어 있었기 때문 — Tomcat 자체 요청 처리 능력을 갉아먹는 방향으로 부하가
역전파된다는 가설이 그대로 확인됐다.

**H1-b 채택.**

### H1-c — Small Queue는 처리량을 높이지 않지만 overload를 빠르게 드러낸다 (B vs A)

B와 A는 Queue 크기(20 vs 100)와 RejectionPolicy(ABORT vs CALLER_RUNS) **두 축이 동시에 다르므로** 순수
"Queue 크기만의 효과"는 아니다 — 이 두 축이 결합된 실제 운영 트레이드오프로 해석한다.

- **B(Small Queue+Abort)**: `accepted`가 정확히 core+queue=30에서 하드 캡됨(Concurrency 40→accepted 30,
  rejected 10; Concurrency 60→accepted 30, rejected 30 — Table 1). **받아들여진 요청의 TTFC는
  Concurrency가 아무리 늘어도 절대 늘지 않는다** — Concurrency 30/40/60 모두 TTFC p95가 10.8~11.0초
  구간에 고정(Table 3). 초과분은 즉시(수 ms) 503으로 거절되어 클라이언트가 오래 기다리지 않는다.
- **A(Large Queue+CallerRuns)**: reject가 전혀 없다(Table 1, 5개 run 전부 `rejected=0`) — 대신 받아들인
  전체 population의 TTFC가 Concurrency와 함께 계속 늘어난다: Concurrency 30→p95 10.9s, 100→p95 45.1s,
  120→p95 50.1s, 150→p95 50.1s(60s 데드라인에 근접). **"처리량(accepted 수)"은 A가 항상 더 크지만
  (150 vs B의 최대 30), 그 대가로 성공까지 걸리는 시간이 60s 데드라인 턱밑까지 계속 나빠진다.**

**"Large Queue가 처리량을 높이는 것은 맞지만, 그 처리량 증가는 accept 시점을 늦추는 대신 지연 시간을
데드라인까지 밀어붙이는 방식으로 얻어진다 — Small Queue처럼 즉시 overload를 드러내지 않는다"**는 것이
정확한 해석이다. B가 "처리량을 낮춘다"기보다, **B는 자신의 실제 capacity(30) 이상으로는 아예 받지
않음으로써 그 capacity 안의 요청들의 지연 시간을 항상 낮게 유지**하고, A는 **capacity 이상도 받아주는
대신 그 초과분이 이미 받아들인 요청들의 지연 시간을 함께 늘린다.**

**H1-c 채택 (단, 위 confound를 명시한 해석으로).**

## 5. 새로 발견한 이슈

### 5-1. (측정 인프라) Docker Desktop VM 시계 drift — 실제로 발생, 방어 로직 추가함

Screening 초기 스모크 테스트 중, 컨테이너 재생성 직후 host 시계와 Docker Desktop VM 시계가 최대
**203초까지 벌어지는 현상을 실측**했다(원인 추정: 장시간 세션 동안 host의 sleep/idle로 인한 VM 일시정지 및
불완전한 재동기화 — macOS Docker Desktop의 알려진 특성). 이 상태에서 실행한 run은 `CHAT_TOTAL_TIMEOUT_MS`
같은 서버 측 타이머가 클라이언트가 관찰하는 실제 wall time과 수백 초 어긋나 모든 latency 지표가 무의미해짐
(`gateway_request_outcome_total`이 전부 `timeout_before_start`로만 찍히는 등 실제로 관찰됨). **모든 후속
Screening run은 오염된 결과였고 재실행했다.** `scripts/run-screening.sh`에 매 run 시작 전
host/container epoch 차이를 확인해 5초 초과 시 즉시 중단하는 가드를 추가했다 — 이후 21개 run 전부 drift
0~2초 이내로 클린하게 완료됨.

### 5-2. (측정 파이프라인) Prometheus scrape 시점 vs run 종료 시점 race — 결과 수집 스크립트 자체 버그, 수정함

컨테이너의 `gateway_async_active_requests`가 0으로 drain된 직후 바로 Prometheus를 질의하면(런이
5초 남짓으로 짧을 때 특히), Prometheus가 아직 해당 시점을 scrape(5s 간격)하지 못해 모든 값이 0/공백으로
잡히는 문제를 발견했다. `scripts/run-screening.sh`가 drain 확인 후 scrape_interval + 여유(8초)를 기다린
뒤에 수집하도록 수정 — 이후 전부 `invariant_ok=true`로 정상 수집됨. (애플리케이션 버그 아님, 이번에 만든
수집 스크립트 자체의 버그였음)

### 5-3. 애플리케이션(Gateway) 레벨에서는 새 버그를 발견하지 못함

21개 run 전부 `gateway_request_received_total == sum(gateway_request_outcome_total)` invariant 통과,
`unexpected_runtime_error=0`, `failed=0`. Unit 5 착수 전 0-3에서 발견한 "absolute deadline 이후에도
platform worker가 blocking read로 남아있는" 한계(`docs/test-results/phase1/absolute-deadline-blocking-read.md`)
는 이번 Screening 범위(모든 요청이 60s 데드라인 안에서 종료)에서는 직접 재현되지 않았다 — Config A의
높은 Concurrency(150)에서도 TTFC p99가 최대 50.1s로 60s 데드라인 밑에 머물렀기 때문. 이 한계는 여전히
"기록된 한계"로 유효하며, 정식 Benchmark에서 데드라인에 더 바짝 붙는 시나리오가 나오면 재현될 수 있다.

## 6. 정식 Benchmark(Unit 6) 추천 구성

Screening은 "최선"을 고르지 않는다 — 아래는 서로 다른 trade-off를 대표하는 2~3개 후보를 숫자 근거와 함께
제시한다.

1. **Config B (Small Queue + Abort, core=max=10, queue=20)** — **빠른 실패(fast-fail) 대표.**
   근거: capacity(30)를 넘는 요청은 즉시(수 ms) 503으로 거절되고, capacity 안에 든 요청의 TTFC p95는
   Concurrency 30~60 전 구간에서 10.8~11.0s로 안정적 — "받아들인 요청은 예측 가능한 지연을 보장하고,
   그 이상은 클라이언트가 빨리 재시도 판단을 할 수 있게 한다"는 운영 전략의 대표.

2. **Config E (core<max + SynchronousQueue, core=10/max=50)** — **queue wait 최소화 대표.**
   근거: capacity(50) 안에서는 TTFC p95가 Concurrency 10→60 전 구간에서 1.05~1.24s로 사실상 일정 —
   D와 동일 core/max인데도 queue wait가 구조적으로 없다. 대가는 `platform_thread_peak`가 Concurrency=50에서
   109까지(D의 같은 조건 대비 유사하거나 더 높음) 늘어난다는 점 — 스레드/RSS 비용이 더 크다.

3. **Config A (Large Queue + CallerRuns, core=max=10, queue=100)** — **무손실(no-reject) 대표, 참고용.**
   근거: Concurrency 150까지도 reject가 0건이라 "요청을 절대 거부하지 않는다"는 정책이 필요할 때의
   레퍼런스가 된다. 다만 TTFC p95가 Concurrency 100 이상에서 45~50s로 60s 데드라인에 바짝 붙고,
   `platform_thread_peak`가 151까지 올라가며 Tomcat 자체 스레드 풀까지 잠식한다(H1-b) — 정식 Benchmark에서
   이 configuration을 다룰 때는 "요청 손실은 없지만 지연이 데드라인까지 악화된다"는 trade-off를 함께
   보고해야 한다.

**Config D는 정식 Benchmark 후보에서 제외한다** — E가 동일 core/max 조건에서 D보다 queue wait 측면에서
항상 같거나 낫고(H1-a'), D가 갖는 유일한 장점(bounded queue로 인한 상대적으로 예측 가능한 메모리 사용)은
이번 Screening 규모에서는 E 대비 뚜렷한 이점으로 나타나지 않았다. D는 "ThreadPoolExecutor의 교과서적
동작을 보여주는 예시"로서 이 문서(H1-a)에 근거로는 남기되, 정식 Benchmark 3회×5분 실행 대상에서는 제외해
실행 시간을 아낀다.

**아직 "최선의 방식"을 결정하지 않는다** — 위 세 구성은 서로 다른 trade-off(빠른 실패 vs 낮은 queue wait
vs 무손실)의 대표일 뿐, Unit 6 정식 Benchmark(5분×3회, 통계적으로 유의미한 반복)에서 이 trade-off가
장시간·정상상태(steady-state)에서도 유지되는지 확인한다.
