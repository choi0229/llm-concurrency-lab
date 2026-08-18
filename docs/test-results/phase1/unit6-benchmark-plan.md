# Unit 6 — Formal Benchmark Plan

Status: 계획 확정, 실행 완료 (아래 §3의 RPS 선정 근거는 유효하나 **service time 값(4.8s)은 이후
`unit6-formal-benchmark-results.md`에서 7.8s로 정정됐다** — 이 문서의 §3 계산은 역사적 기록으로만
남기고, load label(LOW/STRESS/OVERLOAD)도 그 문서에서 중립적 R1/R2/R5/R12로 대체됐다. **공식 결과와
canonical 해석은 항상 `unit6-formal-benchmark-results.md`를 따른다.**)
Date: 2026-08-11

## 0. Unit 5 해석 수정 (승인됨)

Unit 5 최종 보고에서 "D는 E보다 항상 같거나 열등하다"고 결론 내렸던 것을 철회한다. 실제로는 trade-off다:

| | D (Bounded Queue, core<max) | E (SynchronousQueue, core<max) |
|---|---|---|
| 전략 | Queueing으로 thread 증가를 억제 | Queue 생략, pool을 빠르게 max로 확장 |
| Queue Wait / TTFC | 더 나쁨 (Unit 5: concurrency=30에서 TTFC p95 10.8s) | 더 좋음 (동일 조건 TTFC p95 1.13s) |
| Platform Thread / native resource | 상대적으로 억제 | 더 많이 씀 (concurrency=50에서 109 vs D 유사조건 82) |

D는 열등한 구현이 아니라 "queueing으로 thread 비용을 억제하는 전략"의 대표다. H1-a 검증 목적은 Unit 5
Screening으로 충분히 달성했으므로, Unit 6 정식 반복 Benchmark 범위는 **B(fast-fail) / E(queue 최소화) /
A(queue 허용+CallerRuns)** 세 configuration으로 좁히고, D는 Screening 결과와 이 trade-off 분석에만
남긴다 (재실행하지 않음).

## 1. 대상 Configuration (Unit 5와 동일한 실제 값)

| config | 전략 | CHAT_CORE_POOL_SIZE | CHAT_MAX_POOL_SIZE | CHAT_QUEUE_CAPACITY | CHAT_REJECTION_POLICY |
|---|---|---|---|---|---|
| B | Fast-fail | 10 | 10 | 20 | ABORT |
| E | Queue 최소화/Thread 확장 | 10 | 50 | 0 (SynchronousQueue) | ABORT |
| A | Queue 허용+CallerRuns 역전파 | 10 | 10 | 100 | CALLER_RUNS |

## 2. 부하 모델: constant-arrival-rate

Unit 5는 closed-model(고정 VUS, 단발 wave)이었다 — Screening 목적(포화 양상 관찰)에는 적합하지만, 장시간
반복 비교에는 부적합하다: reject가 빠른 configuration(B)은 VU가 iteration을 더 빨리 반복해 스스로 더 많은
요청을 만들어내므로, 같은 VUS로도 configuration마다 실제 arrival rate가 달라진다. 이는 "동일 조건
비교"라는 공정성 원칙(design 문서 섹션 9)에 위배된다.

**Unit 6은 `load-test-k6/scenarios/02-constant-arrival-rate.js`(open-model, `constant-arrival-rate`
executor)를 사용한다** — 모든 configuration에 정확히 동일한 요청 유입률(RPS)을 적용해, "그 유입률에서 이
configuration이 얼마나 잘 버티는가"를 순수하게 비교한다.

## 3. LOW / STRESS / OVERLOAD 부하 선정 근거

Mock LLM workload는 Unit 4/5와 동일하게 고정한다: `FIRST_CHUNK_DELAY_MS=1000`, `CHUNK_INTERVAL_MS=200`,
`CHUNK_COUNT=20` → 성공 요청 1건의 서비스 시간(stream 전체 완료까지) **W ≈ 1000 + 19×200 = 4800ms ≈
4.8s**로 Unit 4/5와 동일하게 고정된다.

Unit 5 Screening(closed-model, concurrency=동시 접속 수)에서 관찰된 각 configuration의 실측 capacity
경계를, **Little's Law (L = λW)** 로 open-model 유입률(λ, req/s)로 환산해 세 구간의 근거로 삼는다 —
임의로 값을 정하지 않고, 이미 실측된 concurrency 경계를 그대로 변환한다.

