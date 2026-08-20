# ADR: Phase 2 Formal Benchmark — Native macOS ARM64 (Docker-free) Environment

Status: Accepted — Native Stability Canary PASSED 2026-08-19, adopted as Phase 2 Primary Formal
environment, 18-run Formal Matrix approved and in progress (see §12)
Date: 2026-08-18

## 문제

`docs/decisions/phase2-formal-linux-environment.md`가 Mac + Docker Desktop 환경을 Formal Benchmark
환경에서 rejected 처리한 뒤, Linux 이전 대안으로 AWS EC2(c8g.2xlarge, ap-northeast-2)가 검토됐다.
사용자는 이후 **유료 Cloud 환경(AWS/GCP 등)을 사용하지 않기로** 결정했다 — 그러나 이것이 Phase 2
Formal 자체를 Deferred로 종료한다는 뜻은 아니다. **Mac + Docker Desktop 환경이 rejected된 것이지,
macOS native execution 자체가 rejected된 것은 아니다.**

이 문서는 마지막 대안 — **Docker Desktop을 완전히 제거한 native macOS ARM64 Formal Benchmark**의
설계와 그 실행을 위한 별도 harness를 기록한다.

## 1. Feasibility 결론 (검토 결과 요약)

실행 전 설계 검토(2026-08-18)에서 다음을 실측으로 확인했다 — 추측이 아니라 각 항목을 직접
다운로드/체크섬 검증/빌드/실행해 확인했다(§5 참고):

| 대상 | 결론 | 근거 |
|---|---|---|
| Gateway native 실행 | 가능 | `EnvUtil.getString/getInt`로 OS 환경변수만 읽음, Docker 의존 없음. `java -jar app.jar`로 정상 기동·`/healthz` 응답 확인 |
| Mock LLM native venv | 가능 | Python 3.12.14 + `fastapi==0.141.1`/`uvicorn==0.52.1`/`prometheus_client==0.26.0`/`pydantic==2.13.4` 정확히 일치하는 venv 재구축 완료 |
| Prometheus native binary | 가능 | `prometheus-3.13.2.darwin-arm64.tar.gz` 공식 배포, 체크섬 일치 확인 |
| k6 v1.8.0 + xk6-sse v0.1.11 native 빌드 | 가능 | `xk6 build v1.8.0 --with github.com/phymbert/xk6-sse@v0.1.11`로 darwin/arm64 네이티브 빌드 성공, `k6/x/sse` import 실제 SSE 요청으로 동작 확인(§5) |
| Docker network → localhost | 최소 수정으로 가능 | 포트 충돌 없음(8080/8000/prometheus 전용 포트), `GATEWAY_URL`/`MOCK_LLM_BASE_URL`이 이미 파라미터화됨 |
| CPU/JVM thread/Heap/GC 측정 | 영향 없음 | `io.prometheus:simpleclient_hotspot`가 `ThreadMXBean`/`MemoryMXBean`/`OperatingSystemMXBean` 기반으로 노출 — Docker/cgroup과 무관한 JVM 자체 지표 |
| RSS(Process resident memory) 측정 | **정정(2026-08-19)**: 기존 판단이 부정확했음 — 별도 macOS sampler 필요 | §1-1 참고 |

### 1-1. RSS 측정 정정 (2026-08-19)

Functional Smoke 실행 중 `rss_measurement_start_bytes`/`rss_peak_bytes`/`rss_delta_bytes`가 매 run
0으로 나오는 것을 발견했다. 실측(임시 Gateway 프로세스 기동 후 `/metrics` 직접 curl)으로 원인을
확인했다 — `process_resident_memory_bytes`/`process_virtual_memory_bytes`는 Prometheus Java client의
`StandardExports`가 **Linux `/proc/self/status` 기반으로만** 노출하는 지표로, macOS에서는 애초에
노출되지 않는다(`process_cpu_seconds_total`, JVM thread/heap/GC 지표는 정상 노출 — 위 §1 표의 판단은
이 항목들에 한해서만 유효했다).

**결정**: RSS를 "미측정"으로 남기지 않는다 — Phase 2의 핵심 비교 항목에 메모리 비용이 포함되므로,
macOS native 전용 RSS sampler를 추가한다:

- **방식**: `run-phase2-native-formal-benchmark.sh`가 Gateway PID를 대상으로 `ps -o rss= -p <pid>`를
  주기적으로 호출(외부 패키지/coreutils 설치 없음, macOS 기본 `ps`만 사용).
