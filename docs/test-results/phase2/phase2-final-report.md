# Phase 2 Final Report — Platform Thread vs Virtual Thread (`gateway-mvc-java21`)

Status: **Phase 2 COMPLETED (Frozen)**. Primary Formal(P-E vs VT-Limited, R3/R6/R8 × 3회, 18/18 valid,
§9-§22) + JFR Pinning Diagnostic(B-0/B-1/B-2, §8) + VT-Unlimited Secondary Screening(A-1/A-2, §7)까지
계획된 Phase 2 범위를 모두 완료했다. Primary Formal의 18-run canonical dataset은 이 최종 갱신에서도
전혀 수정하지 않았다 — Secondary 결과(§7, §8)는 별도 raw 소스(`docs/test-results/phase2/secondary/`)를
근거로 이번에 추가된 섹션이다. 이 보고서 갱신 이후 Phase 2 범위의 추가 Benchmark/JFR diagnostic/
VT-Unlimited run은 수행하지 않는다.
Date: 2026-08-20

---

## 1. Executive Summary

Phase 1은 "Servlet AsyncContext + 전용 `ThreadPoolExecutor` + `HttpURLConnection`(blocking) + SSE
relay" 구조에서, blocking I/O를 유지하는 한 overload 비용이 사라지지 않고 Queue Wait/Reject/Platform
Thread 증가/Caller Thread 전파 중 다른 형태로 이동한다는 것을 확인했다. Phase 2는 같은 blocking
programming model과 blocking HTTP client(`HttpURLConnection`)를 그대로 유지한 채, **Chat Executor
하나만** Platform `ThreadPoolExecutor`(P-E)에서 Java 21 Virtual Thread(VT-Limited,
`Executors.newVirtualThreadPerTaskExecutor()` + `Semaphore(50)` admission gate)로 바꿨을 때 무엇이
달라지는지를 동일 admission concurrency ceiling(=50) 조건에서 측정했다.

**Primary 핵심 결론**: 동일 admission ceiling에서 P-E와 VT-Limited의 completion capacity·rejection
curve·TTFC는 반복 측정의 변동 범위에서 실질적인 차이를 확인하기 어려웠다. 그러나 **JVM platform thread
사용량은 크게 갈렸다** — measurement window 내 JVM platform thread peak가 R3에서 47(P-E) vs
33(VT-Limited), R6에서 71 vs 35, R8에서 73 vs 35로, VT-Limited는 부하가 늘어도 33~35 수준에서 거의
평평하게 유지된 반면 P-E는 부하에 비례해 계속 증가했다. **다만 이 platform thread 절감이 native RSS
절감으로 일관되게 이어지지는 않았다**(R3/R6에서는 오히려 VT-Limited RSS가 더 높았고 R8에서만 소폭
낮았다) — CPU avg도 VT-Limited가 세 부하 전부에서 P-E보다 18~27% 더 높았다(절대값은 양쪽 모두 0.06
core 미만). 즉 Primary가 보여준 Virtual Thread의 핵심 효과는 "downstream capacity를 늘린 것"이 아니라
**"동일 blocking-I/O concurrency를 유지하는 데 필요한 JVM platform thread 수를 부하와 분리(decouple)한
것"**이며, 그 절감이 자동으로 메모리·CPU 절감으로 이어진다고 일반화할 수 없다.

**Secondary 핵심 결론**: Primary 완료 이후 두 갈래 Secondary 실험을 추가로 수행했다. JFR
`jdk.VirtualThreadPinned` diagnostic(B-0 positive control → B-1 정상 SSE → B-2 ~30초 blocking read,
§8)은 detector pipeline 자체가 정상 동작함을 확인한 뒤(B-0, intentional pinning 5/5 검출), 실제
Gateway의 정상 SSE와 장시간 blocking read 두 workload 모두에서 observable pinning을 관측하지 못했다
(**NOT OBSERVED UNDER TESTED CONDITIONS**). VT-Unlimited Secondary Screening(§7)은, admission 제한을
제거하면 Gateway 자신은 500 concurrent SSE stream까지 clean하게 유지할 수 있지만(A-1), downstream(Mock)
capacity 자체는 늘어나지 않는다는 것을 보였다(A-2) — admission ceiling을 없애면 초과 요청이 Gateway
에서 빠르게 거절되는 대신 downstream waiting queue로 옮겨가 TTFC/전체 duration이 크게 늘어났다(최대
16.7s/23.6s). 즉 **Virtual Thread는 overload control을 대체하지 않는다** — downstream capacity 기반
admission control(Semaphore 등) + timeout + fast-fail + monitoring은 Virtual Thread 사용 여부와 무관
하게 여전히 필요하다(§25).

이 결과는 측정 환경 자체의 신뢰성 확보 과정과 분리해서 읽을 수 없다 — Mac + Docker Desktop 환경에서
독립적인 두 신호(로그 gap + Prometheus counter plateau)로 확인된 반복적인 environment-level stall
때문에 그 환경이 Formal Benchmark 환경에서 공식 탈락됐고(§11), Docker를 완전히 제거한 native macOS
ARM64 harness로 이전한 뒤에야 18/18 valid run을 확보했다(§12).

---

## 2. Phase 1에서 이어진 문제

Phase 1(Java 8, `gateway-mvc-executor-java8`)은 Bounded-queue/Abort(B), SynchronousQueue/
Thread-expansion(E), Large-queue/CallerRuns(A) 세 Executor 전략을 24회 formal run으로 비교해 다음을
확인했다: 세 전략 모두 자기 capacity(B≈1.28rps, E≈6.41rps) 안에서는 동일하게 동작하지만, capacity를
넘으면 서로 다른 자원을 대가로 치른다 — B는 accept를 포기하고, E는 platform thread를 계속 늘리며
(A-R12에서 `platform_thread_peak=124`, RSS peak 310.9MB로 세 config 중 최대), A는 reject를 피하는
대신 overload를 Tomcat caller thread로 역전파한다.

E 전략(SynchronousQueue, 사실상 무제한 thread 증식)이 보여준 "concurrency가 늘면 platform thread와
RSS가 함께 늘어난다"는 관찰이 Phase 2의 출발점이다: **Virtual Thread가 이 platform-thread-concurrency
결합을 실제로 끊어낼 수 있는가?**

---

## 3. Research Questions

Phase 2 설계 문서(`docs/test-plan/phase2-design.md` §0)가 정의한 핵심 질문:

> "Blocking Programming Model과 Blocking HTTP I/O를 유지하면서 Platform Thread 대신 Virtual Thread를
> 쓰면, 동일한 I/O concurrency를 훨씬 적은 platform-thread 비용으로 유지할 수 있는가?"