| 근거(Unit 5 실측) | concurrency(L) | λ = L / 4.8 |
|---|---|---|
| B: core+queue=30에서 정확히 reject 시작(Table 1, `B-vus30`→accepted 30/rejected 0, `B-vus40`→accepted 30/rejected 10) | 30 | 6.25 req/s |
| E: max=50에서 정확히 reject 시작(`E-vus50`→accepted 50/rejected 0, `E-vus60`→accepted 50/rejected 10) | 50 | 10.42 req/s |
| A: core+queue=110에서 CallerRuns 시작(`A-vus120`→caller_runs=10) | 110 | 22.92 req/s |
| 모든 config의 core=10(queue/caller_runs 전혀 안 씀) | 10 | 2.08 req/s |

이로부터:

- **LOW = 2 req/s** — `L=λW≈9.6`, 모든 config의 core(10) 이하 → 세 configuration 전부 queue/caller_runs를
  전혀 쓰지 않고 안정적으로 처리할 것으로 예상되는 구간(§1 요구사항의 "모든 configuration이 안정적으로
  처리 가능한 부하").
- **STRESS = 5 req/s** — `L≈24`, B의 capacity(30) 안쪽이지만 core(10)는 넘어 **B/D 계열은 실제로 queue를
  쓰기 시작**하는 구간(Unit 5 `B-vus20`~`B-vus30`과 동일한 band, 그 구간에서 queue wait p95 4.9~9.5s 실측),
  E/A는 각자의 capacity(50/110)에 비해 여유가 있어 영향이 적을 것으로 예상 — 세 configuration이
  **서로 다르게 반응하기 시작하는** 지점(§1 "일부 configuration에서 queue/latency 증가가 시작되는 부하").
- **OVERLOAD = 12 req/s** — `L≈57.6`, B(30)와 E(50) 두 capacity 경계를 모두 넘지만 A(110)는 아직 안
  넘음 → B와 E 모두 자신의 overload policy(각각 reject, thread 확장 후 reject)가 나타날 것으로 예상되는
  반면, A는 Unit 5에서 예측된 대로 reject 없이 queue+CallerRuns로 흡수할 것으로 예상 — 이 **차이 자체가
  검증 대상**이다(§1 "timeout/reject/CallerRuns 등의 overload policy가 나타나는 부하").

## 4. Run matrix (반복 횟수를 줄인 이유 포함)

| load | 반복 | 이유 |
|---|---|---|
| LOW | config당 1회(sanity) | 모든 config가 core capacity 이내로 안정적일 것이 Unit 5에서 이미 명확히 예측되는 구간 — 반복해도 새로운 variance를 볼 가능성이 낮고, 3회 반복의 한계효용이 낮다고 판단해 시간을 아낀다(전체 실행 시간을 3configs×3loads×3reps=27회보다 줄이라는 지시에 따름). |
| STRESS | config당 3회 | 세 configuration이 실제로 다르게 반응하기 시작하는 핵심 비교 구간 — 반복 간 variance(median/min/max)를 반드시 봐야 하는 구간이므로 3회 유지. |
| OVERLOAD | config당 3회 | overload policy(reject/timeout/caller_runs)의 재현성이 Unit 6의 핵심 검증 대상이므로 3회 유지. |

**Total = 3 configs × (1 + 3 + 3) = 21 measured runs** (27회 대비 6회 절감).

## 5. Warm-up / Measurement / Drain

각 measured run:

```
[Preflight] -> [Warm-up 2min] -> [Measurement 5min] -> [Drain] -> [Cooldown]
```

- Warm-up 2분: 동일 arrival rate로 미리 트래픽을 흘려 JVM JIT/커넥션 풀을 데운다. **Warm-up 구간의 k6/Prometheus
  데이터는 정식 결과에서 제외**한다 — k6 스크립트 자체를 warm-up/measurement 두 단계로 나눠 실행하고,
  Prometheus 질의도 `measurement_start`~`measurement_end` 구간만 사용한다.
- Measurement 5분: 이 구간만 정식 결과로 채택.
- Drain: `gateway_async_active_requests==0`이 될 때까지 대기(최대 warm-up 단계에서 설정한
  `CHAT_TOTAL_TIMEOUT_MS=60s` + margin).
- 각 run의 `start_timestamp`, `measurement_start`, `measurement_end`, `drain_end`를
  `timestamps.json`에 정확히 기록한다(§13).

## 6. 공정성 조건 (Unit 5와 동일하게 고정, Executor만 변경)

Mock LLM workload, Mock concurrency capacity(무제한, `MAX_CONCURRENT_PROCESSING=0`), Gateway
`cpus:1.0`/`mem_limit:1g`, Mock `cpus:2.0`/`mem_limit:1g`, timeout(`CHAT_CONNECT_TIMEOUT_MS=3000`/
`CHAT_READ_TIMEOUT_MS=30000`/`CHAT_TOTAL_TIMEOUT_MS=60000`), k6 이미지(`k6-sse:dev`, k6 v1.8.0 +
xk6-sse v0.1.11), arrival rate(각 load 단계별 동일), test duration(warm-up 2min+measurement 5min 동일),
Prometheus scrape interval(5s), logging level(INFO), JDK(Eclipse Temurin 8), Spring(Boot
1.5.22.RELEASE), container architecture(linux/arm64, 에뮬레이션 없음) — 모두 Unit 4/5와 동일하게
고정하고 Executor 설정(core/max/queue/rejection)만 변경한다.

## 7. Preflight (매 run 시작 전 필수)

1. host/container architecture 일치 확인 (`uname -m` 양쪽 arm64)
2. host/container clock drift ≤ 5초 확인 (Unit 5에서 최대 203초 drift 실측 — 필수 가드)
3. Gateway `/healthz` 200
4. Mock LLM `/healthz` 200
5. Prometheus가 Gateway/Mock 둘 다 scrape 성공 중인지 확인 (`up{job=...}==1`)
6. `gateway_async_active_requests == 0`
7. `gateway_executor_queue_size == 0`
8. 직전 run의 drain이 완료된 상태인지(컨테이너를 매 run마다 재생성하므로 자동 충족)
9. **llm-concurrency-lab 이외의 unrelated container 여부 확인** — 있으면 목록을 사용자에게 보여주고
   진행 전 확인받는다(임의 종료 금지). 이번 계획 승인 이전에 이미 1건 발견해 보고함(§8).

drift가 threshold(5초)를 넘으면 그 run을 시작하지 않고 재시도한다. 이 조건들은 각 run의
`environment.json`에 grid로 기록한다.

## 8. 발견된 unrelated container (2026-08-11 기준)

```
NAMES        IMAGE                                 STATUS         CPU%    MEM
prometheus   prom/prometheus:latest                Up 4 hours     0.20%   124MiB
minikube     gcr.io/k8s-minikube/kicbase:v0.0.50   Exited(정지됨)  -       -
```

`prometheus`(우리 프로젝트의 `llm-concurrency-lab-prometheus-1`과 다른 컨테이너, 3개월 전부터 실행 중이던
무관한 프로젝트)가 현재도 실행 중이다. 현재 CPU/Mem 사용량 자체는 낮지만(0.2%/124MiB), 정식 Benchmark의
공정성 원칙(§6)을 지키려면 이 container가 떠 있는 상태로 측정한 run은 채택하지 않는 것이 원칙에 맞다.
`minikube`는 이미 정지 상태라 문제 없다.

**사용자 확인 후 처리 완료** — `docker stop prometheus`로 중지함(승인받음).

## 9. 1차 실행 중 발견한 근본 문제와 전체 재실행 결정

1차 21-run 실행(2026-08-11 15:04Z~18:04Z) 완료 후 invariant 검증에서 3개 run
(`A-LOW-run1`, `A-OVERLOAD-run1`, `E-LOW-run1`)이 위반됐다. 개별 재실행 시도 중, 문제가 단순
transient query flake가 아니라 **host 시계와 Docker VM 시계 간 drift가 preflight 시점 이후에도
발생할 수 있다는 것**(Unit 5에서 이미 경험한 문제의 재발, 단 이번엔 run 도중에 발생)임을 확인했다 —
`measurement_start`/`measurement_end`를 host `date +%s`로 캡처했는데, Prometheus는 Docker VM
안에서 돌고 있어 그 시계를 따른다. Preflight에서 drift=0을 확인해도 그 이후 300초+ 동안 VM이 다시
drift를 일으키면, host 시계 기준 타임스탬프로 Prometheus를 질의했을 때 실제 의도한 시점과 다른
시점의 데이터를 읽게 된다 — 관찰된 증상(예: 한쪽 경계에서 매우 큰 값이 다른 쪽 경계에서 0으로
떨어지는 등)이 이 가설과 정확히 일치했다.

**조치**: `scripts/run-formal-benchmark.sh`의 모든 측정 타임스탬프(`START_TS`, `WARMUP_START`,
`WARMUP_END`, `MEASUREMENT_START`, `MEASUREMENT_END`)를 host 시계가 아닌 **Docker VM 자체의
시계**(`docker run --rm alpine date +%s`)로 캡처하도록 변경 — Prometheus를 쿼리하는 타임스탬프가
항상 Prometheus 자신과 같은 시계 도메인에 있도록 구조적으로 보장한다. Run 종료 시점에 host/VM
drift를 재확인해 `environment.json`에 `post_run_clock_drift_s`로 기록한다.

이 수정 전 1차 21-run 결과는 **invariant를 통과한 18개 run도 미세한 drift로 측정 구간이 약간
어긋났을 가능성을 배제할 수 없어** 전부 신뢰할 수 없는 것으로 간주하고
`docs/test-results/phase1/unit6-attempt1-discarded/`로 보존한 뒤, 수정된 스크립트로 **21-run
전체를 처음부터 재실행**했다(2차).

### 9-1. 2차 실행에서도 재현된 문제 — 진짜 원인은 clock drift가 아니라 Docker 익명 volume 재사용

2차 실행에서도 동일하게 `A-LOW-run1`, `E-LOW-run1` **딱 두 run만** invariant 위반. 두 run 모두
"해당 config로 전환한 뒤 첫 번째 run"이라는 공통점이 있었고, post-run clock drift도 0~2초로 전혀
문제 없었다 — 즉 9번 항목에서 고친 clock drift는 진짜 원인이 아니었다(그래도 구조적으로 더
정확해졌으므로 되돌리지 않는다).

수동으로 직접 재현했다: config B로 실제 reject 620건을 만든 뒤 config E로
`docker compose up -d --force-recreate mock-llm gateway prometheus`로 전환하고, **fresh gateway
자신의 `/metrics`는 즉시 비어있음(정상)을 확인했지만, Prometheus는 45초가 지나도 계속
`gateway_request_outcome_total{outcome="rejected"} == 620`을 그대로 반환**했다. 원인:
**`docker compose up --force-recreate`는 기본적으로 이전 컨테이너의 익명 볼륨(anonymous
volume)을 새 컨테이너에 그대로 재연결한다** — `prom/prometheus` 이미지가 `/prometheus`(TSDB
데이터 디렉터리)를 익명 볼륨으로 선언하므로, "force-recreate"해도 Prometheus의 과거 데이터가
그대로 남는다. 새 config의 gateway가 "rejected" label을 한 번도 다시 증가시키지 않으면(예:
E-LOW처럼 진짜로 reject가 없는 경우) Prometheus 입장에서는 그 시계열에 새 샘플이 안 들어오는
것과 동일해, 마지막 값(이전 config의 620)을 기본 staleness 정책(최대 5분)에 따라 계속 서빙한다 —
`increase()`/`rate()`가 아닌 raw instant query라 counter reset 보정도 없다.

**조치**: `docker compose up -d --force-recreate --renew-anon-volumes`(`-V`)로 변경 — 익명 볼륨을
새로 생성해 Prometheus TSDB를 매 run마다 실제로 완전히 비운다. 수정 후 동일 재현 시나리오에서
전환 직후 즉시 빈 결과가 반환됨을 확인했다.

2차 실행 결과도 이 문제로 오염 가능성을 배제할 수 없어(같은 메커니즘이 invariant를 통과한 다른
run에서도 더 미묘하게 발생했을 수 있음) `docs/test-results/phase1/unit6-attempt2-discarded/`로
보존하고, `-V` 적용 후 **21-run 전체를 3차로 재실행**했다. 아래 결과는 이 3차(최종) 실행 결과다.
