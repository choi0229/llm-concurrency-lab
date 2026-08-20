# ADR: Phase 2 Formal Benchmark — Mac Docker Desktop 환경 탈락 및 Linux 환경 이전

Status: Superseded(AWS/GCP 등 유료 Cloud 경로는 사용자가 채택하지 않기로 결정 — §1~§11의 "Mac
Docker Desktop 탈락" 판정 자체는 여전히 유효하나, 그 이후의 실행 경로는 `docs/decisions/
phase2-formal-native-macos-environment.md`(native macOS ARM64, Docker-free)로 대체됐다.)
Date: 2026-08-17 (superseded 2026-08-18)

## 문제

Phase 2 Unit 6 Formal Benchmark(P-E vs VT-Limited × R3/R6/R8 × 3 rep = 18 measured runs)를 Mac +
Docker Desktop 환경에서 4차례 시도했으나 전부 무효 처리됐고(`docs/test-results/phase2/unit6/README.md`),
호스트를 완전히 재부팅한 뒤 재검증용으로 실행한 **P-E R3 Stability Canary**(2026-08-17,
`pe-r3-canary-run1`, Formal 18-run에 미포함)에서도 동일한 문제가 재발했다:

- clock drift = 0s (pre/post 모두)
- outcome cohort는 깨끗함 — timeout=0, failure=0, unexpected_error=0, `cross_check_ok=true`
- TTFC p95 ≈ 1.008s, stream duration p95 ≈ 7.972s — 둘 다 정상 범위

이었음에도, k6 arrival(target 3.0 RPS)이 계속되는 동안 `gateway_request_received_total`과
`mockllm_completed_requests_total`이 약 30~35초간 **동시에 plateau**했다(`01:37:54`–`01:38:29` UTC 부근,
두 독립 metric 모두 `query_range`로 직접 확인). 이 구간은 gateway.log/mock-llm.log가 동시에 조용해진
로그 gap과도 정확히 겹친다. 그 결과 Formal 판단의 핵심인 throughput population이 다시 오염됐다:

- `gateway_received_rate` ≈ 2.63 RPS (target 3.0 대비 약 -12.3%)
- `measurement_window_completion_rate` ≈ 2.553 RPS (target 3.0 대비 약 -14.9%)

**결정**: 재부팅으로도 해소되지 않았으므로, Mac Docker Desktop에서는 Phase 2 Formal Benchmark를 더 이상
재시도하지 않는다. Official Formal Valid Dataset은 계속 0/18로 유지하며, 이번 Canary도 Formal dataset에
포함하지 않는다.

## 1. Root cause 표현에 대한 원칙

지금까지 관찰된 사실(로그 gap, Prometheus counter plateau, 재부팅 후에도 재발)만으로 **"Apple
Virtualization Framework 문제"**나 **"Docker Desktop VM 자체가 원인"**이라고 확정하지 않는다. 근거가
아직 그 수준의 특정 원인 규명(root cause를 하이퍼바이저 계층까지 좁히는 것)을 지지하지 않기 때문이다 —
관찰된 것은 두 개의 독립적인 컨테이너 프로세스(JVM/Python)가 동시에 멈췄다는 상관관계이지, 그 하위의
정확한 인과 경로가 아니다.

이 프로젝트가 표현을 제한할 문구:

> 현재 Mac + Docker Desktop benchmark 환경에서 재현 가능한 environment-level stall이 반복 발생했고,
> Formal throughput 측정의 신뢰성을 확보할 수 없었다.

정확한 root cause는 **미확정으로 남긴다**. 향후 어떤 문서(README, 최종 보고서, 이 ADR 갱신 포함)도 이
이상으로 원인을 단정하는 표현을 쓰지 않는다.

## 2. 환경 역할 분리

| 환경 | 용도 |
|---|---|
| Mac + Docker Desktop | 개발, 기능 검증, Screening(Phase 1 Unit 5류 탐색적 비교), JFR diagnostic |
| Linux + Docker Engine | Phase 2 Formal Benchmark(18-run 공식 dataset) 전용 |

Mac에서 Phase 2 Formal을 더 이상 재시도하지 않는다. Phase 1(Java 8 executor matrix)과 Phase 2
Screening/JFR 작업은 이 결정의 영향을 받지 않으며 계속 Mac에서 수행한다 — 지금까지 관찰된 stall은
"짧은 stall이 throughput 정밀 측정을 오염시킨다"는 문제이지, 기능이 깨지거나 정성적 비교가 불가능하다는
뜻은 아니기 때문이다.

## 3. Linux Formal Host 요구사항

Formal Benchmark를 실행할 Linux 환경은 아래를 모두 충족해야 한다:

- Linux kernel 위에서 **Docker Engine을 직접 실행**한다(Docker Desktop 계열의 VM 중개 계층 없음).
- **QEMU/architecture emulation 없음** — 컨테이너 아키텍처가 host 아키텍처와 정확히 native로 일치해야
  한다(§4).
- benchmark 실행 중 unrelated workload가 없다 — 이번 Mac 사례에서 다른 프로젝트(`sparta-ecommerce`)의
  컨테이너가 `restart: always`로 자동 기동해 preflight를 막았던 것과 같은 상황을 Linux Formal host에서는
  구조적으로 배제한다(예: benchmark 전용 host/VM으로 유지, 또는 실행 직전 `docker ps -a`로 무관한
  컨테이너 부재를 매 run preflight에서 재확인 — 기존 `run-phase2-formal-benchmark.sh`의 preflight 로직
  그대로 재사용).
- host의 sleep/suspend가 없다 — 절전 진입은 Mac 사례에서 clock drift의 알려진 트리거였다
  (`docs/decisions/version-compatibility.md`가 아니라 [[docker-desktop-clock-drift]] 메모리 참고).
- 안정적인 clock/NTP.
- 기존과 동일한 Docker CPU/memory limit(`cpus`/`mem_limit`)을 그대로 적용 가능해야 한다.
- `k6-sse` 커스텀 이미지(k6 v1.8.0 + xk6-sse v0.1.11)를 빌드/실행할 수 있어야 한다.
- Prometheus/Gateway(`gateway-java21`)/Mock LLM(FastAPI)을 동일 `docker-compose.yml`(`--profile
  phase2`)로 실행할 수 있어야 한다.

**Cloud Linux VM도 허용한다** — bare-metal일 필요는 없다. 단, **Mac 위에 추가로 얹는 Linux VM/UTM/
Docker Desktop류 환경은 Formal 대체 환경으로 사용하지 않는다** — 그 경우 여전히 Mac 하이퍼바이저/호스트
OS 스케줄링을 한 겹 거치게 되어, 지금 배제하려는 것과 같은 계층의 문제를 재도입할 위험이 있기 때문이다.
Cloud VM(예: 별도 클라우드 벤더의 Linux 인스턴스)이나 별도의 물리 Linux 머신이어야 한다.

### 3-1. Machine specification

| 항목 | 최소 실행 가능(기록용, 권장 아님) | **Formal 권장** |
|---|---|---|
| CPU | 4 vCPU | **8 vCPU** |
| Memory | 8 GB | **16 GB** |
| Storage | SSD/NVMe (spinning disk 금지) | SSD/NVMe |
| Instance 종류 | — | **non-burstable**(CPU credit 방식 금지) |
| OS | native Linux(Ubuntu LTS 등) | 동일 |
| Docker | Docker Engine 직접 실행(Docker Desktop 금지) | 동일 |
| Architecture | native arm64 우선, 불가 시 native amd64(QEMU 금지) | 동일 |

권장 사양을 4 vCPU/8GB가 아니라 **8 vCPU/16GB로 정하는 이유**: 이 host 위에서 동시에 떠야 하는 것은
Gateway(고정 `cpus=1.0`) + Mock LLM(고정 `cpus=2.0`) + k6 부하 생성 프로세스 + Prometheus + Docker
Engine/Linux 커널 자체다. 이들의 합이 4 vCPU에 근접하면 host 레벨 CPU 스케줄링 경합이 발생할 여지가
생기고, 이는 Mac 사례에서 관찰된 것과 유사한 "짧은 정지"류 증상을 다른 원인으로 재도입할 위험이 있다.
8 vCPU/16GB는 위 4개 구성요소가 서로 자원을 두고 경쟁하지 않을 충분한 여유를 준다. 4 vCPU/8GB는
기능 검증 등 비-Formal 목적의 최소 실행 가능 사양으로만 기록하고, Formal 18-run에는 사용하지 않는다.

burstable(AWS T-series, Azure B-series 등 CPU credit 기반) 인스턴스는 금지한다 — credit 소진 시
throttling이 발생하며, 이는 host가 예고 없이 일시적으로 느려지는 것과 동일한 효과를 내 이번에 배제하려는
문제 유형(원인 불명의 일시적 처리 지연)을 다시 만들 수 있다.

## 4. Architecture 결정 규칙

우선순위:

1. **native Linux arm64** — 기존 Mac(arm64) 환경과의 연속성이 가장 좋다(동일 JDK
   image manifest 계열, digest 재확인만 필요).
2. **native Linux amd64** — arm64를 구할 수 없을 경우 사용한다. Phase 2의 핵심 비교(P-E vs
   VT-Limited)는 amd64에서도 유효하다 — 이 비교는 동일 host/동일 아키텍처 내에서의 상대 비교이기
   때문이다.

amd64를 사용할 경우 지켜야 할 것:

- **모든 Phase 2 Formal run을 amd64로 통일**한다(같은 18-run 안에서 arm64/amd64를 섞지 않는다).
- JDK amd64 image digest를 **새로 실측하여 기록**한다(`docker manifest inspect
  eclipse-temurin:21.0.11_10-{jdk,jre}-jammy`로 amd64/x86_64 manifest 존재 확인 후 digest pin,
  `docs/decisions/version-compatibility.md` §6과 동일한 절차).
- Mock LLM/k6/Prometheus 이미지도 모두 **native amd64**로 pull/실행한다.
- **QEMU 사용을 금지**한다 — amd64 host에서 arm64 이미지를 emulation으로 돌리는 것도, 반대도 금지.

그리고 반드시 명시할 것: **Phase 1(Mac arm64 결과) vs Phase 2(Linux amd64로 결정될 경우)** 사이의
절대 resource 수치(CPU, RSS, absolute latency 등)는 OS/architecture confound가 있으므로 **직접 동일
환경 비교로 표현하지 않는다**(§10에서 다시 다룸).

## 5. Runtime 버전 고정 — 변경되는 것은 host 환경뿐

Linux로 옮기더라도 아래 Phase 2 Formal Protocol의 고정값은 **그대로 유지**한다(가능한 아키텍처 범위
내에서 — amd64로 갈 경우 image digest만 재실측):

| 항목 | 고정값 |
|---|---|
| JDK | Eclipse Temurin 21.0.11+10-LTS |
| Spring Boot | 4.1.0 |
| Gradle | 8.14 |
| k6 | v1.8.0 |
| xk6-sse | v0.1.11 |
| Mock workload | `firstChunkDelayMs=1000`, `chunkCount=35`, `chunkIntervalMs=200` |
| Gateway 리소스 | `cpus=1.0`, `mem_limit=1g` |
| Mock 리소스 | `cpus=2.0`, `mem_limit=1g` |

변경되는 것은 **benchmark host 환경(Mac Docker Desktop → Linux Docker Engine, 필요 시 아키텍처)뿐**이며,
애플리케이션/런타임 버전, 워크로드 파라미터, 리소스 제한은 Unit 6 프로토콜(`docs/test-plan/
phase2-formal-protocol.md`)에서 바뀌지 않는다.

## 6. Docker / Kernel Freeze Policy

Linux host가 결정되고 Formal 시작 직전, 아래를 1회 기록한다(Linux Canary 실행 전, §7 이전 단계):

```
docker version
docker compose version
uname -a
```

(커널 버전은 `uname -a`의 출력에 포함됨 — 별도 명령 불필요.)

**18개 Formal run이 전부 끝날 때까지, 아래를 성능에 영향을 줄 수 있는 방식으로 업데이트하지 않는다**:

- Docker Engine 버전
- kernel 버전
- OS 패키지(특히 커널/glibc/컨테이너 런타임 관련 패키지)

이유: §5에서 애플리케이션/런타임 버전을 고정하는 것과 같은 원칙이다 — 18-run 도중 host 기반이 바뀌면
run 사이에 새로운 confound가 생겨, §7의 "18개 run 전부가 동일 environment"라는 전제가 깨진다. 보안
패치처럼 지연할 수 없는 예외가 생기면, 적용 전후로 host를 재선정한 것과 동일하게 취급해 그 시점 이전에
확보한 run과 이후 run을 같은 dataset으로 묶지 않는다(재-Canary부터 다시 시작).

## 7. Linux environment metadata — 매 run 기록

Mac 환경의 `environment.json`이 이미 기록하던 항목(§ `run-phase2-formal-benchmark.sh`)에 더해, Linux
Formal run에서는 아래를 추가로 기록한다:

- `uname -a`
- distro/version (예: Ubuntu 24.04)
- kernel version
- CPU architecture
- CPU model
- logical CPU count
- host memory (총량)
- Docker Engine version
- container architecture(= host와 native 일치 확인)
- JDK full version(`java -version` 실측)
- image digest(JDK, k6-sse, Prometheus, mock-llm base image)
- CPU/memory limits(compose에 실제 적용된 값)
- clock/NTP 상태(`timedatectl` 또는 동등 도구 출력)

**18개 run 전부가 동일 host/동일 environment metadata인지 확인**한다 — 하나라도 다르면 해당 run은
격리해 별도 표기하거나 재실행한다(Formal dataset 내부의 환경 일관성이 P-E vs VT-Limited 비교의 유효성
전제이기 때문).

## 8. Linux Stability Canary — 18-run 이전 필수 게이트

Linux 환경으로 옮긴 뒤에도 곧바로 18-run을 시작하지 않는다. Mac에서 했던 것과 동일하게, **P-E R3
Stability Canary를 정확히 1회** 수행한다. Canary는 Formal 18-run에 포함하지 않는다.

통과 조건(전부 충족 필요, 2026-08-17 개정 — §8-1 근거로 rate 항목을 percentage threshold가 아닌
hard gate로 재정의):

- `dropped_iterations = 0`
- `timeout = 0`
- `failure = 0`
- `unexpected_error = 0`
- `actual_started`(k6 측 실제 시작 rate)가 k6 target을 정상 유지(k6 자신의 executor가 목표 rate를
  달성하지 못했다는 신호 — 예: 과도한 VU 부족 — 가 없는지)
- `gateway_request_received_total`에 장기 plateau 없음
- `mockllm_completed_requests_total`에 장기 plateau 없음
- client TTFC p95 ≈ 1s
- stream duration p95 ≈ 7.9s
- final request accounting 정상(`client_cohort.invariant_ok = true` — k6가 유일한 cohort source,
  §14. Prometheus `server_diagnostic`는 diagnostic 전용이며 이 항목의 pass 조건에 포함하지 않는다)
- clock drift 정상(≤5초)
- postflight clean

`actual_started_rate`와 `gateway_received_rate`의 차이는 위 hard gate를 통과하는 한 **sanity/report
metric으로 기록**하되, 그 자체로 PASS/FAIL을 가르는 percentage threshold로 쓰지 않는다 — 근거는
§8-1.

**판정 원칙(Mac에서 확립, 그대로 적용) — 4신호 종합판단**: 로그 gap 단독으로 stall 여부를 판정하지
않는다. 아래 4개 신호를 함께 본다:

1. **로그 gap** — gateway.log / mock-llm.log가 동시에 20초 넘게 조용해지는 구간(`stall_check.json`).
2. **Prometheus counter plateau** — `gateway_request_received_total`, `mockllm_completed_requests_total`
   등 기존 노출 카운터가 k6 arrival이 계속되는데도 여러 scrape 구간에 걸쳐 증가하지 않는지 직접
   `query_range`로 확인(새 heartbeat metric을 추가하지 않고 기존 metric만 사용).
3. **Clock drift** — host vs container, 5초 초과 시 하드 스톱.
4. **Timeout/failure burst** — outcome cohort의 timeout/failure가 특정 시간대에 몰려 있는지.

로그 gap이 있어도 counter plateau·timeout burst가 없고 outcome cohort가 깨끗하면 즉시 invalid로
단정하지 않고 throughput population에 대한 영향만 국소적으로 검토한다(Mac `pe-r3-run1-attempt2` 사례).
반대로 로그 gap과 counter plateau가 같은 구간에서 일치하면(Mac Canary 사례) — outcome cohort가
깨끗하더라도 — 두 독립 채널이 일치한다는 것 자체가 stall의 강한 증거로 간주한다.

Linux Canary가 실패할 경우에도 자동 재시도하지 않고, 사용자에게 보고 후 다음 단계를 다시 논의한다
(Mac 사례와 동일한 원칙) — Mac에서처럼 결과를 억지로 valid 처리하지 않는다.

### 8-1. `actual_started_rate` vs `gateway_received_rate` — percentage threshold를 쓰지 않는다 (2026-08-17 확정)

**폐기된 대안 — Poisson 변동폭**: 이전 초안은 R3(λ=3RPS×300s=900)를 Poisson 도착 과정으로 보고
표준편차 √900≈30(≈±3.3%)를 허용 오차로 제안했으나, **이 근거는 부적절해 폐기한다** — 현재 부하
생성기(k6 `constant-arrival-rate` executor)는 Poisson arrival process가 아니라 고정 간격에 가까운
결정적(deterministic) 스케줄로 iteration을 시작시킨다. Poisson 분산 공식을 여기 적용할 통계적
근거가 없다.

**Unit 5 pilot 원시 데이터 부재**: 이 오차를 "정상 R3 pilot에서 관측된 실측 variation"으로
수치화하는 것이 원래 원칙이었으나, 저장소를 확인한 결과 Phase 2 Unit 5(open-model pilot)의 run별
`actual_started_rate`/`gateway_received_rate` 원시 페어(k6-summary.json/result.json)가
`docs/test-results/`에 남아있지 않다 — `docs/test-plan/phase2-formal-protocol.md` §6은 정성적 요약
(R3: reject/timeout 0)만 담고 있다. 존재하지 않는 pilot 원시 데이터를 근거로 수치를 만들어내지
않는다(`[[project-overview]]` 메모리의 "저장소에 실제로 있는 것만 사실로 취급" 원칙).

**결정**: 근거 있는 percentage threshold를 만들 수 없으므로, Linux Stability Canary의 hard gate는
**percentage 기반 rate-equality 조건을 쓰지 않는다** — §8의 개정된 통과 조건(dropped=0, timeout=0,
failure=0, unexpected_error=0, actual_started가 k6 target 정상 유지, counter plateau 없음, TTFC/
stream duration 정상, accounting 정상, clock/postflight 정상)만으로 환경 안정성을 판정한다.
`actual_started_rate`/`gateway_received_rate`의 차이는 계속 기록하되 **sanity/report metric**으로만
쓴다 — PASS/FAIL을 이 수치로 가르지 않는다.

이 항목에 수치 기반 허용 오차가 필요해지면(예: 18-run 결과 해석 단계에서), 그때는 **Linux에서 직접
확보한 clean run들의 실측 variation**을 근거로 다시 산정한다 — 사전에 임의로 만들지 않는다.

## 9. Formal 18-run 재실행 절차

Linux Canary 통과 시에만:

- P-E: R3 × 3, R6 × 3, R8 × 3
- VT-Limited: R3 × 3, R6 × 3, R8 × 3
- 총 18 valid runs를 **처음부터** 실행한다.

Mac에서 얻은 기존 Formal attempt(discarded 4건 + 이번 Canary)는 **어떤 run도 이 18개에 포함하지
않는다** — Linux 환경에서 새로 18개를 전부 확보한다.

## 10. Cross-environment 결과 원칙

Phase 2 Formal의 **primary conclusion**은 Linux 동일 환경 내에서의 P-E vs VT-Limited 비교만으로
도출한다.

Phase 1(Mac arm64, Java 8 executor matrix) 결과와의 cross-phase 비교는 **보조 분석(secondary)**으로만
사용한다. 특히 CPU, RSS, absolute latency 등 절대 resource 수치는 host/OS/architecture 차이가 있으면
"동일 환경 직접 비교"라고 표현하지 않는다. 사용 가능한 것은 동일한 k6 client 측 metric 정의와, 구조적
경향(예: "Executor 방식이 바뀌면 X 방향으로 변화한다" 같은 정성적 패턴) 비교뿐이며, 이 역시 secondary
분석으로 명시한다.

## 11. Mac 실패 결과 보존 — "Mac Formal Environment Rejected" history

아래는 삭제하지 않는다:

- 4건의 discarded Formal attempt(`docs/test-results/phase2/unit6/discarded/pe-r3-run{1,2}-attempt{1,2}-*`)
- 이번 Stability Canary(`pe-r3-canary-run1` 및 그 `stall_check.json`/로그/`result.json`)
- 관련 모든 로그, result.json, stall_check.json

이들은 모두 **"Mac Formal Environment Rejected"라는 experiment integrity history**로 보존한다(성능
결과 자체를 버린 것이 아니라 측정 환경을 탈락시킨 기록).

최종 보고서(Unit 6 결과 문서)에는 다음 취지를 명시한다:

> 성능 결과가 기대와 달라서 버린 것이 아니라, 측정 환경이 throughput population을 오염시키는 것을
> 독립 metric(로그 gap + Prometheus counter plateau)으로 확인해 Formal environment 자체를 탈락시켰다.

## 12. Measurement Lifecycle — Continuous Warm-up → Measurement (2026-08-17)

Linux host 준비에 착수하기 전, Linux Canary 설계 과정에서 **환경 문제와는 별개인 measurement
protocol 결함**을 하나 더 발견해 여기서 함께 고정한다 — 상세 메커니즘·코드는 `docs/test-plan/
phase2-formal-protocol.md` §3이 authoritative source이며, 이 절은 요약만 남긴다.

**발견한 문제**: 기존 harness(`scripts/run-phase2-formal-benchmark.sh`)는 warm-up과 measurement를
**별도의 두 k6 프로세스**로 실행했고, 그 사이에 `gateway_async_active_requests=0`까지 완전히
drain했다. 그 결과 measurement window가 **빈 시스템**에서 시작됐다 — mock workload의 전체 stream
길이(~7.9s) 때문에 window 시작 후 약 7.9초 동안은 정상적인 환경에서도 completion이 발생할 수 없어,
`measurement_window_completion_rate`가 target arrival rate보다 **환경 stall과 무관하게 구조적으로**
낮게 측정됐다(R3 기준 근사치 약 2.92 RPS, 즉 healthy 상태에서도 target 3.0 대비 약 -2.6%). Mac에서
관측된 -12~15% 편차 전체가 이 효과는 아니지만(대부분은 실제 stall), 이 metric을 "target과 거의
같아야 한다"는 식으로 Canary/Formal 판정에 그대로 쓰는 것은 근거가 부족했다.

**결정**: warm-up과 measurement를 **하나의 연속된 k6 프로세스**로 합치고, 그 사이의 drain을
제거했다 — `load-test-k6/scenarios/03-constant-arrival-rate-continuous.js`(신규 파일, 기존
`02-constant-arrival-rate.js`는 건드리지 않음 — Phase 1의 `scripts/run-formal-benchmark.sh`가 그
파일을 계속 쓰고 있고 Phase 1 결과는 이미 확정·발행됐으므로), `scripts/
run-phase2-formal-benchmark.sh` 갱신. Warm-up에서 넘어온 in-flight population이 measurement window
시작 시점부터 이미 steady state를 이루므로, 위 empty-start 편향이 근본적으로 제거된다.

**결과**: `measurement_window_completion_rate`는 이제 편향 없는 Primary throughput 지표로 계속 쓸 수
있다 — 별도의 "cohort_completion_rate를 Primary로 승격"하는 대체 metric을 도입할 필요가 없었다(가능한
대안이었으나, 근본 원인을 없애는 continuous 설계가 더 단순하고 harness 자체도 오히려 단순해졌다 —
warm-up 이후의 중간 drain-대기 루프가 통째로 사라짐).

**부수 효과**: k6 내장 `iterations` 메트릭은 warm-up/measurement 두 phase를 구분하지 않으므로, Formal
cohort 크기의 k6측 출처가 커스텀 Counter `measurement_iterations_started_total`로 바뀌었다
(`scripts/collect_phase2_formal_result.py` 갱신). `dropped_iterations`는 여전히 전체 run 집계이며
phase별로 분리되지 않는다 — PRE_ALLOCATED_VUS가 충분하면 두 phase 모두 0이라는 전제로 사용한다.

## 13. Unit 5.6 — Continuous Formal Harness Verification (2026-08-17)

Linux host 준비 전, §12에서 구현한 continuous 설계를 실제로 검증하기 위해 Mac에서 짧은 기능 smoke만
수행했다(P-E/R1/warmup=10s/measurement=20s, 결과는 `docs/test-results/phase2/
unit5.6-harness-verification/`에 보존 — Formal dataset에 미포함). 이 검증 과정에서 두 가지를
발견·수정했다:

**(a) Phase boundary 이중 계산 제거**: 기존에는 shell(`vm_epoch()` 기반 `RUN_START+WARMUP_SEC`)과
k6(`setup()`의 `Date.now()+WARMUP_SEC*1000`)가 **각자 독립적으로** 같은 경계를 계산했다 — 같은 VM
clock을 쓰므로 clock skew는 아니었지만, k6 컨테이너 자체의 기동 지연만큼 두 계산 시점이 어긋날 수
있었다. k6의 `setup()` 계산을 유일한 출처로 만들고(`measurement_start_epoch_s`라는 Gauge로 노출),
shell은 그 값을 `--summary-export` JSON에서 그대로 읽어오는 방식으로 바꿨다 — 이제 두 값은 근사가
아니라 **완전히 동일한 하나의 숫자**다(smoke 실측: 두 값 모두 `1786940090.974`로 정확히 일치).

**(b) Outcome cohort의 warm-up spillover 오염 발견·수정**: continuous 설계에서는 warm-up에 도착했지만
아직 진행 중이던 요청이 measurement 시작 이후에 완료될 수 있다. `outcome_cohort`의 Prometheus 측
windowed count(`[measurement_start, drain_end]`, 완료 시각 기준)는 이런 요청의 완료 이벤트까지 함께
세어버려 k6의 정확한 cohort 수(21)보다 크게 부풀려졌다(1차 smoke: `prometheus_completed_cohort=28`
vs `k6_completed=20`, diff=8 > tolerance=2, `cross_check_ok: false`). `gateway_async_active_requests`를
`measurement_start` 시점에 조회하면 이 잔류분을 정확히 알 수 있다는 점을 이용해
`scripts/collect_phase2_formal_result.py`에 보정(raw/corrected 값 모두 결과에 유지)을 추가했다 —
2차 smoke에서 `cross_check_ok: true`, `prometheus_invariant_ok: true` 확인. **Primary throughput
지표(`measurement_window_completion_rate`)는 이 문제의 영향을 받지 않았다** — `[measurement_start,
measurement_end]`(고정 길이) 윈도우에서는 warm-up 유입분과 measurement tail 유출분이 정확히
상쇄되기 때문(continuous 등속 도착의 수학적 성질이지 우연이 아니다 — 상세 계산은 세션 대화 로그
참고, 요약: 길이 W인 임의 위치의 윈도우에 들어오는 완료 건수는 그 window를 서비스 시간만큼 앞으로
민 arrival window의 도착 건수와 같고, 등속 도착이면 이 값은 위치와 무관하게 RATE×W다).

**결론**: 두 항목 모두 **Formal 18-run에 영향을 주기 전에** 발견했다 — Unit 5.6이 정확히 의도한
대로 작동했다. **(b)의 "exact correction"이라는 수정 방식 자체는 이후 §14에서 재검토·폐기됐다** —
아래 참고.

## 14. §13(b) 정정 — Prometheus "exact correction" 폐기, k6를 유일한 cohort source로 고정 (2026-08-18)

§13(b)가 도입한 `gateway_async_active_requests` 기반 보정(measurement_start 시점의 활성 요청 수를
Prometheus의 완료-시각-windowed outcome count에서 빼는 방식)을 **Formal authoritative correction으로
쓰지 않기로 정정한다.** 이유:

1. `gateway_async_active_requests`는 Prometheus가 scrape한 gauge 샘플이지, `measurement_start`
   정확한 순간의 애플리케이션 상태 그 자체가 아니다 — scrape_interval=5s 환경에서 경계와 샘플 시점
   사이에 오차가 있을 수 있다.
2. `measurement_start` 시점에 active였던 warm-up-origin 요청이 반드시 `completed`로 끝난다는 보장이
   없다 — timeout/upstream error/client disconnect 등 다른 terminal outcome도 가능한데, §13(b)의
   보정은 암묵적으로 "전부 completed로 끝난다"고 가정했다.

이 두 가지 이유로 §13(b)의 보정을 "exact"라고 부르는 것은 부적절하다.

**결정**: Formal request-outcome cohort의 **source of truth를 k6로 고정**한다 — Prometheus aggregate
counter로 "measurement에 시작된 request cohort"를 재구성하려는 시도 자체를 하지 않는다.

- **Client cohort accounting**(authoritative): k6의 `measurement_iterations_started_total`/
  `client_completed_total`/`client_rejected_total`/`client_failed_mid_stream_total`/
  `client_failed_no_event_total`(Unit 5.6이 phase gating을 실측 검증한 바로 그 metric들)만 사용한다.
  Invariant `measurement_iterations_started_total = completed + rejected + failed_mid_stream +
  failed_no_event`를 drain 후 정확히 확인한다(구성상 항상 성립 — mutually exclusive/exhaustive한
  outcome 분기이기 때문).
- **Server sanity**(diagnostic, non-authoritative): Prometheus는 (A) `measurement_window_completion_rate`
  = `[measurement_start, measurement_end]` 구간의 gateway completed outcome 증가량/measurement
  duration(§6에서 이미 이 지표는 empty-start bias의 영향을 받지 않음을 확인) — Primary throughput으로
  계속 유지, (B) server/JVM resource(platform threads, executor/VT active, CPU, RSS, Heap, GC, mock
  concurrency/waiting), (C) `gateway_request_outcome_total`의 `[measurement_start, drain_end]` windowed
  raw count(timeout_before_start/deadline_exceeded/upstream_error/client_disconnect/unexpected_error 등)
  — 에만 authoritative하게 사용한다. (C)는 이상 징후(plateau, unexpected_error 급증, timeout/failure
  burst)를 눈으로 확인하는 용도이며, k6 cohort와 숫자가 정확히 같아야 한다고 강제하지 않는다 — 두
  population은 시간 기준이 다를 수 있기 때문이다(continuous lifecycle에서 warm-up 잔류분이 이 windowed
  count에 섞여 들어갈 수 있음, §13(b)에서 발견).

`scripts/collect_phase2_formal_result.py`의 `warmup_spillover_at_boundary`/
`prometheus_completed_cohort_corrected`/`prometheus_outcome_sum_corrected`/`cross_check_ok`/
`prometheus_invariant_ok` 필드는 모두 **제거**했다(diagnostic으로 남기지 않고 단순화 우선 — 결과
schema는 `client_cohort`/`server_diagnostic` 두 블록으로 재구성됐다). `measurement_window_completion_rate`
표현도 함께 다듬었다: "warm-up spillover와 measurement tail이 정확히 상쇄된다"는 매 run 보장이
아니라, **"충분한 warm-up 후 steady state에서 measurement window 시작 시 이미 안정적인 in-flight
population이 존재하므로 기존 empty-start boundary bias가 제거된다 — 따라서 고정 measurement window의
completion rate를 steady-state throughput으로 해석한다"**는 것이 정확한 근거다(§13에서 든 수학적
상쇄 논증은 "그 smoke run에서 관측된 사실"의 설명으로만 유효하며, 매 run 정확히 상쇄된다는
보장으로 일반화하지 않는다).

Unit 5.6 smoke의 재해석: `iterations(all)=31`/`measurement_iterations=21`/`client_completed=21`은
phase gating이 실제로 동작한다는 근거로 계속 유효하다. `Prometheus completed=28` → 보정 후 `20~23`
근사치로 좁혀졌던 결과는, "aggregate Prometheus counter만으로 cohort를 재구성하는 것이 왜 어려운지"를
발견한 계기로만 문서화하며, 새 보정 알고리즘이 정확하다는 증명으로 사용하지 않는다.

이 정정은 코드/기존 smoke 재실행 없이 이뤄졌다 — k6 측 phase gating(Unit 5.6이 검증한 부분)은
바뀌지 않았고, 바뀐 것은 `collect_phase2_formal_result.py`가 Prometheus 값을 어떻게 "쓰는지"뿐이다.

## 지금 이 ADR이 확정하는 것 / 확정하지 않는 것

**확정**: 위 1~14항의 원칙과 절차. Mac에서 Phase 2 Formal을 더 이상 시도하지 않는다는 결정. §8-1
(rate 허용 오차)의 percentage-threshold 방식은 폐기하고 hard gate 방식으로 대체 확정. §13의 harness
검증(phase gating/continuous lifecycle/boundary single source)은 PASS로 유지되며, §13(b)의 correction
알고리즘만 §14에서 폐기·대체됐다 — Linux host 준비 단계로 진행 가능하다는 것이 이 ADR의 판단이다.

**확정하지 않음(실행 전 단계)**: 실제 Linux host의 구성/기동, Linux Stability Canary 실행, 18-run
Formal Matrix 실행. 이들은 사용자 승인과 Linux host 준비 이후의 후속 작업이다.