- **시점**: Gateway healthy 확인 직후, k6(warm-up) 시작 전부터 샘플링을 시작해 drain 완료 확인 직후까지
  계속한다 — warm-up → measurement → drain 전 구간을 raw CSV(`native-rss-samples.csv`,
  `timestamp_epoch_s,rss_kib`)로 남긴다.
- **interval**: **1초 고정**, P-E/VT-Limited 모든 native run에 동일하게 적용한다. Gateway PID 하나에
  대한 `ps` 호출뿐이라 overhead는 작지만, Formal 전체에서 동일 구현/동일 interval을 쓴다는 점을
  명시적으로 고정한다.
- **집계**: k6 종료 후 이미 authoritative하게 확보된 `measurement_start`/`measurement_end`(§ 기존
  단일 source of truth 메커니즘, 변경 없음)를 이용해 raw CSV를 사후 필터링한다 — sampler 자체는
  measurement window를 알거나 계산하지 않는다.
  - `rss_measurement_start_bytes` = `measurement_start` 이후 가장 가까운 첫 sample
  - `rss_measurement_peak_bytes` = `[measurement_start, measurement_end]` 구간 내 최대값
  - `rss_measurement_delta_bytes` = peak − start
  - KiB(ps 원 단위) → bytes로 명시 변환해 저장한다.
- **schema**: 기존 `collect_phase2_formal_result.py`(Docker harness와 공유, frozen)는 무수정 — 그
  출력에 native harness가 별도 `native_resource` 블록(`rss_source`, `sampling_interval_seconds`,
  `raw_samples_file`, `rss_measurement_start/peak/delta_bytes` 등)을 merge하는 방식으로 추가했다.
  기존 Prometheus 기반 resource 필드(CPU/heap/GC/thread)와 role을 혼동하지 않도록 blocks를 분리했다.
  Docker harness/`collect_phase2_formal_result.py`의 공통 schema, R3/R6/R8, warm-up/measurement 정책은
  전혀 건드리지 않았다.
- **Aggregation 정책(2026-08-19 확정, 현재는 정책만 — 아직 Native aggregate/report 스크립트 자체가
  존재하지 않음, Docker Phase 2 쪽도 마찬가지로 없음, Phase 1의 `docs/test-results/phase1/unit6/
  _aggregated.json`만 존재)**: 향후 Native 18-run aggregation/report를 만들 때는 반드시
  `native_resource.rss_measurement_start_bytes` / `native_resource.rss_measurement_peak_bytes` /
  `native_resource.rss_measurement_delta_bytes`를 RSS의 authoritative 출처로 사용한다.
  `result.json` 최상위의 `rss_measurement_start_bytes`/`rss_peak_bytes`/`rss_delta_bytes`(Docker
  schema에서 상속된 필드, `collect_phase2_formal_result.py`가 채우려 시도하지만 native에서는
  `process_resident_memory_bytes`가 애초에 노출되지 않아 항상 0)를 native RSS 값으로 잘못 집계하지
  않는다.
- **lifecycle**: sampler PID(`RSS_SAMPLER_PID`)를 다른 프로세스와 동일하게 관리한다 — drain 확인
  직후 명시적으로 종료시키고(SIGTERM → 최대 5초 대기 → SIGKILL), postflight에서 살아있으면
  `rss_sampler_still_alive`로 postflight_fail에 기록한다. 스크립트가 어떤 경로로 종료되든(성공/실패/
  중단) 기존 `cleanup()` trap의 PID 목록에도 포함돼 있어 이중으로 보장된다.

2026-08-19 Functional Smoke 재검증(`docs/test-results/phase2/unit6-native/pe-smoke-rss-r1-run1`,
Formal dataset 미포함)에서 정상 동작 확인: 46개 raw sample(1Hz × 약 46초 구간), measurement window
내 20개 sample, `rss_measurement_start_bytes≈201.4MB`, `rss_measurement_peak_bytes≈206.6MB`,
`rss_measurement_delta_bytes≈5.2MB`, postflight 100% clean(sampler 포함 전부 정상 종료).

## 2. CPU 정책 — `jdk.virtualThreadScheduler.parallelism` 사용 금지 (2026-08-18 확정)

