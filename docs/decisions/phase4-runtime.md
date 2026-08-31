# ADR: Phase 4 Unit 0 — Common Runtime Compatibility (PT-Queue / VT / WebFlux)

Status: Accepted (Phase 4 Unit 0 scope only — implementation not started)
Date: 2026-08-25

## 0. 목적

Phase 4는 Phase 1~3와 질문이 다르다 — "누가 더 빠른가"가 아니라 "같은 host/같은 workload에서 세
concurrency architecture(Platform Thread+Queue / Virtual Thread / WebFlux)가 어느 load까지
sustainable하며 어디서 saturation되는가"를 검증한다. 이 비교가 성립하려면 세 구현체가 **동일
runtime**(JDK/Spring/Gradle/Tomcat/Reactor/Micrometer) 위에서 돌아야 한다 — Phase 1~3처럼 서로
다른 runtime(Java 8 / Java 21, Spring Boot 1.5 / 2.7 / 4.1)을 쓰면 cross-model 숫자 비교 자체가
성립하지 않는다(§35의 "Cross-Phase 숫자를 직접 경쟁시키지 않는다"와 동일 원칙을 Phase 4 *내부*
세 모델에도 적용한 것 — 여기서는 반대로 내부에서는 반드시 동일해야 한다는 뜻).

이 문서는 그 공통 runtime을 공식 문서 + 실측 spike로 결정한 기록이다.

## 1. 결론 요약

Phase 2(`gateway-mvc-java21`)가 이미 이 조합을 확립하고 검증해 두었다(`docs/decisions/
version-compatibility.md`, `docs/decisions/phase2-formal-native-macos-environment.md`). Phase 4는
이 조합을 **재확인**하고, WebFlux 축(M3)까지 같은 세대에서 실제로 기동하는지 새로 spike로
검증했다.

| 항목 | 값 | 근거 |
|---|---|---|
| JDK | Eclipse Temurin 21.0.11+10-LTS, macOS aarch64 native | Phase 2 재사용, 이 Unit에서 `java -version`/`release`/`file`/`sysctl.proc_translated`로 재확인 |
| Gradle | 8.14 | Phase 2(`gateway-mvc-java21`)와 동일 wrapper, 이 Unit의 3개 spike도 동일 wrapper로 BUILD SUCCESS |
| Spring Boot | 4.1.0 | 기존 `gateway-mvc-java21/build.gradle` 재확인 + 3개 spike 전부 동일 plugin 버전으로 기동 성공 |
| Spring Framework | 7.0.8 | `gradlew dependencies --configuration compileClasspath` 실측 (MVC/WebFlux 양쪽 동일) |
| Tomcat (M1/M2) | 11.0.22 | 위와 동일, WebFlux(M3) classpath에는 **부재 확인**(jar 안에 tomcat-*/servlet-api-* 0건) |
| Reactor Core (M3) | 3.8.6 | 위와 동일 |
| Reactor Netty (M3) | 1.3.6 (http+core) | 위와 동일 |
| Micrometer (M3, 신규 검증) | 1.17.0 (core + registry-prometheus) | Spike C에서 최초로 실제 빌드/기동 확인 — **주의: Phase 2 자체는 Micrometer를 쓰지 않음**, §5 참고 |
| Host | macOS 26.4.1, Darwin 25.4.0, arm64, 10 core, 16GB RAM, Docker 미사용(native) | Phase 2/3와 동일 host, native 실행 원칙 계승 |

**세 architecture(M1 MVC-Platform / M2 MVC-VirtualThread / M3 WebFlux-WebClient) 모두 동일
Spring Boot 4.1.0 / Spring Framework 7.0.8 세대에서 기동 가능함을 실측으로 확인했다 — Unit 0
blocker 없음.**

## 2. JDK — Eclipse Temurin 21.0.11+10 (macOS aarch64)