이 질문을 §23(가설 판정)의 H2-a~H2-f(Primary)와 S1-S3(Secondary)로 세분화해 실측으로 답한다.

---

## 4. 비교 구조

Java 8(Phase 1)과 Java 21(Phase 2)을 직접 비교하지 않는다 — JDK 세대 차이가 결과에 섞이기 때문이다.
같은 Java 21 환경에서 **Chat Executor 하나만** 바꾼다:

```
Front → Spring MVC → AsyncContext → Chat Executor → HttpURLConnection → Mock LLM → SSE relay
                                     ^^^^^^^^^^^^^
                                     여기만 바뀐다
```

Tomcat 자신의 요청 처리 스레드 풀은 두 구성 모두 기본값(platform thread)으로 고정한다 — Spring Boot의
전역 `spring.threads.virtual.enabled=true`는 어느 비교에서도 사용하지 않는다(design §1). Primary
비교는 P-E vs VT-Limited(§5-6), Secondary로 계획된 VT-Unlimited는 §7에서 함께 다룬다.

---

## 5. P-E 설계

`ThreadPoolExecutor`(core=10, max=50, `SynchronousQueue`, `AbortPolicy`) — Phase 1 Config E를 그대로
포팅했다. Chat task concurrency ceiling = 50(스레드 풀 max). Overload 시 즉시(수 ms)
`RejectedExecutionException` → 503, queue 없음.

---

## 6. VT-Limited 설계

`Executors.newVirtualThreadPerTaskExecutor()` + `Semaphore(50)` 기반 admission gate. Admission
concurrency ceiling도 50(semaphore permit 수)으로 P-E와 동일하게 맞췄다. Admission 절차:

1. Tomcat 요청 스레드에서 virtual thread를 만들기 **전에** `semaphore.tryAcquire()`(non-blocking)를
   호출한다.
2. 실패하면 `outcome=rejected`로 즉시 기록, 503 응답 — virtual thread를 아예 생성하지 않는다.
3. 성공하면 그제서야 virtual thread에 task를 제출한다. Permit은 task 전체 생명주기(upstream 호출 +
   SSE relay 전부) 동안 점유되고 `finally`에서 해제된다 — P-E에서 "platform thread 50개가 다 찼다"는
   것과 동일한 의미가 되도록 점유 범위를 맞췄다.

`Semaphore`는 blocking 대기 없이 `tryAcquire()`만 사용한다 — `acquire()` 기반의 "기다리는 VT" 모델은
P-E의 admission semantics(대기 없이 즉시 accept/reject)와 달라지므로 이번 설계에 포함하지 않았다
(Future Work 후보로만 기록, design §2-1).

---

## 7. VT-Unlimited Secondary Screening 결과 (Unit 8-A)

Primary Formal(§9-§22)이 답하지 않는 질문 — "Virtual Thread admission 제한이 없을 때 Gateway가 얼마나
많은 blocking concurrency를 유지할 수 있는가, 그리고 그것이 downstream capacity 증가를 의미하는가" —
를 별도 **Secondary Screening**으로 확인했다. Primary Formal(18-run, 3회 반복)과 달리 **각 조건 1회
관찰**이며, 3회 반복 통계(median/min-max/CV)를 만들지 않는다 — scaling law로 일반화하지 않는다. Raw
결과: `docs/test-results/phase2/secondary/vt-unlimited-screening/`.

### 7.1 A-1 — Gateway-bound Screening

`THREAD_MODE=VIRTUAL_UNLIMITED`(admission gate 없음), Mock LLM 자체도 processing/waiting 제한 없음
(`MOCK_LLM_MAX_CONCURRENT_PROCESSING=0`, `MOCK_LLM_MAX_WAITING=0`) 상태에서, 표준 Phase 2 정상 SSE
workload(firstChunkDelayMs=1000, chunkIntervalMs=200, chunkCount=35)를 closed-model concurrency
100/200/500으로 각 1회 실행했다.

| VUS | Completed | Failed | Rejected | VT active peak | JVM platform-thread peak | RSS peak | CPU avg |
|---|---|---|---|---|---|---|---|
| 100 | 100 | 0 | 0 | 100 | 128 | ≈262MB | ≈0.108 core |
| 200 | 200 | 0 | 0 | 200 | 99 | ≈280MB | ≈0.169 core |
| 500 | 500 | 0 | 0 | 500 | 96 | ≈348MB | ≈0.254 core |

3단계 모두 hard-stop 조건(timeout/failed_mid_stream/failed_no_event/unexpected runtime
error/host stall/resource exhaustion/cleanup 실패) 없이 clean하게 통과했다. **Virtual task
concurrency가 100→500으로 증가했지만 JVM 전체 platform thread peak는 요청 수와 1:1로 증가하지
않았다**(96~128 범위 안에서만 움직였다 — VUS=100에서 오히려 가장 높은 128을 기록했는데, 이는 JVM
cold-start 직후 JIT/컴파일러 스레드가 아직 정리되지 않은 영향일 가능성이 있으나 원인을 확정하지는
않는다). **500 concurrency에서도 정상 완료했으며, RSS는 증가했지만 virtual task 수와 동일 비율(5배)로
증가하지는 않았다**(262MB→348MB, 약 1.33배). 각 조건 1회 screening이라는 한계를 다시 강조한다.

### 7.2 A-2 — Downstream-bound Comparison

Mock LLM의 처리 capacity를 인위적으로 좁혀(`MOCK_LLM_MAX_CONCURRENT_PROCESSING=20`, waiting은
unlimited로 두어 Mock waiting-limit rejection을 실험 변수에서 제거) closed-model VUS=60에서
VT-Limited(`CHAT_VT_LIMITED_PERMITS=20`, Gateway admission ceiling을 Mock capacity와 동일하게 맞춤)와
VT-Unlimited를 각 1회 비교했다.

| Config | Gateway completed | Gateway rejected | Mock `current_concurrency` peak | Mock `waiting_requests` peak |
|---|---|---|---|---|
| VT-Limited(permits=20) | 20 | 40 | 20 | 0 |
| VT-Unlimited | 60 | 0 | 20 | 40 |

**Latency는 두 config에서 population 성격이 달라 blended average를 직접 비교하지 않는다**(raw
`k6-summary.json` 기준):

- **VT-Limited**: admitted 20건의 TTFC는 min 1.025s ~ max 1.030s(좁은 범위, 정상 workload와 동일 —
  reject된 요청은 SSE event를 아예 받지 못해 TTFC 자체가 기록되지 않는다)로 깨끗한 단일 population
  이다. `client_stream_duration_seconds`는 completed(20건, p90/p95≈7.892s — 정상 workload 전체
  길이와 일치)와 rejected(40건, median 0.013s — 즉시 503)가 뚜렷이 분리된 두 population을 하나의
  trend로 섞은 값(avg 2.639s)이라, 이 blended average는 결론의 근거로 쓰지 않는다.