**이전 초안에서 제안했던 `-Djdk.virtualThreadScheduler.parallelism=1`(Docker `cpus=1.0`을 흉내내려는
시도)은 채택하지 않는다.** 이유: Docker `cpus=1.0`은 JVM 프로세스 전체가 관찰하는 CPU 자원(스레드
스케줄링 전체)에 영향을 주는 환경 조건이었지만, `jdk.virtualThreadScheduler.parallelism`은 Virtual
Thread carrier pool의 병렬성 **하나만** 제한한다. 이를 native에서 VT-Limited에만 걸면(P-E는 이
플래그의 영향을 받지 않음 — ThreadPoolExecutor는 `availableProcessors()`를 쓰지 않음, §5) **VT-Limited만
인위적으로 자원 제약을 받는 새로운 confound**가 생긴다.

**결정**: Native Formal의 비교 조건은 "Docker의 cpus=1.0을 재현하는 것"이 아니라 아래로 새로 정의한다:

- 동일 native macOS host
- 동일 JDK / Spring / HttpURLConnection / FastAPI / workload
- 동일 admission ceiling(=50, `CHAT_VT_LIMITED_PERMITS`)
- **동일 host CPU access** — P-E는 OS scheduler에 맡기고, VT-Limited도
  `jdk.virtualThreadScheduler.parallelism` 기본값(=`Runtime.availableProcessors()`)을 그대로 쓴다.
  둘 다 별도의 CPU affinity/parallelism 제한을 걸지 않는다.

`Runtime.availableProcessors()`(이 dev host 실측: **10**)를 매 run의 `environment.json`에 기록한다.

CPU 사용량 자체의 위험도(§3 이하)는 기존 R3 Mac Docker discarded run 5건의 실측(`cpu_avg_cores≈
0.02~0.03`, `cpu_peak_cores≈0.04`, 1.0 CPU 한도에 전혀 근접하지 않음 — workload가 고정 delay 기반
I/O-bound이기 때문)을 근거로 R3에서는 낮다고 판단하지만, **R6/R8(near/over-capacity)에서는 아직
실측된 바 없다** — Native Canary(R3) 이후 R6/R8 실행 시 CPU 사용량을 반드시 재확인한다.

## 3. Docker Formal과 Native Formal은 서로 다른 environment (2026-08-18 확정)

Native Formal은 기존 Docker `cpus=1.0`/`mem_limit=1g` 환경을 재현하는 실험이 **아니다** — 별도의
Formal environment다. 따라서:

- Docker attempt(discarded, 전부 Formal dataset에서 제외됨)와 Native Formal의 **absolute CPU, RSS,
  latency를 직접 비교하지 않는다.**
- Primary comparison은 오직 **동일 Native environment 내부의 P-E vs VT-Limited**다.
- 이는 `docs/decisions/phase2-formal-linux-environment.md` §10(Phase 1 Mac vs Phase 2 Linux 절대
  수치 비교 금지)과 동일한 원칙을 native에도 그대로 적용한 것이다 — OS/실행 방식이 다르면 절대
  수치는 secondary로만 쓴다.

## 4. Heap 정책 — 명시적 `-Xmx` 없음, 양쪽 동일 default ergonomics (2026-08-18 확정)

**확인**: `gateway-mvc-java21/Dockerfile`, `build.gradle`, `application.yml`, `docker-compose.yml`
어디에도 `-Xmx`/`-Xms`/`JAVA_OPTS`/`JAVA_TOOL_OPTIONS`/`MaxRAMPercentage` 등 heap을 명시적으로
고정하는 설정이 **없다** — Docker에서도 JVM 기본 ergonomics(container-aware, `mem_limit=1g`의 1/4 ≈
256MB)에 맡겨져 있었다. 즉 "기존 Docker run의 실제 MaxHeapSize"로 참고할 만한 **명시적 근거 값이
존재하지 않는다**(container-aware ergonomics의 emergent 결과였을 뿐, 의도적으로 결정된 숫자가 아님).

**결정**: 임의의 `-Xmx` 값을 새로 만들지 않는다. **P-E와 VT-Limited 양쪽 모두 명시적 heap 플래그 없이
JDK 기본 ergonomics를 그대로 사용**한다. 이 host는 물리 메모리가 고정(16GB)이므로, 두 config 모두
**항상 동일한 ergonomic 값**으로 귀결된다(host RAM 기반 산정이지 run별로 달라지는 값이 아니므로,
"양쪽에 완전히 동일한 heap option을 적용"하는 목표가 별도 조정 없이 자동으로 만족된다).

**실측(이 dev host, pinned JDK 21.0.11+10)**:
```
InitialHeapSize = 268435456   (256 MB, ergonomic)
MaxHeapSize      = 4294967296  (4 GB, ergonomic — host 16GB RAM의 1/4)
```
(`java -XshowSettings:vm -version`으로도 "Max. Heap Size (Estimated): 4.00G"로 교차 확인.)

