# ADR: Phase 4 OS/Control Resource Safety Policy

Status: Accepted (Phase 4 Unit 1 scope — policy freeze only; no OS values changed, no load
executed)
Date: 2026-08-25

## 1. OS Snapshot (Unit 0, read-only, 변경 없음)

`docs/test-results/phase4/unit0-runtime-spike/os-resource-snapshot.txt` 원본 실측값:

```
ulimit -n (soft)        = 1,048,576
ulimit -Hn (hard)        = unlimited
kern.maxfiles             = 122,880
kern.maxfilesperproc      = 61,440
kern.num_files (당시 시스템 전체 open count) = 5,463
ephemeral port range      = 49152–65535 (16,383개)
hw.physicalcpu / logicalcpu = 10 / 10
hw.memsize                = 17,179,869,184 bytes (16 GB)
swap                      = 0.00M used / 0.00M free (당시 스냅샷)
Docker Desktop            = 미기동
```

**Unit 1에서 이 값들을 변경하지 않는다.** ulimit/kern.* 변경은 필요성과 안전성을 Unit 3에서 검토한
뒤 사용자 승인을 받아야만 진행한다(설계 브리프 §12 원칙 계승).

## 2. Phase 4 특유의 FD 압력 구조

한 SSE stream은 최소 두 개의 socket 관계를 만든다: **LoadGen → Gateway**, **Gateway → Mock**.
따라서 높은 N에서는 FD/ephemeral port가 architecture 자체보다 먼저 ceiling이 될 수 있다 — 이
가능성을 Environment-limited result(§6)로 분류할 준비를 미리 한다.

### 2-1. FD Denominator — Unit 3에서 실측 확인 필요

실제 active limiter가 다음 중 무엇인지 아직 확정하지 않는다:

- per-process soft limit(`ulimit -n` = 1,048,576, 매우 큼)
- system-wide `kern.maxfiles`(122,880)
- per-process `kern.maxfilesperproc`(61,440)

Gateway 프로세스 하나만 놓고 보면 `kern.maxfilesperproc`(61,440)가 이 host의 실질적 상한일
가능성이 높지만(soft ulimit이 이보다 크므로), Mock LLM/Prometheus/k6가 동시에 FD를 점유하는
system-wide 합계가 `kern.maxfiles`(122,880)에 먼저 도달할 가능성도 배제하지 않는다. **Unit
3에서 실측으로 확인한다.**

## 3. Hard Safety Stop Conditions — freeze

다음 중 하나가 관측되면 escalation(더 높은 concurrency/rate로 진행)을 즉시 중단한다:

- FD usage >= **실제 active limiter의 85%**(§2-1에서 확정된 denominator 기준)
- host available/free memory가 안전 margin 이하, 또는 memory pressure critical(정확한 macOS
  metric/threshold는 Unit 3 read-only calibration 후 freeze — 이번 Unit에서 숫자 미확정)
- `EADDRNOTAVAIL`(ephemeral port 고갈)
- `EMFILE`(FD 고갈)
- system responsiveness의 심각한 저하
- Gateway/Mock/LoadGen crash
- OOM
- unexpected error rate >= 1%
- timeout burst
- postflight leak(§Formal invariant, `phase4-metrics-contract.md` §14)
- environment stall(log gap 등)
- control headroom violation(§4)

**CPU 100%는 자동 hard-stop이 아니다** — CPU saturation 자체가 실제 model boundary일 수 있으므로
관찰/분류 대상이지 무조건 중단 사유가 아니다(설계 브리프 §19 원칙 계승).

## 4. Direct Mock Control Headroom — freeze: **최소 1.25×**

Unit 3에서 반드시 수행하는 calibration 요구사항:

- **Closed**: Gateway 최대 target concurrency보다 Mock+LoadGen direct path(Gateway를 거치지
  않고 LoadGen이 Mock을 직접 호출)가 최소 **1.25배** headroom을 가져야 한다. 예: Gateway를 4000
  concurrent까지 주장하려면 direct Mock/LoadGen이 최소 약 5000 concurrent를 정상 유지할 수
  있어야 한다.
- **Open**: Gateway 최대 target arrival rate보다 direct Mock control이 최소 **1.25배**
  headroom을 가져야 한다.