- **VT-Unlimited**: 60건 전부 완료된 단일 population이다. TTFC avg≈8.888s(min 1.026s ~ max
  16.746s), stream duration avg≈15.747s(min 7.885s ~ max 23.609s) — Mock의 20-concurrency 게이트
  뒤에서 순서대로 처리되며, 늦게 도착할수록 TTFC/전체 duration이 길어졌다.

**핵심 관찰**: Mock의 `current_concurrency` peak는 두 config 모두 **20으로 동일**하다 — admission
control 유무가 downstream 실제 처리 capacity를 바꾸지 않았다. 달라진 것은 초과분(40건)이 어디서
기다리거나 실패하는가였다: VT-Limited는 Gateway에서 즉시(median 0.013s) 거절했고, VT-Unlimited는 그
40건을 그대로 downstream(Mock `waiting_requests` peak=40)으로 넘겨 결국 완료는 시켰지만 TTFC/전체
duration이 크게 늘어났다(최대 16.7s/23.6s). **Virtual Thread는 overload control을 대체하지 않는다**
— Gateway가 더 많은 동시 연결을 유지할 수 있다는 사실(§7.1)이 곧 admission control이 불필요하다는
뜻은 아니다.

---

## 8. JFR Pinning Diagnostic 결과 (Unit 8-B)

design 문서(§6)가 요구한 `jdk.VirtualThreadPinned` JFR diagnostic을 B-0(Positive Control) →
B-1(Normal Gateway SSE) → B-2(Long Blocking Read) 3단계로 실행했다. Raw 결과:
`docs/test-results/phase2/secondary/jfr-pinning-diagnostic/`. 공통 JFC: `phase2-vt-pinning.jfc`
(`jdk.VirtualThreadPinned` enabled, stackTrace=true, **threshold=0ms**) — B-0/B-1/B-2 전부 동일 파일을
재사용했고, 중간에 threshold나 설정을 바꾸지 않았다.

### 8.1 B-0 — Positive Control

Gateway 코드가 아닌 독립 진단 도구(`scripts/diagnostic-tools/JfrPositiveControl.java`)로, virtual
thread 5개가 순서대로 `synchronized` monitor를 잡고 `Thread.sleep()`하는(알려진 pinning 유발 패턴)
워크로드를 실행했다.

- `jdk.VirtualThreadPinned` events: **5/5**(모든 intentional pinning이 검출됨)
- duration: 303~305ms(workload가 요구한 ~300ms sleep과 일치)
- 대표 stack: `Thread.sleep` → `JfrPositiveControl.lambda$main$0`
- **판정**: JFR detector pipeline(threshold=0ms 설정 포함) 자체는 정상 동작한다.

### 8.2 B-1 — Normal Gateway SSE

실제 Gateway(`gateway-mvc-java21`), `THREAD_MODE=VIRTUAL_LIMITED`, concurrency=30
(`CHAT_VT_LIMITED_PERMITS` 기본값 50 유지 — permit 경쟁 없음), 표준 Phase 2 정상 SSE workload
(firstChunkDelayMs=1000, chunkIntervalMs=200, chunkCount=35)로 `Virtual Thread → HttpURLConnection →
BufferedReader.readLine() → SSE relay` 정상 경로를 관측했다.

- `jdk.VirtualThreadPinned` events: **0**
- Gateway: 30/30 completed, 0 failed/timeout, postflight clean

### 8.3 B-2 — Long Blocking Read

동일 `THREAD_MODE=VIRTUAL_LIMITED`, concurrency=30, JDK/JFC/Gateway 코드 동일 — Mock workload만
first-chunk 대기를 ~30초로 늘려(firstChunkDelayMs=30000) virtual thread가 `readLine()`에서 장시간
blocking하는 상황을 재현했다.

- `jdk.VirtualThreadPinned` events: **0**
- **재현 확인**: `client_ttfc_seconds` avg≈30.029s(min 30.026s ~ max 30.032s)로, 설정한 ~30초
  blocking read가 실제로 재현됐음을 확인했다.
- Gateway-side: 30/30 `outcome=completed` 로그, timeout/exception 로그 0건 — Gateway 관점에서는 clean
  완료.
- **Limitation/diagnostic note**: k6 `x/sse` client는 긴 idle gap(30초) 이후 스트림당 기대 5개 SSE
  event 중 1개만 파싱하고 `client_stream_failed_total=30`으로 기록했다(Gateway 쪽 로그는 30건 전부
  정상 완료). 이는 k6 client-side SSE parsing artifact로 판단하며, **B-2를 다시 실행하지 않는다** —
  대신 이 결과를 그대로 limitation으로 기록한다: **Gateway-side blocking/JFR diagnostic(30초 blocking
  read 재현, pinning 0건 관측)은 유효하지만, 이 run의 client-side SSE completion/latency 수치는 정상
  completion evidence로 강하게 사용하지 않는다.**

### 8.4 종합 판정 (H2-d)

> "Positive control에서는 intentional pinning을 JFR이 정상 검출했지만, 테스트한 실제 Gateway의 정상
> SSE 및 약 30초 blocking-read workload에서는 observable `jdk.VirtualThreadPinned` event를 확인하지
> 못했다."

**H2-d: NOT OBSERVED UNDER TESTED CONDITIONS.**

다음으로 일반화하지 않는다:

- "`HttpURLConnection`은 pinning-safe하다"
- "Java 21 Virtual Thread에는 pinning 문제가 없다"
- "모든 workload에서 carrier pinning이 없다"

B-1/B-2는 각각 1회 관측이며, `CHAT_VT_LIMITED_PERMITS` 기본값(50) 하에서 concurrency=30(permit 여유
있음, admission 경쟁 없음)로 진행됐다 — 더 높은 concurrency/permit 포화 상태, 다른 workload(가변 지연,
다른 blocking 경로)나 다른 JDK 패치 레벨에서는 재검증이 필요하다.

---

## 9. Open-model Pilot

Formal load(R3/R6/R8) 선정 전, closed-model screening → open-model short pilot 절차(design §11)를
따랐다. Pilot은 P-E/VT-Limited 각각 **단일 컨테이너(JVM)를 R1/R3/R5/R6/R7/R8/R10 7개 rate에 재사용**해
실행했다(`docs/test-plan/phase2-formal-protocol.md` §1, §12) — Phase 1 Units 1-4와 같은 이유로 pilot의
raw 산출물 자체는 이 저장소에 별도 파일로 남아있지 않고, 그 핵심 관찰만 protocol 문서에 기록돼
있다: `jvm_threads_peak`(process-lifetime 누적값, rate별로 분리 불가) 곡선이 P-E는
36→54→76→88→94, VT-Limited는 32→36→52→52→52→52→52로, VT-Limited가 R5~R6 부근에서 이미 평평해지는
경향을 보였다. R8에서 reject 28/160(≈17.5%)이 관측됐다. 이 관찰이 Formal의
`platform_threads_window_peak` authoritative metric 재정의(§13)와 R3/R6/R8 선정(§10)의 직접
근거였다 — 다만 pilot 값 자체는 process-lifetime 오염 문제가 있어 Formal 수치로 재사용하지 않았다.