매 run마다 `environment.json.resource_policy.heap_ergonomics`에 위 값을 실제 조회해 기록한다
(`run-phase2-native-formal-benchmark.sh`가 `-XX:+PrintFlagsFinal`로 매 run 확인).

## 5. Native Dependency Validation — 실측 기록 (2026-08-18)

| 항목 | 값 | 검증 방법 |
|---|---|---|
| JDK | Eclipse Temurin 21.0.11+10-LTS, macOS aarch64 | Adoptium API에서 다운로드, artifact `OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.11_10.tar.gz`, SHA256 `6ebcf221c9b41507b14c098e93c6ead6440b8d9bd154f8ec666c4c73abbdb201` 일치 확인. `java -version` → `Temurin-21.0.11+10 (build 21.0.11+10-LTS)`, Mach-O arm64 실행파일 확인 |
| Prometheus | 3.13.2, darwin-arm64 | GitHub Release `prometheus-3.13.2.darwin-arm64.tar.gz`, SHA256 `f68ca4f1dbedd6366bbfdd8ac5d2c0b7ba1f273474acc8d38eb33202fbeec7a4` 일치 확인. `prometheus --version` → `3.13.2 ... platform: darwin/arm64` |
| k6 | v1.8.0 + xk6-sse v0.1.11, darwin/arm64 custom build | `xk6 build v1.8.0 --with github.com/phymbert/xk6-sse@v0.1.11` 성공(`Successful build platform=darwin/arm64`). `k6 version` → `k6 v1.8.0 (go1.26.6, darwin/arm64)` + `Extensions: github.com/phymbert/xk6-sse v0.1.11, k6/x/sse [js]`. **`k6/x/sse` import 실제 동작 확인**: native Gateway+Mock 대상으로 실제 SSE 요청 실행, `client_completed_total=4`/`client_stream_duration_seconds avg=7.91s`/`client_ttfc_completed_seconds avg=1.01s` 정상 관측(mock workload의 기대값 1s/7.9s와 일치) |
| Mock LLM | Python 3.12.14, fastapi==0.141.1, uvicorn==0.52.1, prometheus_client==0.26.0, pydantic==2.13.4 | `python3.12 -m venv` 재생성, `pip install -r requirements.txt` 후 `pip freeze`로 4개 pinned 패키지 정확히 일치 확인(`.native-runtime/mock-llm-pip-freeze.txt`에 저장) |
| Runtime.availableProcessors() | **10**(이 dev host) | 컴파일된 helper class로 실측, P-E/VT-Limited 양쪽 동일(§2, 별도 제한 없음이므로 자동으로 동일) |

이 검증에 쓴 Gateway/Mock LLM/Prometheus 프로세스는 **dependency validation 전용**이며 정상 종료했다
— 어떤 Canary/Formal 결과에도 포함되지 않는다.

빌드/다운로드 도구(Go 1.26.6, Python 3.12.14)는 Homebrew로 설치했다(`brew install go python@3.12`).
런타임 산출물(JDK/Prometheus/k6 바이너리, Go 모듈 캐시, 약 1.3GB)은 `.native-runtime/`에 저장되며
**git에 커밋하지 않는다**(`.gitignore` 추가) — `scripts/setup-phase2-native-runtime.sh`로 언제든
재생성 가능하고, 체크섬이 이 문서에 기록돼 있으므로 바이너리 자체를 저장소에 둘 필요가 없다.

## 6. Native Harness — Docker harness와 완전히 분리

**기존 Docker harness는 이 작업으로 전혀 수정하지 않았다**(freeze 유지):
`scripts/run-phase2-formal-benchmark.sh`, `scripts/run-phase2-formal-matrix.sh`,
`monitoring/prometheus/prometheus.yml`, `docker-compose.yml`.

**신규 native 전용 파일**:
- `scripts/setup-phase2-native-runtime.sh` — JDK/Prometheus/k6/mock venv를 다운로드·체크섬 검증·
  빌드(idempotent).
- `scripts/run-phase2-native-formal-benchmark.sh` — 단일 run 실행(native preflight/fresh
  process/continuous k6/drain/postflight/stall_check/collect).
- `scripts/run-phase2-native-formal-matrix.sh` — 18-run 배치 드라이버(Docker 버전과 동일한 고정 순서).
- `monitoring/prometheus/prometheus-native.yml` — job name(`gateway-mvc-java21`/`mock-llm-fastapi`)은
  Docker 버전과 **동일하게 유지**(target만 `localhost:8080`/`localhost:8000`로 변경) — job name이
  바뀌면 `collect_phase2_formal_result.py`가 깨지기 때문.