이 조건을 만족하지 못하면 **Gateway boundary claim의 범위를 낮춘다** — Mock 코드를 자동으로
최적화/변경하지 않는다. 필요하면 (a) test range를 축소하거나 (b) Phase 4 전용 scalable
downstream simulator를 별도 설계한 뒤 다시 control validation을 수행한다(사용자 승인 필요,
설계 브리프 §10과 동일 원칙).

이 1.25× 비율은 M3의 `WebClient.ConnectionProvider` headroom 정책(`docs/test-plan/
phase4-design.md` §7-1)과 동일한 비율을 채택한 것이다 — Phase 4 전체에서 "headroom"이라는 개념을
하나의 숫자로 통일한다.

## 5. Load Generator Control Requirement

k6/xk6-sse도 실험 대상이 아니다. Unit 3에서 k6/xk6-sse가 planned range에서 valid load를 만들 수
있는지 검증한다. 다음은 **Gateway result가 아니다** — `LOADGEN_LIMIT`으로 분류한다:

- `dropped_iterations`
- k6 자체의 CPU/resource saturation
- k6 process error
- client-side ephemeral-port exhaustion

k6가 target load를 만들지 못하면 처음부터 새 tool을 만들지 않고, 필요한 경우에만 lightweight Go
SSE load generator 같은 fallback을 Unit 3에서 검토한다(설계 브리프 §11 원칙 계승).

## 6. Environment Ceiling Classification — Model RED와의 구분

`docs/decisions/phase4-scalability-definition.md` §7(Green/Amber/Red)이 참조하는 원칙: OS/제어
환경이 원인인 실패는 **"Model RED"라고 주장하지 않는다.**

| 관측된 원인 | 분류 | Model boundary claim에 포함 여부 |
|---|---|---|
| `AbortPolicy` rejection(M1), timeout, application-level SLO 실패 | `MODEL_REJECTION`/`MODEL_TIMEOUT`/`MODEL_SATURATION` | 포함 — 이것이 Phase 4가 찾으려는 것 |
| FD 고갈(`EMFILE`), ephemeral port 고갈(`EADDRNOTAVAIL`) | `OS_FD_LIMIT`/`OS_PORT_LIMIT` | **제외** — Environment-limited result로 별도 기록, model 간 비교에서 "이 model이 여기서 죽었다"고 claim하지 않는다 |
| host memory pressure critical | `HOST_MEMORY_LIMIT` | **제외** |
| k6/xk6-sse 자체 실패 | `LOADGEN_LIMIT` | **제외** |
| Mock LLM이 direct calibration 상한보다 먼저 도달 | `DOWNSTREAM_LIMIT` | **제외** — §4 headroom 위반이 이미 감지했어야 하는 상황 |
| 원인 불명 | `UNKNOWN` | 증거 없이 어느 쪽으로도 claim하지 않음 |

## 7. Memory Safety Rule

Mac 전체를 OOM까지 밀지 않는다. Unit 3에서 host memory monitor를 구현한다. Hard stop 후보:
available/free memory가 안전 margin 이하, 또는 memory pressure critical — **정확한 macOS
metric/threshold는 Unit 3 read-only calibration 후 freeze한다**(이번 Unit에서는 숫자를 정하지
않음, §3과 동일). 결과를 얻기 위해 swap을 강제로 만들거나 OS를 불안정하게 만들지 않는다.

## 8. Docker Desktop 정책 (계승)

Phase 2/3 native Formal 원칙을 Phase 4도 계승한다: Formal Benchmark 동안 Docker Desktop이
기동되어 있으면 안 된다(Unit 0 스냅샷 시점에는 미기동 확인됨). Preflight에서 Docker Desktop
프로세스가 감지되면 FATAL — 스크립트가 자동으로 종료시키지 않고 운영자에게 수동 종료를 요청한다
(`docs/decisions/phase2-formal-native-macos-environment.md` §10과 동일 원칙, Unit 3/4 harness
구현 시 반영).

## 9. Unit 1에서 확정하지 않는 것 (Unit 3 dependency)

다음은 Unit 3 calibration 없이는 정할 수 없는 구조적 dependency이므로 이번 Unit에서 freeze하지
않는다:

- FD safety threshold의 실제 denominator(§2-1)
- host memory pressure critical의 정확한 macOS metric/threshold(§7)
- Phase 4 maximum concurrency/arrival rate 자체
- `WebClient.maxConnections` 정확한 숫자(`phase4-design.md` §7-1)
- FD/connection sampling frequency(§metrics-contract.md §10)