---

## 10. Formal Load R3/R6/R8 선정 근거

Pilot 관찰을 근거로 세 부하 상태를 선정했다(`phase2-formal-protocol.md` §6):

- **R3 — Stable**: pilot에서 두 config 모두 reject/timeout 0.
- **R6 — Near-capacity**: pilot에서 reject 0이나 capacity(rough estimate 50/7.8s≈6.41rps)에 근접,
  두 config의 자원 사용 차이가 pilot에서 가장 뚜렷했던 구간.
- **R8 — Over-capacity**: pilot에서 reject가 명확히 관측된 구간(≈17.5%).

이 rough capacity estimate(50/7.8s≈6.41rps)는 **Little's Law가 아니라** 단순 동시성/서비스시간 기반
어림값이다(closed concurrency를 open arrival rate로 수식 환산한 것이 아니라, 두 값을 사전 참고
범위로만 사용하고 실제 R3/R6/R8은 open-model pilot의 실측 곡선으로 확정했다) — §16에서 이 어림값과
R8의 실측 completion rate를 다시 대조한다.

---

## 11. Mac Docker Desktop Formal Environment Rejection

Phase 2 Formal Benchmark는 처음 Mac + Docker Desktop 환경에서 4차례 시도됐으나 전부 무효 처리됐다.
호스트를 완전히 재부팅한 뒤 재검증용으로 실행한 P-E R3 Stability Canary(`pe-r3-canary-run1`)에서도
동일한 문제가 재발했다:

- outcome cohort는 깨끗함(901/901 completed, timeout/failure 0) — 성공률만 보면 정상처럼 보이는 run.
- 그러나 k6 arrival이 계속되는 동안 `gateway_request_received_total`과
  `mockllm_completed_requests_total`이 **약 30~35초간 동시에 plateau**했다 — gateway.log/mock-llm.log가
  동시에 조용해진 로그 gap과도 정확히 겹쳤다.
- 그 결과 throughput population이 오염됐다: `gateway_received_rate`≈2.63 RPS(target 3.0 대비
  -12.3%), `measurement_window_completion_rate`≈2.553 RPS(-14.9%).

**핵심 메시지**: 성능 수치가 마음에 들지 않아서 환경을 바꾼 것이 아니라, **독립적인 두 metric(로그
gap + Prometheus counter plateau)이 같은 30~35초 구간에서 일치한다는 것 자체를 measurement
environment가 throughput population을 오염시키는 증거로 삼아** 환경 자체를 탈락시켰다. Root cause는
"Apple Virtualization Framework 문제"나 "Docker Desktop VM 자체가 원인"처럼 하이퍼바이저 계층까지
좁혀 확정하지 않는다 — 관찰된 것은 두 독립 프로세스(JVM/Python)가 동시에 멈췄다는 상관관계이지 그
정확한 인과 경로가 아니기 때문이다. 이 4건의 discarded attempt와 post-reboot Canary는 모두 삭제하지
않고 `docs/test-results/phase2/unit6/discarded/`에 "Mac Formal Environment Rejected" 이력으로
보존했다. 근거·상세 절차는 `docs/decisions/phase2-formal-linux-environment.md`가 authoritative
source다.

---

## 12. Native macOS Formal Environment 채택 과정

Linux(AWS EC2 등) 이전이 검토됐으나 사용자가 유료 Cloud를 쓰지 않기로 결정하면서, Docker Desktop을
완전히 제거한 **native macOS ARM64(Docker-free) harness**가 최종 대안으로 설계·실행됐다
(`docs/decisions/phase2-formal-native-macos-environment.md`). 이 환경은 Docker `cpus=1.0`/
`mem_limit=1g`를 재현하는 실험이 **아니다** — 별도의 Formal environment로, P-E/VT-Limited 둘 다 동일
host CPU에 접근하고(별도 CPU affinity/parallelism 제한 없음, `jdk.virtualThreadScheduler.parallelism`
미설정) 동일 default JVM heap ergonomics(명시적 `-Xmx` 없음, `MaxHeapSize=4GB` ergonomic)를 쓴다.
채택까지의 단계:

1. **Native Dependency Validation**(2026-08-18) — Temurin 21.0.11+10, Prometheus 3.13.2, k6
   v1.8.0+xk6-sse v0.1.11, mock venv를 전부 실측 검증.
2. **Native Functional Smoke**(2026-08-19, P-E/R1/warmup10s/measurement20s) — harness의 continuous
   lifecycle/phase boundary/client cohort/Prometheus scrape 구조가 정상 동작함을 확인. 이 과정에서
   실제 스크립트 버그 2건을 발견·수정했다(GNU `timeout` 부재로 clock check가 무의미하게 fallback되던
   문제, Prometheus 첫 scrape jitter와 경합하던 단발성 `up{}` 체크).
3. **Native RSS gap 발견·해결** — `process_resident_memory_bytes`가 macOS에서 Prometheus Java
   client에 전혀 노출되지 않는다는 것을 실측으로 확인(Linux `/proc` 전용 지표), macOS `ps -o rss=`
   기반 1초 간격 sampler를 추가해 `native_resource` 블록(§20)으로 별도 authoritative RSS 출처를
   만들었다.
4. **Native Stability Canary**(P-E R3, warmup 120s + measurement 300s, 정확히 1회,
   `pe-r3-canary-run1`) — client_cohort 901/901 completed, 0 rejection/failure,
   `measurement_window_completion_rate=3.0`(target과 정확히 일치), TTFC p95≈1.007s, stream duration
   p95≈7.855s, **counter plateau 없음**(run 자체의 leftover TSDB에 read-only Prometheus를 재기동해
   `query_range`로 직접 재확인 — 최장 "동일값 유지" 구간이 5초/2 샘플뿐, Mac 사례의 30~35초와 명확히
   다름), log gap 없음, postflight clean. 이 결과를 근거로 native macOS ARM64를 Phase 2 Primary
   Formal 환경으로 채택했다.