- 결과물 경로: `docs/test-results/phase2/unit6-native/`(Docker 버전의 `unit6/`와 분리).

**그대로 재사용(무수정)**:
- `load-test-k6/scenarios/03-constant-arrival-rate-continuous.js`
- `scripts/collect_phase2_formal_result.py`(Prometheus URL/포트만 파라미터로 다르게 전달, 코드 무수정)
- R3/R6/R8, warm-up 120s, measurement 300s
- metric schema, `client_cohort` semantics, `measurement_window_completion_rate` semantics
  (`docs/decisions/phase2-formal-linux-environment.md` §12~§14)

## 7. Native Service Layout

Docker network/service DNS 이름을 쓰지 않는다 — 전부 localhost:

- k6 → `http://localhost:8080/chat/stream` (Gateway)
- Gateway → `http://localhost:8000/mock/stream` (Mock LLM, `MOCK_LLM_BASE_URL`)
- Prometheus → scrape `localhost:8080`(gateway-mvc-java21), `localhost:8000`(mock-llm-fastapi)
- Prometheus 자체 리스닝 포트: **9092**(Docker 버전이 쓰는 9091과 충돌 방지 — Docker Desktop이
  떠 있는 동안에도 포트 레벨 충돌이 없도록 명확히 분리)

## 8. Fresh process per run

매 run마다 반드시:
1. fresh Prometheus TSDB 디렉터리 생성(`mktemp -d`) — Docker의 `--renew-anon-volumes`와 동일한
   "이전 run 데이터 잔존 없음" 보장.
2. fresh Mock LLM 프로세스 시작.
3. fresh Gateway JVM 시작(명시적 heap/parallelism 플래그 없음, §2·§4).
4. health check(`/healthz` polling).
5. preflight(mode-aware idle 확인).
6. k6 continuous warm-up → measurement(단일 프로세스, drain 없음).
7. drain(active=0 polling).
8. result collect(`collect_phase2_formal_result.py`).
9. 세 프로세스 전부 종료.

이전 run의 JVM/Python/Prometheus 프로세스를 재사용하지 않는다 — `run-phase2-native-formal-benchmark.sh`
의 `cleanup()`이 `trap ... EXIT`로 등록돼 스크립트가 어떻게 종료되든(성공/실패/중단) 항상 실행된다.

## 9. PID lifecycle

`gateway.pid`/`mock.pid`/`prometheus.pid`를 각 run의 결과 디렉터리에 기록한다. `cleanup()`이 각
PID를 SIGTERM으로 종료 시도 후 최대 10초 대기, 그래도 살아있으면 SIGKILL한다. 다음 run 시작 전
preflight가 8080/8000/9092 포트가 비어 있는지, 이전 run의 PID 파일이 아직 살아있는 프로세스를
가리키는지 확인하고, 하나라도 걸리면 FATAL로 중단한다(자동으로 죽이지 않음 — 원인 파악이 우선).

## 10. Native Preflight

`run-phase2-native-formal-benchmark.sh`가 매 run 시작 전 확인·기록하는 것:

- Docker Desktop 프로세스가 완전히 종료돼 있음(`Docker Desktop`/`com.docker.backend` 프로세스
  검색) — **하나라도 감지되면 FATAL, 스크립트가 직접 종료시키지 않고 운영자에게 수동 종료를
  요청한다**(Docker Desktop을 자동으로 quit하는 것은 이 세션에서 사용자가 명시적으로 거부한 액션 —
  GUI 앱 종료는 운영자가 직접 한다).
- 무관한 heavy workload — 프로그램적으로 완벽히 판별하기 어려운 항목이라, top CPU 프로세스를 로그로
  남기는 advisory 수준으로만 다룬다(hard gate 아님, 운영자 책임).
- `caffeinate -dims -w $$`를 스크립트 자신이 기동해 스스로 sleep을 방지한다(운영자가 별도로 감싸지
  않아도 보장됨, 스크립트 종료 시 자동 해제).
- power source(`pmset -g batt`) 기록.
- `Runtime.availableProcessors()` 기록.
- JDK/k6/Mock 버전 기록(§5 값과 일치하는지).
- 포트 8080/8000/9092 clean 확인.
- stale PID 없음 확인.
- clock 확인(`sntp -sS time.apple.com`, best-effort — native에는 Docker VM처럼 비교할 두 번째 clock
  domain이 없으므로 host clock 자체의 NTP 동기화 여부만 기록, 네트워크가 막히면 "unavailable"로
  기록하고 계속 진행— 이 항목만으로 run을 막지 않는다).

