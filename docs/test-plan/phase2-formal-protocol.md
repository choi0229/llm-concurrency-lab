# Phase 2 — Formal Measurement Protocol Freeze (Unit 5.5)

Status: **Protocol frozen — Formal 18 runs 미실행**. Unit 5(open-model pilot) 결과를 근거로 Formal
측정 방법·run isolation만 확정한다. 새 pilot을 추가하지 않는다.
Date: 2026-08-16 (2026-08-17 §3 개정: warm-up→measurement lifecycle을 continuous 방식으로 변경 —
근거·상세는 `docs/decisions/phase2-formal-linux-environment.md` 참고)

Primary 승인: `P-E vs VT-Limited`, load = **R3/R6/R8**(§6), matrix = **18 measured runs**(§6).

---

## 1. `jvm_threads_peak` semantics — 오염 확인 및 authoritative metric 재정의

**Pilot JVM 재사용 여부 확인**: Unit 5 pilot은 P-E/VT-Limited 각각 **하나의 gateway 컨테이너(JVM)를
7개 rate(R1/R3/R5/R6/R7/R8/R10)에 걸쳐 재사용**했다(컨테이너를 rate마다 재생성하지 않음). 따라서
pilot이 보고한 `jvm_threads_peak`(P-E: 36→54→76→88→94, VT-Limited: 32→36→52→52→52→52→52)는
**해당 JVM 프로세스가 시작된 이후 그 시점까지의 누적 process-lifetime peak**이며, 특정 rate 하나만의
peak이 아니다 — 즉 R7/R8/R10에서 관측된 94/52라는 값은 "그 rate에서 처음 도달한 peak"일 수도, 더
이른 rate(R5/R6)에서 이미 도달한 뒤 유지되고 있는 값일 수도 있어 rate별로 명확히 분리할 수 없다.
**오염 확인됨.**

**Formal authoritative metric 재정의**:

| 이름 | 정의 | 용도 |
|---|---|---|
| `platform_threads_window_peak` | **measurement window(§4) 내** `jvm_threads_current`(Prometheus gauge)의 최댓값 — `max_over_time(jvm_threads_current{job="..."}[measurement_start:measurement_end])` 형태의 PromQL 범위 쿼리로 산출 | **Primary 비교(P-E vs VT-Limited)의 authoritative 자원 지표** |
| `jvm_threads_peak` | `ThreadMXBean.getPeakThreadCount()` 기반 Prometheus gauge, JVM 시작 이후 누적 — Fresh JVM 정책(§2) 적용 시에도 warm-up 구간의 peak까지 섞여 들어갈 수 있음 | **참고용 process-lifetime diagnostic**로만 기록, Primary 비교에 쓰지 않음 |

Pilot이 보여준 방향성(P-E 스레드 수가 더 크게 증가, VT-Limited는 낮게 유지)은 **참고 관찰로만 유지**하고,
Formal 수치는 Fresh JVM(§2) + measurement-window `max_over_time()`(위 정의)로 다시 확정한다.

---

## 2. Fresh JVM per measured run

**Gateway**: 매 measured run마다 **반드시 컨테이너를 재생성**한다(`docker compose up -d
--force-recreate`) — `ThreadMXBean` peak, Heap/JIT 누적 상태, 이전 run의 executor/VT task 상태를
run 간에 완전히 분리하기 위함.

**Mock LLM + Prometheus**: Phase 1 Attempt 2(Prometheus anonymous volume 재사용으로 이전 config
값이 최대 5분간 그대로 서빙된 사고, `unit6-benchmark-plan.md` §9-1)가 이미 겪은 정확히 같은 위험이
재발하지 않도록, **Gateway와 동일하게 매 run마다 전체 스택(gateway + mock-llm + prometheus)을**
`docker compose up -d --force-recreate --renew-anon-volumes`**로 재생성한다** — Phase 1이 이미
검증한 안전한 방식을 그대로 재사용하며, "무엇을 재생성 안 해도 안전한가"를 새로 판단하지 않는다.
서비스 hostname(`gateway-java21`/`mock-llm`/`prometheus`)은 compose 서비스명으로 고정되므로
Prometheus의 scrape target 설정은 run마다 바뀌지 않는다.