5. **Formal validity gate 재설계**(§14) — Canary 통과 직후 18-run Matrix를 시작했으나, 사용자가
   validity gate에 counter-plateau 신호가 빠져 있고 TSDB 삭제 정책 때문에 사후 검증도 불가능하다는
   점을 지적해 3/18 run(2 완료 + 1 진행 중) 시점에서 중단시켰다. `counter-plateau-check.json`을 매 run
   Prometheus 종료 전에 생성하도록 harness를 수정하고, matrix validity gate에 하드 게이트로
   추가한 뒤(§14) 처음부터 다시 실행했다.

---

## 13. Formal Measurement Protocol

- **Fresh process per run**: 매 run마다 fresh Gateway JVM, fresh Mock LLM, fresh Prometheus(+ fresh
  TSDB)를 새로 기동한다 — 이전 run의 프로세스를 재사용하지 않는다.
- **Continuous warm-up → measurement lifecycle**: warm-up(120s)과 measurement(300s)를 하나의 연속된
  k6 프로세스로 실행하고 그 사이에 drain을 넣지 않는다 — warm-up에서 넘어온 in-flight population이
  measurement window 시작 시점부터 이미 steady state를 이루도록 해, "빈 시스템에서 시작하는" 구조적
  편향을 제거한다.
- **Throughput population vs Outcome cohort 분리**: `measurement_window_completion_rate`(A, Primary
  throughput)는 `[measurement_start, measurement_end]` 고정 길이 창 안에서 실제 완료된 request
  수/duration이다. Outcome cohort(B)는 그 창 안에서 **시작**된 request를 drain까지 추적해 terminal
  outcome을 확정한 것으로, k6가 유일한 authoritative source다(`measurement_iterations_started_total =
  completed + rejected + failed_mid_stream + failed_no_event`). Prometheus의 windowed outcome
  count는 diagnostic 전용이며 k6 cohort와 정확히 일치해야 한다고 강제하지 않는다.
- **Drain**: measurement 종료 후 신규 iteration 유입을 중단하고, cohort에 속한 진행 중 request가 각자
  terminal outcome에 도달할 때까지 기다린 뒤(`CHAT_TOTAL_TIMEOUT_MS` + 안전 margin) 최종 집계한다.

---

## 14. Experiment Integrity

18-run Matrix는 매 run마다 다음을 확인·기록하고, 하나라도 실패하면 그 run을 즉시 INVALID로 격리하고
matrix를 중단하는 **hard validity gate**를 통과해야 공식 dataset에 들어간다:

- `dropped_iterations`∈{0, null}
- `client_cohort.invariant_ok = true`
- `client_cohort.failed_mid_stream`∈{0, null}, `failed_no_event`∈{0, null}(`rejected`는 R8의 정상적인
  overload 결과일 수 있어 hard gate에서 제외)
- `environment.json.postflight_fail = ""`
- **`counter-plateau-check.json` 존재 + `query_ok=true` + `plateau_over_20s=false`** — 이 run의 살아
  있는 Prometheus에 직접 `query_range`해 `gateway_request_received_total`/
  `mockllm_completed_requests_total`이 20초 넘게 plateau하지 않았는지 확인한 결과. 20초 기준은
  `stall_check.json`의 log-gap 기준과 통일했다(Native Canary의 clean 기준 ~5초, Mac Docker Desktop
  rejected 환경의 30~35초 관측치를 근거로).
- `stall_check.json`(gateway/mock-llm log gap)은 advisory 신호로만 쓴다(counter plateau가 더 직접적인
  증거이므로 그 자체만으로 run을 무효화하지 않는다).

**결과**: 18/18 valid, 0 invalid, 0 retry. 전체 18개 run에서 `query_ok=true`·`plateau_over_20s=false`가
항상 성립했고, 관측된 최대 plateau는 gateway 0초/mock 10초로 20초 기준에 여유 있게 미달했다.
`postflight_fail`은 전부 빈 문자열, `docker_desktop_running`은 전부 false, `available_processors`는
전부 10으로 일관됐다.

---

## 15. 18-run 결과 개요

| 항목 | 값 |
|---|---|
| Config × Load × Rep | P-E/VT-Limited × R3/R6/R8 × 3 |
| Valid / Invalid / Retry | 18 / 0 / 0 |
| 실행 순서 | rep1(P-E 먼저)→rep2(VT-Limited 먼저)→rep3(P-E 먼저), rep 내부는 항상 R3→R6→R8 (`docs/decisions/phase2-formal-native-macos-environment.md` §13) |
| 실행 시각 | 2026-08-19 11:49–14:04 UTC (약 2h15m, 무중단) |
| Warm-up / Measurement | 120s / 300s |
| Canonical raw | `docs/test-results/phase2/unit6-native/{pe,vtl}-{r3,r6,r8}-run{1,2,3}/` |
| Canonical aggregate | `docs/test-results/phase2/unit6-native/_aggregated.json` (`scripts/aggregate_phase2_native_formal.py`) |
| 제외(공식 dataset 아님) | Mac discarded 4건 + Canary(`unit6/discarded/`), Native smoke/Canary/RSS 검증 run, `*-PRE_PLATEAU_CHECK-not-counted`, `*-INCOMPLETE-aborted-not-counted` |

아래 §16~§22의 모든 값은 median(min–max), n=3(반복 3회).

---

## 16. Throughput

| Load | P-E completion (RPS) | VT-Limited completion (RPS) |
|---|---|---|
| R3 (target 3.0) | 3.000 (3.000–3.000) | 3.000 (3.000–3.000) |
| R6 (target 6.0) | 6.000 (6.000–6.000) | 6.000 (6.000–6.000) |
| R8 (target 8.0) | 6.347 (6.337–6.347) | 6.350 (6.320–6.353) |

R3/R6에서는 두 config 모두 target을 정확히 처리했다. R8에서는 두 config 모두 약 6.35 RPS 수준으로
포화했다 — 3회 반복에서 P-E와 VT-Limited 사이의 차이는 매우 작았다(중앙값 차이 0.003 RPS, 두 config의
min–max 범위가 거의 겹친다).

**R8 rough capacity estimate와의 대조**: admission ceiling=50, 실측 stream duration≈7.85s로
50/7.85≈6.37 RPS(§10에서 명시한 것과 같은 단순 동시성/서비스시간 기반 어림값 — Little's Law라고
부르지 않는다). 실측 completion throughput(P-E 6.347, VT-Limited 6.350)이 이 어림값에 근접했다.

---

## 17. Rejection

| Load | P-E rejected / cohort | P-E rejection ratio | VT-Limited rejected / cohort | VT-Limited rejection ratio |
|---|---|---|---|---|
| R3 | 0 / 901 | 0.0% | 0 / 901 | 0.0% |
| R6 | 0 / 1800 | 0.0% | 0 / 1801 | 0.0% |
| R8 | 500 / 2401 | 20.7% (20.7–20.8%) | 494 / 2400 | 20.6% (20.6–20.9%) |