## 11. Native resource measurement

**정정(2026-08-19, §1-1과 동일 내용 요약)**: 기존 JVM/Prometheus 지표(`jvm_threads_current`, Heap, GC,
`process_cpu_seconds_total`)는 그대로 재사용한다(§1에서 확인한 대로 Docker/cgroup과 무관) —
`platform_threads_window_peak`, CPU avg/peak, Heap start/peak/delta, GC, executor active/VT active는
기존 protocol과 완전히 동일하게 수집한다(`collect_phase2_formal_result.py` 무수정 재사용, 이 지표들의
authoritative 출처는 계속 Prometheus다).

**RSS만 예외**: `process_resident_memory_bytes`는 macOS에서 Prometheus Java client가 노출하지 않는다
(§1-1). 그래서 RSS만 macOS `ps -o rss=` 기반 native sampler(§1-1)가 authoritative 출처이며, 결과의
`native_resource` 블록(`rss_measurement_start/peak/delta_bytes`)에 담긴다 — 기존 `result.json` 최상위의
`rss_measurement_start_bytes` 등(Docker harness 스키마에서 상속된 필드, `collect_phase2_formal_result.py`가
채우려 시도하지만 native에서는 항상 0)과 혼동하지 않는다. macOS `top`은 여전히 보조 diagnostic으로만
쓴다(preflight의 "무관한 workload" 확인 등).

## 12. 현재 상태

**정정(2026-08-19)**: Functional Smoke PASS(§1-1의 RSS 검증 포함), **P-E R3 Native Stability Canary
PASS**(120s warm-up + 300s measurement, `docs/test-results/phase2/unit6-native/pe-r3-canary-run1`,
Formal 18-run에서 제외) — client_cohort 901/901 completed·0 rejection/failure,
`measurement_window_completion_rate=3.0`(target 3.0과 정확히 일치), TTFC p95≈1.007s, stream duration
p95≈7.855s, counter plateau 없음(run 자체의 leftover TSDB에 read-only Prometheus를 재기동해
`gateway_request_received_total`/`mockllm_completed_requests_total`을 직접 `query_range` 재확인 —
warm-up 시작~drain_end 전 구간에서 최장 "동일값 유지" 구간이 5초/2 샘플뿐, Mac Docker Desktop
사례의 30~35초 plateau와 명확히 다름), log gap 없음, postflight clean(신규 `rss_sampler_still_alive`
체크 포함). **사용자가 이 Canary 결과를 근거로 Native macOS ARM64 환경을 Phase 2 Primary Formal
환경으로 채택하고 18-run Matrix 실행을 승인함** — 이 판단으로 `docs/decisions/
phase2-formal-linux-environment.md` 경로(Linux 서버 이전)는 최종적으로 불필요해졌다(그 문서는
이력으로 계속 보존, §1~§11 판정 자체는 유효).

18-run Matrix 실행 전 반영한 2건(상세는 §13/§14):
1. Prometheus temporary TSDB cleanup — `run-phase2-native-formal-benchmark.sh`의 `cleanup()`이
   run 종료 시 자신의 `mktemp -d` TSDB 디렉터리를 삭제하도록 최소 수정(결과 파일은 무관, 삭제 안 함).
2. Formal run ordering을 P-E 9개 → VT-Limited 9개로 block하지 않고 interleave — §15 참고.

**현재 실행 중/다음 단계**: 18-run Native Formal Matrix(`scripts/run-phase2-native-formal-matrix.sh`).

## 13. Formal run ordering — P-E/VT-Limited interleave (2026-08-19)

장시간(18 run × 약 7~8분 ≈ 2시간 이상) native macOS 실행에서 시간대/발열/host state 변화가 특정
config에만 몰리는 것을 피하기 위해, P-E 9개를 모두 먼저 실행하고 VT-Limited 9개를 나중에 실행하는
식으로 config를 block하지 않는다 — 대신 `scripts/run-phase2-native-formal-matrix.sh`의 `RUNS` 배열에서
rep 단위로 두 config를 interleave한다. Rate 순서는 매 rep 안에서 항상 R3→R6→R8, 어느 config가 먼저
오는지는 rep마다 번갈아 확정했다(rep1: P-E 먼저, rep2: VT-Limited 먼저, rep3: P-E 먼저):