**Run 시작 전 확인**(Preflight, §10과 연동):
```
gateway_async_active_requests = 0
chat_virtual_tasks_active = 0        (VT-Limited)
gateway_executor_active_threads = 0  (P-E)
mockllm_current_concurrency = 0
mockllm_waiting_requests = 0
```
하나라도 0이 아니면 그 run을 시작하지 않는다 — fresh recreate 직후이므로 정상적으로는 항상 0이어야
하며, 0이 아니라면 preflight 자체가 실패한 것으로 간주한다.

**Unit 6 착수 시 필요한 설정 변경**(Unit 5.5에서는 구현하지 않음, 계획만 기록): `monitoring/
prometheus/prometheus.yml`에 Phase 2 gateway job(`targets: ["gateway-java21:8080"]`)을 추가해야
한다 — 현재는 Phase 1의 `gateway:8080` job만 존재한다(실측 확인, 아래 §12 근거).

---

## 3. Warm-up과 Fresh JVM의 관계 (2026-08-17 개정: continuous lifecycle)

Fresh JVM을 매 run마다 쓰므로, **2분 warm-up은 JIT/connection/runtime 안정화를 위한 필수 구간으로
그대로 유지**한다(Fresh JVM이라고 warm-up을 생략하지 않는다 — 오히려 매번 콜드 스타트이므로 더
필요하다). Warm-up 부하는 해당 measured load(R3/R6/R8)와 **동일 RPS**로 건다. Warm-up 구간의 모든
지표(client/server 양쪽)는 Formal 결과에서 완전히 제외한다 — Phase 1과 동일 원칙.

**Lifecycle(개정)**: warm-up과 measurement는 **하나의 연속된 k6 프로세스**로 실행하며, 그 사이에
drain(in-flight request를 0으로 비우는 것)을 넣지 않는다 — `load-test-k6/scenarios/
03-constant-arrival-rate-continuous.js`, `scripts/run-phase2-formal-benchmark.sh`.

이전 설계(별도 warm-up k6 프로세스 실행 → 완전 drain → 새 measurement-only k6 프로세스 시작)는
measurement window가 **비어있는 상태**(in-flight request = 0)에서 시작됐다. Mock workload의 전체
stream 길이가 약 7.9초이므로, window 시작 후 약 7.9초 동안은 정상적인 환경에서도 completion이 발생할
수 없어 `measurement_window_completion_rate`가 target arrival rate보다 **구조적으로** 낮게 측정됐다
(환경 stall과 무관한 measurement artifact — `docs/decisions/phase2-formal-linux-environment.md`
item 2에서 발견·확인). Continuous 설계는 warm-up에서 넘어온 in-flight population이 measurement
window 시작 시점부터 이미 steady state를 이루도록 해 이 편향을 근본적으로 제거한다.

Warm-up 구간(iteration 시작 시각이 measurement 경계 이전인 요청)은 여전히 실제 트래픽을 만들어
Gateway JVM과 k6 자체의 VU/connection pool을 모두 데우지만, 어떤 Formal client-side metric에도
기록되지 않는다 — `03-constant-arrival-rate-continuous.js`의 `setup()`이 잡은 경계(같은 Docker VM
clock 도메인의 `Date.now()` 기준)로 매 iteration을 분류한다. k6 내장 `iterations` 메트릭은 두 구간을
구분하지 않으므로, Formal cohort 크기의 출처는 이 파일이 measurement 구간에서만 증가시키는 커스텀
Counter `measurement_iterations_started_total`로 바뀐다(§11 metadata에 반영).

---

## 4. Measurement Window semantics — Throughput population vs Outcome cohort

7.8초급 workload에서 measurement 종료 직전에 시작된 요청은 window를 넘어 drain 구간에서 끝날 수
있다. 두 population을 명확히 분리한다:

**A. Throughput population** — window 안에서 **실제 완료**된 request 수:
```
completion_throughput = (measurement window 안에서 completed로 종료된 request 수) / measurement_duration
```
window 밖(warm-up 또는 drain)에서 완료된 request는 포함하지 않는다.

**B. Outcome cohort** — window 안에서 **시작**된 request:
```
cohort = measurement_start ≤ 요청 시작 시각 < measurement_end 인 모든 request
```
이 cohort는 measurement 종료 후 **drain까지 계속 추적**해서 각자의 terminal outcome(completed/
rejected/timeout/failed)이 확정될 때까지 기다린 뒤 집계한다 — outcome 집계는 drain 완료 후에만
최종 확정된다.

**혼동하지 않을 것**:
- `arrival rate`(k6가 시도한 rate) ≠ `completion throughput`(A)
- `measurement-started count`(B의 cohort 크기) ≠ `measurement-window completion count`(A의 분자)

**B의 operational source(2026-08-18 개정)**: cohort 정의(위 수식) 자체는 그대로지만, 이를 실제로
**누가 측정하는지**가 바뀌었다 — Prometheus aggregate counter가 아니라 **k6가 유일한 authoritative
source**다(`docs/decisions/phase2-formal-linux-environment.md` §14). Continuous warm-up→measurement
lifecycle(§3)에서는 warm-up에 도착한 요청이 measurement_start 이후에 완료될 수 있어, Prometheus의
outcome counter를 `[measurement_start, drain_end]`로 windowed 집계하면 이런 요청까지 섞여 들어간다 —
Prometheus counter만으로는 "이 완료가 어느 phase에서 시작된 요청의 것인지" 구분할 수 없기 때문이다.
k6는 이 문제가 없다 — `03-constant-arrival-rate-continuous.js`가 iteration 시작 시각으로 직접
phase를 분류해 measurement-phase 요청만 `measurement_iterations_started_total`/`client_completed_total`
등에 기록하므로, `collect_phase2_formal_result.py`는 이 값들을 그대로 cohort로 채택하고 Prometheus
쪽 windowed 값은 diagnostic(`server_diagnostic`, 이상 징후 확인용)으로만 쓴다 — 두 값이 정확히
일치해야 한다고 강제하지 않는다.

---

## 5. Drain

Measurement 종료 시점에 k6의 신규 iteration 유입을 중단한다(constant-arrival-rate의 `duration`
경계). §4-B cohort에 속한, 아직 진행 중인 요청은 각자 completed/timeout/failed 중 하나의 terminal
outcome에 도달할 때까지 drain한다.

**Drain timeout**: `CHAT_TOTAL_TIMEOUT_MS`(기본 60000ms) + 안전 margin(예: +30s, 여유 있게) —
Phase 1의 `run-formal-benchmark.sh`가 쓰던 방식과 동일한 여유폭을 채택한다.

**Drain 완료 확인**(Preflight 재확인과 동일 항목):
```
gateway_async_active_requests = 0
chat_virtual_tasks_active = 0        (VT-Limited)
gateway_executor_active_threads = 0  (P-E)
mockllm_current_concurrency = 0
mockllm_waiting_requests = 0
```

---

## 6. Formal Primary Matrix — 최종 승인표

| 항목 | 값 |
|---|---|
| Primary configs | P-E vs VT-Limited (2) |
| Load(RPS, 중립 식별자) | **R3 / R6 / R8** (3) |
| 반복 | 3회 |
| **총 measured run** | **2 × 3 × 3 = 18** |
| Warm-up | 2분(해당 load와 동일 RPS) |
| Measurement | 5분 |
| Drain | §5, timeout = `CHAT_TOTAL_TIMEOUT_MS` + margin |

**Load 상태 설명(표 밖 본문 서술용, 공식 표 컬럼명으로 쓰지 않음)**:
- **R3** — Stable: pilot에서 두 config 모두 reject/timeout 0.
- **R6** — Near-capacity: pilot에서 reject 0(아직 R7 이전)이나 capacity(rough estimate
  50/7.8≈6.41rps) 근접, P-E/VT-Limited 자원 사용 차이(pilot 관찰)가 가장 뚜렷했던 구간.