Phase 2가 이미 Adoptium 공식 API로 검증한 것을 그대로 재사용한다(새로 다운로드하지 않음 — 이미
`.native-runtime/jdk-21.0.11+10`에 존재, 이 Unit에서는 재검증만 수행).

```
$ java -version
openjdk version "21.0.11" 2026-04-21 LTS
OpenJDK Runtime Environment Temurin-21.0.11+10 (build 21.0.11+10-LTS)
OpenJDK 64-Bit Server VM Temurin-21.0.11+10 (build 21.0.11+10-LTS, mixed mode, sharing)
```

- Artifact: `OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.11_10.tar.gz`
- SHA256: `6ebcf221c9b41507b14c098e93c6ead6440b8d9bd154f8ec666c4c73abbdb201`
- Native arm64: `file bin/java` → `Mach-O 64-bit executable arm64`
- Rosetta 없음: `sysctl -n sysctl.proc_translated` → `0`

전체 근거: `docs/test-results/phase4/unit0-runtime-spike/checksums.txt`, `environment.txt`.

## 3. Gradle — 8.14

Spring Boot 4.1.0 Gradle plugin은 Gradle 8.14 이상(또는 9.x)을 요구한다(기존 `docs/decisions/
version-compatibility.md` §의 실측 표에서 이미 확인된 사실 — "Gradle Wrapper: 8.14 이상 또는
9.x"). 이 Unit의 3개 spike 모두 `gateway-mvc-java21`에서 그대로 복사한 `gradle-wrapper.properties`
(`distributionUrl=...gradle-8.14-bin.zip`, `validateDistributionUrl=true`)로 BUILD SUCCESS했다.

공식 SHA256(`https://services.gradle.org/distributions/gradle-8.14-bin.zip.sha256`, 이 Unit에서
직접 fetch): `61ad310d3c7d3e5da131b76bbf22b5a4c0786e9d892dae8c1658d4b484de3caa`

**주의**: `distributionSha256Sum`이 `gradle-wrapper.properties`에 pin되어 있지 않다(Phase 2부터
이어진 상태) — Unit 1 hardening 후보로 기록만 하고 이 Unit에서 수정하지 않는다.

## 4. Spring stack — 세 architecture 공통 세대 실측

`gradlew dependencies --configuration compileClasspath --offline`(spike A/B는 기존 캐시로 offline
resolve됨) / 온라인(spike C — webflux/micrometer는 이 dev host에 이전에 캐시된 적이 없어 최초 1회
온라인 resolve 필요했음, 이후 재실행은 offline 가능)으로 실측했다. 전체 트리:
`docs/test-results/phase4/unit0-runtime-spike/{existing-java21-module,spike-c}-resolved-dependencies-full.txt`,
요약: `resolved-dependencies.txt`.

핵심 확인 사항:
- `spring-core`/`spring-context`/`spring-web`은 MVC(A/B)와 WebFlux(C) 양쪽에서 **동일하게
  7.0.8** — Spring Boot 4.1.0 BOM이 세 architecture 모두에 동일 Framework 버전을 강제함을 확인.
- WebFlux(spike C) classpath/jar에는 Tomcat/Servlet API가 **전혀 없음**(jar 내부 `tomcat-*`,
  `servlet-api-*` grep 0건, exit code 1) — Phase 3의 `gateway-webflux`가 지켰던 "no Servlet API"
  원칙이 Boot 4.1.0/Java 21 세대에서도 그대로 재현 가능함을 확인.
- Reactor Core 3.8.6 / Reactor Netty 1.3.6은 Boot 4.1.0의 managed 버전이며 M3 전용(M1/M2가
  HttpURLConnection만 쓰는 한 M1/M2 classpath에는 나타나지 않을 것으로 예상 — Unit 2에서 실제
  M1/M2 구현 후 재확인 필요).

## 5. Metrics 라이브러리 — Unit 1로 넘기는 미결정 사항

- Phase 1/2(`gateway-mvc-executor-java8`, `gateway-mvc-java21`)는 `io.prometheus:simpleclient`
  0.16.0을 직접 쓴다.
- Phase 3(`gateway-mvc-blocking-spring5`, `gateway-mvc-webclient`, `gateway-webflux`)는
  Micrometer(`io.micrometer:micrometer-registry-prometheus`)를 쓴다
  (`docs/decisions/phase3-metrics-contract.md`).
- 이 Unit에서 **Micrometer 1.17.0**(Boot 4.1.0의 managed 버전)이 M3(WebFlux) 스파이크에서 실제
  빌드/기동됨을 새로 확인했다. 반면 `io.prometheus:simpleclient` 0.16.0(Phase 1/2가 쓰던 정확한
  pinned 버전)이 Boot 4.1.0/Java 21 위에서도 여전히 동작하는지는 **이 Unit에서 검증하지 않았다**
  (Spike A/B는 web 스켈레톤만 확인, 별도 metrics 의존성을 추가하지 않았음).
- Phase 4는 M1/M2/M3에 **동일한 metrics 라이브러리**를 요구한다(design brief §4 "공통 Runtime").
  이 Unit이 실제로 증명한 것은 Micrometer 쪽뿐이므로, **Micrometer가 Unit 1의 기본 후보**이고
  `io.prometheus:simpleclient` 재사용은 새로 검증이 필요한 미확정 옵션이다 — Unit 1에서 결정한다
  (이 문서에서 결정하지 않음, 결정할 근거가 아직 부족함).

## 6. Spike 결과

Disposable spike 3개(디렉터리 자체는 세션 scratchpad에만 존재, 정식 모듈로 커밋하지 않음).
로그/증거: `docs/test-results/phase4/unit0-runtime-spike/spike-{a,b,c}-startup.log`.

### Spike A — MVC Platform
- `spring-boot-starter-web`만 사용, Boot 4.1.0 / Java 21 toolchain.
- `./gradlew build -x test` → BUILD SUCCESSFUL (offline, 기존 캐시)
- 기동 로그: `Tomcat started on port 18091 (http)`, 예외 없음.
- `GET /ping` → 200, 응답 본문에 `java=21.0.11 arch=aarch64 ... virtual=false` 포함(요청 스레드가
  일반 Platform Thread임을 응답으로 직접 확인).
- 프로세스 정상 종료(SIGTERM 후 확인).

### Spike B — MVC + Virtual Thread
- Spike A와 동일 의존성(추가 스타터 없음), `Executors.newVirtualThreadPerTaskExecutor()`를 필드로
  직접 생성해 사용(Phase 2 `gateway-mvc-java21`의 `VIRTUAL_UNLIMITED` 모드와 동일한 패턴).
- `GET /vt-check` → 200, 응답: `taskThread=VirtualThread[#50]/runnable@ForkJoinPool-1-worker-1
  isVirtual=true` — **`Thread.isVirtual()==true`를 실제 런타임 evidence로 확인**.
- 요청 스레드 자체(Tomcat NIO 스레드)는 여전히 Platform Thread(`isVirtual=false`) — 이는 기대된
  결과다(Servlet 요청 처리 자체는 AsyncContext로 넘기기 전까지 Tomcat worker thread에서 진행되고,
  실제 blocking 작업만 virtual thread executor로 넘어가는 것이 Phase 2/Phase 4 공통 설계임).

### Spike C — WebFlux + WebClient
- `spring-boot-starter-webflux` + `micrometer-registry-prometheus`만 사용, `spring-boot-starter-web`
  **없음**.
- `./gradlew build -x test` → BUILD SUCCESSFUL (webflux/micrometer는 이 dev host에 캐시가 없어
  최초 1회 온라인 resolve 필요했음 — 이후 재현은 `--offline`으로 가능).
- 기동 로그: `Netty started on port 18093 (http)` — **Tomcat 관련 로그 라인 0건**.
- `GET /ping` → 200, 응답에 `webClientClass=org.springframework.web.reactive.function.client.
  DefaultWebClient` 포함 — WebClient가 실제로 인스턴스화/사용 가능함을 확인.
- jar 내부 `unzip -l | grep -i "tomcat\|servlet-api"` → 0건(grep exit code 1) — Servlet API 완전
  부재를 아카이브 레벨에서 직접 확인.
- 프로세스 정상 종료.

## 7. Phase 4에서 재사용 가능한 기존 컴포넌트

- **JDK/Gradle 바이너리**: `.native-runtime/jdk-21.0.11+10`, `~/.gradle/wrapper/dists/gradle-8.14-bin`
  그대로 재사용(재다운로드 불필요).
- **Mock LLM**(`mock-llm-fastapi/app/main.py`): 무수정 재사용 가능. `MOCK_LLM_MAX_CONCURRENT_PROCESSING`/
  `MOCK_LLM_MAX_WAITING`이 둘 다 기본값 `0`이면 `_processing_semaphore=None`이 되어 **완전히
  unlimited**(admission gate 없음) — Phase 4 Primary가 요구하는 "Mock unlimited" semantics를 정확히
  만족하는 기존 동작임을 소스 레벨에서 확인(§8 설계 브리프와 일치). Chunk 설정 knob
  (`firstChunkDelayMs`/`chunkIntervalMs`/`chunkCount`/`chunkSizeBytes`)도 그대로 사용 가능.
- **k6/xk6-sse**: `.native-runtime/k6` — `k6 v1.8.0 (go1.26.6, darwin/arm64)`,
  `Extensions: github.com/phymbert/xk6-sse v0.1.11` — Phase 2/3와 동일 바이너리, 재사용 가능.
  `load-test-k6/scenarios/01-concurrent-connections.js`(closed-model)와
  `03-constant-arrival-rate-continuous.js`(open-model)가 Phase 4 Experiment A/B의 후보 base
  스크립트로 재사용 가능해 보인다(Unit 1에서 admission-removal 관련 스크립트 파라미터만 재검토
  필요, 스크립트 자체 구조는 무수정 재사용 가능성이 높음).
- **Normal SSE workload 값 재확인**: `scripts/run-phase2-native-screening.sh`,
  `run-phase3-native-formal-benchmark.sh` 등이 실제로 `FIRST_CHUNK_DELAY_MS=1000
  CHUNK_INTERVAL_MS=200 CHUNK_COUNT=35 CHUNK_SIZE_BYTES=64`를 사용하고 있음을 grep으로 확인 —
  design brief §7의 후보값과 정확히 일치. 예상 duration도 계산상 일치: `1000 + (35-1)*200 = 7800ms`
  ≈ Phase 2 Canary 실측(`stream duration p95≈7.855s`).
- **`gateway-mvc-java21`의 M2(VT-Unlimited) 골격**: `ChatExecutorConfig`의
  `THREAD_MODE=VIRTUAL_UNLIMITED` 분기가 이미 `Executors.newVirtualThreadPerTaskExecutor()` +
  admission-gate-없음(`VirtualUnlimitedTaskSubmitter`) 조합으로 구현되어 있다 — design brief §3
  M2("Application admission ceiling: 없음")와 정확히 일치하는 기존 코드. `MockLlmClient`도 이미
  `HttpURLConnection`만 사용(WebClient 없음). **M2는 이 모듈을 강하게 재사용/포크할 후보.**

## 8. Phase 4에서 새로 구현해야 하는 것 (재사용 불가로 확인된 부분)

- **M1(PT-QUEUE)은 `gateway-mvc-java21`의 기존 `THREAD_MODE=PLATFORM`을 그대로 재사용할 수
  없다.** 기존 P-E 설정은 `SynchronousQueue`(사실상 큐 용량 0 — 즉시 handoff 또는 reject)를
  쓴다(`ChatExecutorConfig.platformExecutor()` 실측 확인). 그러나 Phase 4 design brief §3 M1은
  명시적으로 **"bounded queue"**(실제 대기열이 있는 아키텍처)를 요구하고, H4-b는 "queue wait와
  TTFC degradation의 관계"를 직접 검증 대상으로 삼는다 — `SynchronousQueue`에는 관찰 가능한 queue
  wait가 사실상 없으므로 이 가설을 검증할 수 없다. **M1은 `LinkedBlockingQueue`/`ArrayBlockingQueue`
  같은 실제 bounded queue + `AbortPolicy` 조합으로 새로 설계해야 한다** — Unit 1/2 blocker는 아니지만
  design 결정이 필요함을 여기 기록한다.
- **M3(WebFlux) 전용 모듈**: 기존 `gateway-webflux`는 Java 8 / Spring Boot 2.7.18이라 재사용
  불가 — Boot 4.1.0 / Java 21 기준으로 새로 작성해야 한다(다만 Spike C가 그 골격이 문제없이
  기동함을 이미 증명했으므로 구조적 blocker는 없음).
- **Admission 제거**: 기존 모듈들은 모두 `CHAT_ADMISSION_LIMIT`류의 gate를 갖고 있다(Phase 2/3
  공통) — Phase 4 Primary는 M2/M3에서 이 gate를 완전히 제거해야 한다(design brief §8). M1은
  ThreadPoolExecutor/큐 자체가 bounded resource이므로 별도 admission gate가 필요 없다.
- **Metrics 라이브러리 통일**: §5 참고 — Micrometer로 통일할지, 기존 Phase 2 방식
  (`io.prometheus:simpleclient`)을 Boot 4.1.0에서 재검증할지 Unit 1에서 결정 필요.

## 9. Risk / Blocker

**Blocker 없음.** 세 architecture 모두 동일 Spring Boot 4.1.0 / Spring Framework 7.0.8 / Java 21
세대에서 기동 가능함을 실측으로 확인했다.

발견된 리스크(모두 Unit 1에서 다룰 설계 결정이지 구조적 blocker가 아님):
1. M1의 큐 타입이 Phase 2 P-E(SynchronousQueue)와 Phase 4 M1(bounded queue) 사이에서 달라져야
   한다 — §8.
2. Metrics 라이브러리 미확정(Micrometer vs simpleclient) — §5.
3. Gradle wrapper에 `distributionSha256Sum` pin이 없음 — §3, hardening 후보.
4. WebFlux/Micrometer 의존성이 이 dev host에 사전 캐시되어 있지 않아 최초 1회 온라인 resolve가
   필요했다 — Formal 실행 전에 Unit 1/2 초기에 한 번 더 `--offline` 재현이 되는지 확인 필요(이미
   이번 Unit에서 캐시가 생성됐으므로 이후에는 offline 가능할 것으로 예상되나 미확인).

## 10. Unit 1 TODO

- M1 큐 타입/용량/rejection policy를 bounded queue 기준으로 명시적으로 설계.
- Metrics 라이브러리(Micrometer vs simpleclient) 결정.
- 세 모델의 정확한 module 디렉터리 이름 확정(design brief §32 후보).
- SLO threshold(TTFC p95<=2.0s, duration p95<=10.0s) rationale 문서화 및 freeze — **screening 데이터
  보기 전에**.
- k6 시나리오(01/03)의 admission-removal 관련 파라미터 재검토(Gateway 쪽에 admission 자체가
  없어지므로 k6 스크립트가 admission 관련 태그/기대값을 갖고 있다면 제거 필요할 수 있음 — 이번
  Unit에서는 코드를 읽었을 뿐 수정하지 않았음).
- Gradle `distributionSha256Sum` pin 여부 결정(hardening, 선택사항).