R8의 rejection ratio는 3회 반복에서 두 config 사이 차이가 매우 작았다(20.7% vs 20.6%, min–max 범위가
거의 겹친다). **이번 I/O-bound workload와 동일 admission ceiling(=50) 조건에서는** rejection curve가
thread 모델이 아니라 admission ceiling 자체에 의해 결정되는 것으로 보인다 — 이 결론을 "thread model은
어떤 상황에서도 throughput에 영향을 주지 않는다"로 일반화하지 않는다.

---

## 18. TTFC / Stream Duration

| Load | P-E TTFC p50/p95/p99 (s) | VT-Limited TTFC p50/p95/p99 (s) | P-E stream duration p50/p95 (s) | VT-Limited stream duration p50/p95 (s) |
|---|---|---|---|---|
| R3 | 1.005 / 1.006 / 1.007 | 1.005 / 1.006 / 1.007 | 7.841 / 7.853 | 7.869 / 7.878 |
| R6 | 1.004 / 1.006 / 1.006 | 1.004 / 1.006 / 1.007 | 7.843 / 7.874 | 7.866 / 7.878 |
| R8 | 1.004 / 1.005 / 1.006 | 1.004 / 1.006 / 1.007 | 7.841 / 7.873 | 7.837 / 7.850 |

전 구간에서 두 config 사이 차이는 반복 측정의 변동 범위에서 실질적인 차이를 확인하기 어려웠다.
Mock LLM workload가 고정 지연(`firstChunkDelayMs=1000`, 총 스트림 길이≈7.85~7.88s)이므로, thread
모델이 admission된 이후의 per-request latency에 영향을 주지 않는 것은 이 워크로드 특성상 예상 가능한
결과다.

---

## 19. JVM Platform Threads

**`platform_threads_window_peak`**(measurement window 안에서의 JVM 전체 platform thread peak —
Tomcat 자신의 스레드, GC/JIT 스레드, chat executor 스레드/carrier를 모두 포함한 `ThreadMXBean` 기반
JVM 전체 지표. "worker thread"나 "carrier thread" 개수가 아니다 — JDK 21에는 carrier thread 수를
직접 얻는 단순 public API가 없어 이 값은 formal metric에 포함하지 않았다):

| Load | P-E | VT-Limited | 변화율 |
|---|---|---|---|
| R3 | 47 (47–47) | 33 (32–34) | 약 29.8% 감소 |
| R6 | 71 (71–71) | 35 (34–35) | 약 50.7% 감소 |
| R8 | 73 (73–73) | 35 (35–36) | 약 52.1% 감소 |

이번 데이터셋에서 가장 뚜렷하고 부하-의존적인 차이다. P-E는 부하가 늘수록 JVM platform thread
peak가 계속 증가한 반면(47→71→73), VT-Limited는 33~35 수준에서 부하와 거의 무관하게 유지됐다. P-E
쪽 3회 반복의 CV는 0%(완전히 결정적), VT-Limited는 R3에서 3.0%, R6/R8에서 1.6~1.7%로 아주 작은
변동만 있었다.

---

## 20. Native RSS

**Authoritative 출처는 `native_resource.rss_measurement_{start,peak,delta}_bytes`뿐이다.**
`result.json` 최상위의 `rss_measurement_start_bytes`/`rss_peak_bytes`/`rss_delta_bytes`는
Docker harness 스키마에서 상속된 필드로, native macOS에서 `process_resident_memory_bytes`가
Prometheus Java client에 노출되지 않아(Linux `/proc` 전용 지표) 18-run 전부에서 항상 0이다 — 이
필드를 native RSS 값으로 절대 사용하지 않는다. Native RSS는 macOS `ps -o rss=`(Gateway PID 대상,
1초 간격, P-E/VT-Limited 동일 방식) sampler로 별도 수집했다.

| Load | P-E RSS peak (MB) | VT-Limited RSS peak (MB) | 차이 |
|---|---|---|---|
| R3 | 242.2 (227.2–261.5) | 255.9 (245.0–259.6) | VT +5.7% |
| R6 | 263.5 (252.0–292.7) | 272.0 (249.3–277.1) | VT +3.2% |
| R8 | 282.8 (276.3–297.5) | 272.5 (269.0–273.4) | VT -3.6% |

**Platform thread 감소가 RSS 감소로 일관되게 연결되지는 않았다.** R3/R6에서는 오히려 VT-Limited의
RSS peak가 P-E보다 높았고, R8에서만 VT-Limited가 소폭(-3.6%) 낮았다. RSS delta(measurement_start→
peak)는 두 config 모두 CV 12~52%로 상대적으로 noisy해 강한 결론의 근거로 쓰지 않았다 — 위 표는
CV가 훨씬 낮은(1~11%) 절대 start/peak 값을 중심으로 해석한 것이다.

---

## 21. CPU

| Load | P-E cpu_avg (cores) | VT-Limited cpu_avg (cores) | 차이 |
|---|---|---|---|
| R3 | 0.0255 (0.0255–0.0293) | 0.0302 (0.0265–0.0354) | VT +18.2% |
| R6 | 0.0400 (0.0394–0.0458) | 0.0509 (0.0467–0.0537) | VT +27.4% |
| R8 | 0.0462 (0.0442–0.0526) | 0.0564 (0.0535–0.0598) | VT +22.1% |

VT-Limited가 상대적으로 약 18~27% 높은 방향이 세 조건 모두에서 일관되게 나타났다. 단 **절대
사용량은 양쪽 모두 0.06 core 미만으로 매우 낮았다**(10-core host 기준 여유가 매우 크다). Virtual
Thread scheduler/continuation 관리 등 런타임 비용 가능성이 있으나, JFR diagnostic(§8)은 pinning
0건을 확인했을 뿐 이 CPU 차이의 원인을 별도로 profiling하지는 않았다 — 원인은 확정하지 않는다.

---

## 22. Heap / GC

| Load | P-E heap peak (MB) | VT-Limited heap peak (MB) | P-E GC count/pause(s) | VT-Limited GC count/pause(s) |
|---|---|---|---|---|
| R3 | 71.5 | 66.5 | 3 / 0.011 | 3 / 0.013 |
| R6 | 75.4 | 84.3 | 6 / 0.016 | 4 / 0.012 |
| R8 | 89.1 | 87.4 | 5 / 0.014 | 5 / 0.014 |

뚜렷한 방향성이 없다 — R3는 P-E가 더 높고, R6은 VT-Limited가 더 높고, R8은 거의 같다. heap_delta는
두 config 모두 CV가 최대 117%까지 올라가는 run도 있어 매우 noisy했다. Heap/GC는 이번 실험에서
secondary metric으로만 취급하며, Virtual Thread가 Heap을 확실하게 절감/증가시켰다고 결론 내리지
않는다.