- **R8** — Over-capacity: pilot에서 reject 명확(28/160≈17.5%).

공식 결과표의 load axis는 `R3`/`R6`/`R8`만 쓰고, Stable/Near-capacity/Over-capacity라는 이름은
본문 서술에만 쓴다(Phase 1의 R1/R2/R5/R12 중립화 원칙과 동일).

---

## 7. Formal Authoritative Metrics

**Client** (k6): `client_ttfc_completed_seconds` p50/p95/p99, `client_stream_duration_seconds`,
`dropped_iterations`(whole-run 집계 — continuous 설계에서 warm-up/measurement phase별로 분리되지
않음, §3. PRE_ALLOCATED_VUS가 충분하면 두 phase 모두 0이어야 한다는 전제로 사용).

**Throughput**: target arrival rate, actual started rate(k6 `measurement_iterations_started_total`
/duration — §3 continuous 설계 이후 내장 `iterations` 대신 이 커스텀 Counter를 쓴다),
measurement-window completion/sec(§4-A). `actual_started_rate` vs `gateway_received_rate`의 차이는
sanity/report metric으로 기록하되, 근거 없는 percentage threshold로 PASS/FAIL을 결정하지 않는다
(`docs/decisions/phase2-formal-linux-environment.md` §8-1).

**Outcome cohort**(§4-B, drain 완료 후 확정, **k6가 유일한 authoritative source — 2026-08-18 개정**):
completed / rejected / failed_mid_stream / failed_no_event(k6 client_cohort), invariant
`measurement_iterations_started_total = completed + rejected + failed_mid_stream + failed_no_event`를
매 run 확인한다. Prometheus의 더 세분화된 server-side 분류(timeout_before_start/deadline_exceeded/
upstream_timeout/upstream_error/client_disconnect/unexpected_error)는 `[measurement_start,
drain_end]` windowed raw count로 계속 기록하되 **diagnostic 전용**이다 — warm-up spillover로 인해
k6 cohort와 정확히 일치하지 않을 수 있고, 이를 일치시키려는 보정도 하지 않는다(근거:
`docs/decisions/phase2-formal-linux-environment.md` §14).

**Resource**(공통): `platform_threads_window_peak`(§1), CPU avg/peak, Heap peak, RSS peak, GC.

**P-E 전용**: executor active peak, pool size peak, `executor_task_start_delay_seconds`.

**VT-Limited 전용**: virtual task active peak, virtual started/finished total, admission rejected
total.

**Mock**: `mockllm_current_concurrency` peak, `mockllm_waiting_requests` peak.

---

## 8. RSS / Heap — baseline + peak + delta

절대 peak만 보지 않는다. 각 run에 대해:

```
rss_measurement_start   (measurement 시작 시점 RSS)
rss_peak                (measurement window 내 최댓값)
rss_delta = rss_peak - rss_measurement_start

heap_measurement_start
heap_peak
```

Fresh JVM + 동일 warm-up 조건(§2, §3)이므로 이 값들은 run 간 비교 가능하다 — warm-up이 이미 JIT/
connection 상태를 안정화시킨 뒤의 baseline이라는 전제.

---

## 9. R5 Anomaly — 최종 표현 (수정)

~~"코드 결함이 아님을 확인"~~ (폐기, 과잉확정)

**정정된 표현**: "동일 조건 재실행에서는 재현되지 않았으며, 약 63초의 전역 로그 공백과 기존 Docker
Desktop stall 이력(Phase 1 Experiment Integrity)을 고려하면 환경 stall 가능성이 높다. 현재 코드
결함을 뒷받침하는 재현 증거는 없다."