```
rep1: P-E R3 → VT R3 → P-E R6 → VT R6 → P-E R8 → VT R8
rep2: VT R3 → P-E R3 → VT R6 → P-E R6 → VT R8 → P-E R8
rep3: P-E R3 → VT R3 → P-E R6 → VT R6 → P-E R8 → VT R8
```

각 (config, load) 쌍의 `run_number`(결과 디렉터리명 `${config}-${load}-run${N}`의 N)는 실행 순서가
아니라 rep 번호(1/2/3)를 그대로 쓴다 — interleave는 실행 *순서*만 바꾸고 결과 식별/저장 방식은
바꾸지 않는다.

## 14. Formal Matrix 중간 안전장치 — per-run validity gate

18개 run을 무조건 끝까지 밀어붙이지 않는다.

**정정(2026-08-19, 18-run Matrix 1차 시작 직후 사용자가 중단·재설계 요청)**: Matrix를 실제로 시작해
3번째 run 진행 중(`pe-r3-run1`, `vtl-r3-run1` 완료, `pe-r6-run1` 진행 중)이었을 때, 사용자가 검토 후
당시 validity gate의 근본적인 공백을 지적해 중단시켰다 — **Native Stability Canary에서 환경 안정성
판단의 핵심 독립 신호였던 Prometheus counter plateau(`gateway_request_received_total`/
`mockllm_completed_requests_total`)를 이 gate가 log-gap 휴리스틱으로만 근사하고 있었고, 게다가 §12의
TSDB 삭제 정책 때문에 run 종료 후에는 그 근사조차 사후 검증할 수 없었다.** 이 상태로 18개를 계속
진행하지 않고, 아래로 재설계한 뒤에만 재개하기로 했다. 이 중단 시점에 이미 완료돼 있던 두 run과
중단된 한 run은 삭제하지 않고 보존하되(`docs/test-results/phase2/unit6-native/
pe-r3-run1-PRE_PLATEAU_CHECK-not-counted`, `vtl-r3-run1-PRE_PLATEAU_CHECK-not-counted`,
`pe-r6-run1-INCOMPLETE-aborted-not-counted`), 아래 신설된 counter-plateau-check.json 증거가 없으므로
공식 18-run dataset에는 포함하지 않는다 — 재개된 Matrix가 같은 이름(`pe-r3-run1` 등)으로 새로
재수집한다.

**신설 — counter-plateau-check.json (authoritative)**: `run-phase2-native-formal-benchmark.sh`가 drain
확인 직후, RSS sampler 정지 후, Postflight 이전(= Prometheus가 아직 살아있고 자신의 scratch TSDB가
아직 삭제되기 전)에 `gateway_request_received_total`/`mockllm_completed_requests_total`을 직접
`query_range`로 조회해 `${OUT_DIR}/counter-plateau-check.json`에 저장한다. 순서는 반드시:
**measurement/drain 완료 → plateau query → counter-plateau-check.json 저장 → `collect_phase2_formal_result.py`
실행 → (스크립트 종료 시) Prometheus 정지 → TSDB 삭제**(§12의 cleanup 정책 자체는 그대로 유지, 순서만
그 앞에 이 조회를 끼워 넣은 것).

- **조회 구간**: `[measurement_start - warmup_sec, measurement_end]`만 — 신규 arrival이 실제로 발생하는
  구간으로 한정한다. Drain 구간(k6가 이미 멈춘 뒤 in-flight 요청이 자연스럽게 정리되는 구간)에서
  counter가 정상적으로 더 이상 늘지 않는 것은 plateau로 오판하지 않는다.
- **계산**: `step=5`(scrape_interval과 동일)로 받은 `(timestamp, value)` 시계열에서, 값이 바뀌지 않고
  유지된 최장 시간 구간(`max_plateau_seconds`)을 각 metric별로 계산한다. `gateway_max_plateau_seconds`,
  `mock_max_plateau_seconds`, `plateau_over_20s`(둘 중 하나라도 20초 **초과**면 true) 저장.
  20초 기준은 기존 `stall_check.json`의 log-gap 20초 기준과 동일한 Formal integrity 기준을 그대로
  가져온 것(임의의 새 숫자 아님) — 근거: Native Canary(clean run)의 최장 plateau는 약 5초였고, Mac
  Docker Desktop rejected 환경에서는 30~35초 plateau가 반복 관측됐다(§ Mac 이력, `docs/decisions/
  phase2-formal-linux-environment.md`).