---

## 23. 가설별 판정

Phase 2 설계 문서(`phase2-design.md`)의 원래 서술을 이번 보고서의 canonical hypothesis 명칭
H2-a~H2-f(Primary)와 S1-S3(Secondary)로 대응시켜 판정한다.

### 23.1 Primary (18-run Formal)

| ID | 가설 | 판정 | 근거 |
|---|---|---|---|
| H2-a | 동일 admission ceiling(=50)에서 P-E와 VT-Limited의 completion capacity가 유사하다 | **SUPPORTED** | §16 — R3/R6에서 두 config 모두 target 정확히 달성, R8에서 두 config 모두 ≈6.35 RPS로 수렴, 3회 반복에서 실질적 차이 확인 어려움 |
| H2-b | VT가 JVM platform thread 사용량을 감소시킨다 | **SUPPORTED** | §19 — R3 -29.8%, R6 -50.7%, R8 -52.1%, 부하 증가와 함께 격차 확대 |
| H2-c | VT의 platform-thread 절감이 RSS 절감으로 이어진다 | **NOT SUPPORTED** | §20 — R3/R6은 오히려 VT RSS가 더 높음(+5.7%/+3.2%), R8만 소폭 낮음(-3.6%), 일관된 방향성 없음 |
| H2-d | 실제 테스트한 Gateway workload에서 carrier pinning이 관찰된다 | **NOT OBSERVED UNDER TESTED CONDITIONS** | §8 — JFR positive control(B-0)로 detector pipeline 정상 확인(5/5 검출) 후, 실제 Gateway 정상 SSE(B-1)·~30초 blocking read(B-2) 모두에서 `jdk.VirtualThreadPinned` 0건. 다른 workload/concurrency로 일반화하지 않음(§8.4) |
| H2-e | VT가 CPU까지 감소시킨다 | **NOT SUPPORTED** | §21 — 오히려 세 부하 전부에서 VT의 cpu_avg가 18~27% 더 높음(절대값은 여전히 낮음, <0.06 core) |
| H2-f | 동일 admission policy에서 overload rejection curve가 두 방식에서 유사하다 | **SUPPORTED** | §17 — R8 rejection ratio 20.7% vs 20.6%, 3회 반복에서 실질적 차이 확인 어려움 |

### 23.2 Secondary (Unit 8-A Screening, 각 조건 1회)

| ID | 가설 | 판정 | 근거 |
|---|---|---|---|
| S1 | VT-Unlimited가 Gateway 자신의 concurrency를 크게 확장할 수 있다 | **SUPPORTED BY SCREENING** | §7.1 — VUS 100/200/500 전부 clean(0 failed/rejected), JVM platform thread peak가 요청 수와 1:1로 증가하지 않음(96~128 범위) |
| S2 | VT-Unlimited가 downstream(Mock) capacity 자체를 증가시킨다 | **NOT SUPPORTED** | §7.2 — Mock `current_concurrency` peak가 VT-Limited/VT-Unlimited 모두 20으로 동일, admission 제거가 실제 처리 capacity를 바꾸지 않음 |
| S3 | Downstream capacity 제한 하에서는 여전히 admission control이 필요하다 | **SUPPORTED BY DOWNSTREAM-BOUND SCREENING** | §7.2 — admission 제거 시 초과 40건이 Gateway reject 대신 Mock waiting queue(peak 40)로 이동, TTFC 최대 16.7s/stream duration 최대 23.6s로 tail latency 급증 |

각 조건 1회 screening 결과이므로, S1-S3는 Primary(H2-a~f)와 같은 반복측정 신뢰도를 갖지 않는다.

---

## 24. Primary Trade-off

동일 admission ceiling=50 → completion capacity 거의 동일(H2-a) → rejection curve 거의 동일(H2-f) →
TTFC 거의 동일(§18). 하지만 **JVM platform thread 사용량은 부하와의 관계가 근본적으로 다르다** —
P-E는 부하 증가와 함께 platform thread peak가 계속 늘어나고(47→71→73), VT-Limited는 33~35 수준에서
비교적 평평하게 유지된다(H2-b). 즉 Primary가 보여준 Virtual Thread의 핵심 효과는 **"downstream
capacity를 늘린 것"이 아니라 "blocking concurrency와 platform thread 증가를 분리한 것"**이다.

그러나 **platform thread 절감이 RSS/CPU 절감으로 자동 연결되지는 않았다**(H2-c, H2-e) — R3/R6에서는
VT-Limited의 RSS가 오히려 더 높았고, CPU는 세 부하 전부에서 VT-Limited가 더 높았다(다만 절대값은
작다). "Virtual Thread를 쓰면 thread도 줄고 메모리/CPU도 줄어든다"는 단순화된 서사는 이번 실험
결과와 맞지 않는다 — thread-count 이점과 memory/CPU 비용을 함께 제시해야 정확하다.

---

## 25. Secondary Trade-off & Phase 2 핵심 결론

Primary(§24)와 Secondary(§7, §8)를 하나의 흐름으로 정리하면:

```
Blocking Platform Thread (Phase 1 Config E, Phase 2 P-E)
  → concurrency 증가
  → JVM platform thread 증가

Virtual Thread + 동일 admission ceiling (VT-Limited, §16-§22)
  → completion capacity 동일 수준 (H2-a)
  → rejection curve 동일 수준 (H2-f)
  → TTFC 동일 수준 (§18)
  → JVM platform thread peak 약 50~52% 감소 (H2-b)
  → 그러나 RSS 감소 보장 없음 (H2-c)
  → CPU 감소 보장 없음, 오히려 18~27% 증가 (H2-e)

Virtual Thread Unlimited (admission 제거, §7)
  → Gateway는 500 concurrent까지 clean하게 유지 가능 (S1, §7.1)
  → 그러나 downstream capacity는 증가하지 않음 (S2, §7.2 — Mock current_concurrency는 20으로 고정)
  → admission 제거 시 초과분은 사라지지 않고 downstream waiting으로 이동
    (S3 — Mock waiting peak 40, TTFC 최대 16.7s, stream duration 최대 23.6s)
```

**Java 21 실무 적용 기준**(이번 실험 범위 안에서 도출 가능한 시사점, 범위를 벗어난 일반화는 §26에서
명시):

- 동일 blocking I/O 구조를 유지한 채 Chat Executor만 Virtual Thread로 바꾸는 것은, 이 워크로드/이
  admission ceiling 조건에서 completion capacity나 latency를 희생하지 않는다 — 최소한 손해는
  아니었다.