**Formal에서 동일 현상 재관측 시 처리 방침**: 자동으로 "환경 stall"로 분류하지 않는다. 매번 다음을
확인한 뒤 판단한다 — (1) host/container clock drift, (2) Prometheus scrape gap(해당 구간
`up{job=...}` 값), (3) Gateway 컨테이너 로그(공백/burst 패턴), (4) Mock LLM 로그, (5) Docker 리소스
상태(`docker stats`, 동시 실행 중인 무관한 워크로드 여부). 이 확인 없이 "stall이었다"고 결론 내리지
않는다 — 이 5가지가 R5 anomaly 때 실제로 근거가 됐던 항목이며, Formal에서도 동일한 조사 없이는
동일한 결론을 재사용하지 않는다.

---

## 10. Formal Preflight Checklist

각 measured run 시작 **직전** 전부 확인, 하나라도 실패하면 그 run을 시작하지 않는다:

```
[ ] 무관한 Docker workload 없음 (llm-concurrency-lab-* 외 컨테이너 확인)
[ ] macOS sleep 방지 상태(예: caffeinate 또는 동등 조치)
[ ] host/container(Docker VM) clock drift ≤ 5초
[ ] Gateway 이미지가 올바른 JDK exact digest 사용 중
    (eclipse-temurin:21.0.11_10-{jdk,jre}-jammy@sha256:...)
[ ] 컨테이너 arch = linux/arm64 (에뮬레이션 없음)
[ ] Docker CPU/memory 제한이 설정값과 일치(gateway cpus:1.0, mock cpus:2.0)
[ ] k6 버전 = k6 v1.8.0 + xk6-sse v0.1.11 (k6-sse:dev 이미지)
[ ] Prometheus가 fresh 상태로 재생성됨(anonymous volume renewed) — 이전 run 데이터 잔존 없음
[ ] Gateway가 fresh JVM(이번 run을 위해 새로 생성된 컨테이너)
[ ] 모든 active gauge = 0 (§2 목록)
[ ] 직전 run의 drain이 완료된 상태 (§5)
```

---

## 11. Run Metadata

각 formal run 디렉터리(§ result directory 구조, Phase 1과 동일한 `docs/test-results/phase2/unit6/
<config>-<load>-run<N>/` 패턴 예정)에 다음 두 파일을 남긴다.

**`timestamps.json`**(2026-08-17 개정 — §3 continuous lifecycle 반영, `warmup_end`는 더 이상 별도
관측되지 않음: warm-up이 독립 프로세스가 아니므로):
```
run_start          (k6 프로세스 시작, vm_epoch 실측)
measurement_start   (= run_start + WARMUP_SEC, nominal)
measurement_end     (= measurement_start + MEASUREMENT_SEC, nominal)
drain_end           (k6 프로세스 종료 + drain 확인, 실측)
```

**`environment.json`**:
```
config              (P-E | VT-Limited)
load_rps            (3 | 6 | 8)
repeat              (1 | 2 | 3)
jdk_version          21.0.11+10-LTS
jdk_image_digest      eclipse-temurin:21.0.11_10-{jdk,jre}-jammy@sha256:...
architecture          arm64 (실측, uname -m)
cpu_limit / mem_limit  (docker-compose 값)
thread_mode           (PLATFORM | VIRTUAL_LIMITED)
k6_version            k6 v1.8.0 + xk6-sse v0.1.11
```

이 두 파일 모두 Phase 1의 `unit6-benchmark-plan.md`/`unit6-formal-benchmark-results.md`가 이미
확립한 형식을 그대로 계승한다(새 스키마를 발명하지 않는다).

---

## 12. 근거 — 실제 확인한 것

- Pilot이 P-E/VT-Limited 각각 단일 컨테이너를 7개 rate에 재사용했다는 사실: Unit 5 실행 로그/스크립트
  (`run_pilot.sh`가 기존 컨테이너 이름 `gw-pe`/`gw-vtl`에 계속 요청만 보냈고, 컨테이너를 rate마다
  재생성하지 않았음)로 확인.
- `monitoring/prometheus/prometheus.yml`에 Phase 2 gateway job이 아직 없음: 파일 직접 확인(현재
  `gateway-mvc-executor-java8`/`mock-llm-fastapi` 두 job만 존재).

---

**Unit 6 Formal Benchmark(18 runs) 실행 준비 완료. 아직 실행하지 않았다.**