- Prometheus query 자체가 실패하거나(`query_ok=false`) 파일이 없으면 그 자체로 무효 신호로 취급한다
  (아래 gate 참고).

**Matrix validity gate 재설계** — `run-phase2-native-formal-matrix.sh`가 매 run 직후 아래를 확인,
하나라도 걸리면 해당 run 디렉터리를 `-INVALID-review-needed`(또는 스크립트 자체가 비정상 종료했으면
`-INVALID-script-exit-<code>`) suffix로 rename하고 **matrix를 그 자리에서 중단**한다(`exit 1`) —
나머지 run을 자동으로 계속하지 않는다. Root cause 확인 후 사용자와 다음 단계를 논의한다(Mac/Linux
Canary 실패 때와 동일한 원칙 — 결과를 억지로 valid 처리하거나 자동 재시도하지 않는다):

- `dropped_iterations`가 0 또는 null(=0)인가
- `client_cohort.invariant_ok`가 true인가
- **신설**: `client_cohort.failed_mid_stream`이 0 또는 null(=0)인가
- **신설**: `client_cohort.failed_no_event`가 0 또는 null(=0)인가 — `invariant_ok`는 accounting이
  맞는지(시작=완료+거부+mid-stream 실패+no-event 실패)만 보장할 뿐 실패가 0이라는 뜻이 아니므로 별도
  gate가 필요했다. `rejected`는 의도적으로 hard gate에 넣지 않는다 — R8(over-capacity)에서는 rejection
  자체가 실험이 의도한 정상적인 overload 결과이기 때문.
- `environment.json.postflight_fail`이 빈 문자열인가
- **신설/authoritative로 승격**: `counter-plateau-check.json`이 존재하고 `query_ok=true`이고
  `plateau_over_20s=false`인가

**log gap은 보조 신호로 강등**: `stall_check.json`의 log gap은 이제 hard gate가 아니다 — 위 plateau
check가 훨씬 직접적인 근거(counter 자체의 시계열)를 제공하므로, log gap은 advisory로만 출력하고(대개
plateau 결과와 교차 확인하는 용도) 그 자체만으로 run을 무효 처리하지 않는다.

`measurement_window_completion_rate` vs target 차이는 매 run advisory로 출력만 하고, 임의 percentage
threshold를 걸어 pass/fail을 가르지 않는다(§8-1과 동일 원칙).

**검증(2026-08-19, 재설계 직후, 5분 Canary 재실행 없이)**: (1) `max_plateau_seconds` 알고리즘을
synthetic 케이스(빈 시계열, 단일 샘플, 정상 지속 증가, 정확히 20초 plateau 경계, 30초 total stall,
20초 미만의 분리된 두 짧은 flat 구간)로 unit-test — 전부 기대값과 일치(최초 한 케이스는 테스트 데이터
자체의 손 계산 실수로 실패했다가 수정 후 통과 — 알고리즘 버그 아님). `plateau_over_20s`의 20초
경계값도 "정확히 20초는 트립 안 함, 20.01초는 트립함"을 확인해 `stall_check.json`과 동일한 `>` 규약을
지켰는지 검증. (2) 실제 harness로 짧은 기능 smoke 1회 실행(P-E/R1/warmup10s/measurement20s,
`docs/test-results/phase2/unit6-native/pe-plateau-check-r1-run1`, Formal dataset 미포함)해 전체 배선을
end-to-end로 확인 — `counter-plateau-check.json`이 `collect_phase2_formal_result.py` 실행 **이전**에
생성됐고(요구된 순서 그대로), `query_ok=true`, `gateway_max_plateau_seconds=0.0`,
`mock_max_plateau_seconds=5.0`(클린 케이스, Canary의 ~5초와 일치하는 범위), `plateau_over_20s=false`.
Matrix의 `check_run_validity()` 로직을 이 실제 run 디렉터리에 대해 직접 실행해 `VALID` 판정을 재확인.
Postflight/포트/프로세스/TSDB 전부 clean.

## 참고: `docs/decisions/phase2-formal-linux-environment.md`와의 관계

그 문서(Linux/AWS 경로)는 폐기되지 않고 이력으로 유지한다 — Mac Docker Desktop rejected 판정과
그 근거(§1~§11)는 여전히 유효하다. 다만 AWS EC2 실행 경로(그 문서 이후 대화에서 논의된 c8g.2xlarge
계획)는 사용자가 유료 Cloud를 쓰지 않기로 결정하며 진행하지 않았고, 이 문서(native macOS)가 그
자리를 대체하는 현재 활성 경로다.