- JVM platform thread 수를 부하 증가와 분리하고 싶다면(운영 중인 스레드/스택 수를 예측 가능한
  범위로 유지하고 싶다면) Virtual Thread 전환이 뚜렷한 효과를 낸다 — 특히 near/over-capacity(R6/R8)
  구간에서 그 격차가 크다(-50~52%).
- "메모리/CPU를 줄이기 위해" Virtual Thread를 도입하는 것은 이번 데이터로 정당화되지 않는다 —
  RSS는 방향이 일정하지 않았고 CPU는 오히려 소폭 더 썼다. 리소스 절감이 아니라 thread-model 단순화/
  운영 가시성 개선이 주 동기가 되어야 한다.
- pinning 리스크(H2-d)는 정상 SSE·~30초 blocking read 두 workload 모두에서 관측되지 않았다 —
  그러나 이는 이 두 workload에 한정된 관찰이며, 다른 concurrency/permit 포화 상태나 다른
  `HttpURLConnection`/`synchronized` 경로가 섞인 실제 프로덕션 코드에 그대로 일반화하기 전에는 별도
  JFR 검증이 필요하다.
- **Virtual Thread는 downstream capacity 기반 admission control을 대체하지 않는다** — Gateway
  자신이 더 많은 동시 연결을 유지할 수 있어도(S1), downstream이 처리할 수 있는 양은 그대로다(S2).
  Virtual Thread + downstream capacity 기반 Semaphore/admission control + timeout + fast-fail +
  monitoring을 함께 써야 초과 부하가 예측 불가능한 tail latency로 downstream에 쌓이는 것을 막을 수
  있다(S3).

---

## 26. Phase 2 Limitations

- **VT-Unlimited Secondary는 3회 반복 Formal이 아니라 각 조건 1회 screening이다**(§7) — Primary
  Formal(18-run)과 같은 반복측정 신뢰도(median/min-max/CV)를 갖지 않는다. Scaling law로 일반화하지
  않는다.
- **B-2의 k6 client-side SSE parsing artifact**(§8.3) — 30초 idle gap 이후 k6 `x/sse` client가 기대한
  이벤트를 모두 파싱하지 못해 `client_stream_failed_total`이 실제 Gateway 실패를 의미하지 않는다.
  Gateway 자체는 30/30 정상 완료했다 — 이 run의 client-side latency/completion 수치는 정상 completion
  evidence로 강하게 사용하지 않는다.
- **JFR diagnostic(B-1/B-2)은 단일 조건에서만 관측됐다** — concurrency=30(permit 50 중 여유 있음,
  admission 경쟁 없음). 더 높은 concurrency/permit 포화 상태, 다른 workload(가변 지연, 더 큰
  payload)에서 pinning이 나타나지 않는다고 일반화하지 않는다.
- **design §7이 계획한 P-E/VT-Limited 전용 formal metric 중 일부가 실제로 수집되지 않았다** —
  executor active/pool size peak(P-E 전용), virtual task active peak/started/finished total
  (VT-Limited 전용)은 `collect_phase2_formal_result.py`에 별도 필드로 구현되지 않았다. Admission
  reject 자체는 공통 `client_cohort.rejected`로 커버되지만(§17), 위 세부 지표는 이번 18-run 결과에
  없다.
- **단일 host, 단일 실행 세션(Primary)** — 18개 Primary run 전부가 같은 native macOS ARM64 host에서
  한 세션(2026-08-19 11:49–14:04 UTC) 안에 연속 실행됐다. Secondary(§7, §8)는 같은 host에서 별도
  세션(2026-08-19~20)으로 실행됐다. 다른 host/다른 시점에서의 재현성은 확인하지 않았다. 통계적
  유의성 검정은 별도로 수행하지 않았다 — 본 보고서의 "차이가 작았다/컸다"는 서술은 반복의
  median/min–max/CV 범위에 대한 기술(記述)이지 가설 검정 결과가 아니다.
- **범위 한정**: 이번 결과는 **동일 native macOS ARM64 환경 내부의 P-E vs VT-Limited vs VT-Unlimited
  비교**로만 해석한다. Linux production server, Docker `cpus=1`/`mem_limit=1g` 제한 환경, 다른 CPU
  architecture로 절대 수치(RSS/CPU/latency)를 직접 일반화하지 않는다. Docker attempt(§11, discarded)와
  Native Formal(§12)의 절대 CPU/RSS/latency도 서로 직접 비교하지 않는다 — 측정 환경 자체가 다르기
  때문이다.
- **워크로드 범위**: Primary는 고정 지연(1s first-chunk + 200ms×35 chunk) SSE 스트림이라는 단일
  워크로드 형태로만 측정했다. Secondary는 진단 목적으로 first-chunk delay를 의도적으로 변형했다
  (B-2: ~30s, A-2: Mock capacity 제한) — 다른 지연 분포/payload 크기에서 결과가 달라질 수 있다.

---

## 27. Phase 2 Freeze / 남은 질문 (Phase 3용)

**Phase 2 = COMPLETED.** Primary Formal(18-run) + JFR Pinning Diagnostic(B-0/B-1/B-2) + VT-Unlimited
Secondary Screening(A-1/A-2)까지 계획된 범위를 모두 실측했다. 이 보고서 갱신 이후 Phase 2 범위의 추가
Benchmark, 추가 JFR diagnostic, 추가 VT-Unlimited run은 수행하지 않는다.

이번 실측으로 답하지 못한, Phase 3 이전에 검토할 수 있는 질문들:

- 이번 CPU/RSS 반직관적 결과(§20, §21)가 이 워크로드(고정 지연, 짧은 스트림)에 특화된 것인지, 더
  다양한 워크로드 형태(가변 지연, 더 긴 concurrency-hold 시간, CPU-bound 구간 포함)에서도
  재현되는지 확인이 필요하다.
- VT-Unlimited Secondary Screening(§7)을 3회 반복 Formal로 승격하면, S1-S3의 scaling 신뢰도를
  Primary 수준으로 높일 수 있다.
- JFR diagnostic(§8)을 admission-saturated concurrency(permit 포화)나 다른 workload(가변 지연, 다른
  blocking 경로)에서 재실행하면, H2-d "NOT OBSERVED UNDER TESTED CONDITIONS"의 관찰 범위를 더 넓힐 수
  있다.
- design §7이 원래 요구한 P-E/VT-Limited 전용 세부 metric(executor pool peak, virtual task
  started/finished total)을 수집기에 추가하면 admission gate 내부 동작을 더 세밀하게 검증할 수 있다.
- Linux(비-Docker-Desktop) 환경에서 같은 18-run/Secondary screening을 재현하면, 이번 native macOS
  결과가 architecture/OS에 독립적인지 확인할 수 있다.

이 항목들은 Phase 2 범위가 아니라 Phase 3 시작 여부와 함께 별도로 결정한다.
