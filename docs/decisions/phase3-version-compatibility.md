# ADR: Phase 3 Version & Runtime Compatibility Decisions

Status: Accepted (Phase 3 Unit 0 scope)
Date: 2026-08-22

이 문서는 프로젝트 설계 원칙(추측 금지, 공식 문서/실측 확인 후 결정)에 따라 Phase 3 Unit 0에서
실측 확인한 버전 결정을 기록한다. 조사/실측 시점은 2026-08 기준이며, Phase 3 착수 이후 버전이
바뀌지 않는 한 재확인하지 않는다.

**근거 우선순위 (Phase 1 ADR과 동일 원칙 적용):** 공식 vendor metadata API(Azul) → 공식 Gradle
compatibility matrix → Spring 공식 문서 → 실제 `gradle dependencies` 해석 결과 / Spring Boot BOM
POM 실측 → 제3자 사이트는 호환성 결정의 근거로 사용하지 않는다.

## 0. 이 문서의 목적 — Java8 선택의 의미

이 조합(Java 8 + Spring Boot 2.7.18 / Spring Framework 5.3.31)은 **2026년 기준 신규 production
스택 추천이 아니다.** Spring Boot 2.7은 이미 OSS 지원이 종료됐고(2023-11), Spring Framework 6.0+/
Boot 3.x가 현재 세대다.

이 조합을 선택한 이유는 Phase 3의 연구 질문 자체가:

> "현재 업무 환경(Java 8 + Spring Framework 4.x)에서 **Java 8은 유지하면서 Spring 세대만
> 현실적으로 올렸을 때**(Spring 5.3.x/Boot 2.7.x 수준), WebClient/WebFlux를 적용할 수 있는가?"

이기 때문이다. 즉 이 런타임은 "권장 스택"이 아니라 **Java8 유지 + 최소 Spring 업그레이드의 실현
가능성을 검증하기 위한 compatibility benchmark runtime**이다. Phase 2(Spring Boot 4.1.0 / Spring
Framework 7.0.8, Java 21)와는 의도적으로 다른, 별도의 실험 축이다 — Phase 2 대비 "더 오래된
런타임을 새로 채택"하는 것이 아니라, "Java8을 못 벗어나는 조직이 실제로 취할 수 있는 최소 변경
경로"를 재현하는 것이 목적이다.

## 1. Host / Build Environment

- Host: macOS (Darwin), `uname -m` = **arm64** (Apple Silicon), macOS 26.4.1 (`sw_vers` 실측)
- 원칙(Phase 2 native 채택 이후 동일 유지): Formal Benchmark는 **native macOS ARM64**만 사용한다.
  Rosetta/QEMU/Docker Desktop VM을 경유하는 emulation은 Formal에 사용하지 않는다.

## 2. JDK — Azul Zulu OpenJDK 8 (macOS ARM64 native)

### 2-1. Temurin을 쓰지 않는 이유

Eclipse Temurin(Phase 1/2가 계속 사용해온 벤더)은 **JDK 8의 macOS 빌드를 x64로만 배포**하며,
macOS aarch64(Apple Silicon) native 빌드가 존재하지 않는다(Adoptium 공식 supported-platforms
확인). 이는 Phase 1/2의 "Temurin 일관성" 관례에서 벗어나는 예외이며, 이번 Phase 3에서만 벤더를
바꾸는 근거는 이 한 가지 — **Java8 macOS aarch64 native 빌드가 Temurin에는 존재하지 않기
때문**이다.

### 2-2. 선택: Azul Zulu

Azul 공식 metadata API(`api.azul.com/metadata/v1/zulu/packages`)를 통해 GA 상태의 macOS aarch64
JDK8 최신 빌드를 실측 조회했다.

| 항목 | 값 | 근거 |
|---|---|---|
| Vendor | Azul Systems, Inc. | Azul 공식 metadata API 응답 |
| Artifact 파일명 | `zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64.tar.gz` | API 응답 `name` 필드 |
| Java version | **1.8.0_504** (`java_version`: 8.0.504) | `java -version` 실측 + API 응답 일치 |
| Zulu distro version | 8.96.0.205 | API 응답 `distro_version` |
| Download URL | `https://cdn.azul.com/zulu/bin/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64.tar.gz` | API 응답 `download_url` |
| SHA256 (공식 API 값) | `58bb3c08f2aa63d9743cf31899fa4b8c6c9effefce9479e7288c26621c3bb21b` | Azul metadata API 응답 |
| SHA256 (다운로드 파일 실측) | `58bb3c08f2aa63d9743cf31899fa4b8c6c9effefce9479e7288c26621c3bb21b` | `shasum -a 256` 실행 결과, **일치 확인** |
| support_term | lts | API 응답 |
| 설치 경로 | `.native-runtime/zulu8.96.0.205-ca-jdk8.0.504-macosx_aarch64/Contents/Home` | 표준 macOS JDK bundle 레이아웃 |

### 2-3. Native ARM64 실측 검증 (Rosetta/emulation 없음)

```
$ java -version
openjdk version "1.8.0_504"
OpenJDK Runtime Environment (Zulu 8.96.0.205-CA-macos-aarch64) (build 1.8.0_504-b01)
OpenJDK 64-Bit Server VM (Zulu 8.96.0.205-CA-macos-aarch64) (build 25.504-b01, mixed mode)

$ java -XshowSettings:properties -version 2>&1 | grep -E "os\.(arch|name)"
    os.arch = aarch64
    os.name = Mac OS X

$ file bin/java
bin/java: Mach-O 64-bit executable arm64

$ cat release | grep -E "OS_ARCH|IMPLEMENTOR"
IMPLEMENTOR="Azul Systems, Inc."
OS_ARCH="aarch64"

$ sysctl -n sysctl.proc_translated
0
```

`file(1)`이 `arm64` Mach-O(= x86_64가 아님)를 직접 보고하고, `os.arch=aarch64`, `sysctl.proc_translated=0`
(현재 셸이 Rosetta 하에서 실행되고 있지 않음)이 모두 일치 — **Rosetta/에뮬레이션 없이 native
arm64로 실행됨을 실측 확인.**

## 3. Gradle — 7.6.6

### 3-1. 선택 근거

- Spring Boot 2.7.18 Gradle plugin 공식 문서(`docs.spring.io/spring-boot/docs/2.7.18/gradle-plugin/reference/htmlsingle/`):
  **"Spring Boot's Gradle plugin requires Gradle 6.8, 6.9, 7.x, or 8.x"**
- Gradle 공식 Compatibility Matrix(Phase 1 ADR §3에서 이미 인용한 것과 동일 공식 출처,
  `docs.gradle.org/current/userguide/compatibility.html`): **JDK 8은 Gradle 2.0 ~ 8.14.x 구동
  가능** — Zulu 8 위에서 Gradle 7.x를 직접(별도 build-time JDK 없이) 구동 가능함을 의미.
- "안정적인 마지막 7.x 버전"을 선택하라는 지시에 따라, Gradle 공식 릴리스 목록
  (`gradle.org/releases`)에서 **7.x 라인의 최종 릴리스인 7.6.6(2025-07-04)**을 확정.

### 3-2. 다운로드/검증

| 항목 | 값 |
|---|---|
| Distribution | `gradle-7.6.6-bin.zip` |
| 공식 SHA256 (`services.gradle.org/distributions/gradle-7.6.6-bin.zip.sha256`) | `673d9776f303bc7048fc3329d232d6ebf1051b07893bd9d11616fad9a8673be0` |
| 실측 SHA256 | `673d9776f303bc7048fc3329d232d6ebf1051b07893bd9d11616fad9a8673be0` — **일치** |

### 3-3. Zulu 8 위에서 native 실행 확인

```
$ JAVA_HOME=<zulu8 home> ./gradle-7.6.6/bin/gradle -v
------------------------------------------------------------
Gradle 7.6.6
------------------------------------------------------------
JVM:          1.8.0_504 (Azul Systems, Inc. 25.504-b01)
OS:           Mac OS X 26.4.1 aarch64
```

Gradle 자체가 Zulu 8(JVM 1.8.0_504) 위에서, macOS 26.4.1 aarch64로 직접 구동됨을 확인 — 별도의
build-time JDK(예: 기존 Phase 2용 JDK21)가 필요하지 않다.

## 4. Spring Boot 2.7.18 Minimal Spike — 결과

Unit 0 목적에 맞게 production Gateway가 아닌 disposable spike 3종을 스크래치 디렉터리에서
빌드/기동해 검증했다(Gradle 7.6.6 + Zulu 8, `--no-daemon`).

### 4-1. 확인 A — Spring MVC minimal start

- 앱: `spring-boot-starter-web`만 의존
- `BUILD SUCCESSFUL`, `Compiling with toolchain '<zulu8 Home>'`
- 기동 로그: `Tomcat initialized with port(s): 18081`, `Starting Servlet engine: [Apache Tomcat/9.0.83]`,
  `Started SpikeMvcApplication in 3.026 seconds` — **예외 없음**
- `curl /ping` → `mvc-ok:1.8.0_504` (200 OK)
- **PASS**

### 4-2. 확인 B — MVC + WebClient (dependency/class load)

- 앱: `spring-boot-starter-web` + `spring-boot-starter-webflux` 동시 의존(WebClient만 쓰고 서버는
  MVC 유지하는, P3-B와 동일한 구성 패턴)
- `BUILD SUCCESSFUL`
- 기동 로그: **`Tomcat`이 기동됨** (`Starting Servlet engine: [Apache Tomcat/9.0.83]`), Netty 서버는
  기동되지 않음 — 즉 두 스타터가 공존해도 Spring Boot의 `WebApplicationType` 추론이 SERVLET(MVC)을
  선택함을 실측 확인. `Started SpikeMvcWebClientApplication in 1.217 seconds` — **예외 없음**
- `curl /ping` → `mvc-ok:1.8.0_504` (200 OK)
- `curl /webclient-check` → `webclient-class-loaded:org.springframework.web.reactive.function.client.DefaultWebClient` (200 OK)
  — WebClient 인스턴스 생성 및 클래스 로드 성공
- **PASS**

### 4-3. 확인 C — WebFlux + Reactor Netty native start

- 앱: `spring-boot-starter-webflux`만 의존(MVC 스타터 없음)
- `BUILD SUCCESSFUL`
- 기동 로그: **`o.s.b.web.embedded.netty.NettyWebServer : Netty started on port 18083`** — Tomcat
  기동 로그 없음. `Started SpikeWebfluxApplication in 1.079 seconds` — **예외 없음**
- `curl /ping` → `webflux-ok:1.8.0_504` (200 OK, `Mono<String>` reactive 응답)
- **PASS**

세 spike 모두 종료 후 프로세스/포트(18081-18083) 정리 확인.

## 5. 실제 Resolved Dependency Versions

`./gradle dependencies --configuration runtimeClasspath` 실행 결과(spike-mvc-webclient,
spike-webflux 두 프로젝트에서 개별 확인, Reactor/Netty/Spring 핵심 버전 완전 일치) + Boot
2.7.18 BOM(`spring-boot-dependencies-2.7.18.pom`, 로컬 캐시 실측)의 `<properties>` 교차 확인.

| 항목 | 실제 resolve된 버전 | 확인 방법 |
|---|---|---|
| Spring Boot (plugin) | 2.7.18 | build.gradle 직접 지정 |
| Spring Framework | **5.3.31** | `gradle dependencies` (spring-webflux/spring-webmvc/spring-web/spring-core 등 전부 5.3.31) |
| Reactor Core | **3.4.34** | `gradle dependencies` (`io.projectreactor:reactor-core:3.4.34`) — BOM의 `reactor-bom.version`은 릴리스 트레인명 `2020.0.38`이며 실제 아티팩트 버전은 3.4.34로 일치 |
| Reactor Netty | **1.0.39** | `gradle dependencies` (`reactor-netty-core:1.0.39`, `reactor-netty-http:1.0.39`) |
| Micrometer | **1.9.17** | BOM property(`<micrometer.version>1.9.17</micrometer.version>`) + `micrometer-core` 실제 의존성 추가 후 `gradle dependencies` 재해석으로 교차 검증(`io.micrometer:micrometer-core -> 1.9.17`) — 두 방법 일치 |
| Netty | **4.1.101.Final** | `gradle dependencies` (`netty-common`, `netty-codec-http` 등) |
| Tomcat (embedded) | **9.0.83** | `gradle dependencies` (`tomcat-embed-core:9.0.83`) + 실행 로그(`Apache Tomcat/9.0.83`) 이중 확인 |
| Jackson (databind) | 2.13.5 | `gradle dependencies` (참고용, Phase 3 핵심 결정 대상은 아님) |
| Gradle | **7.6.6** | §3 |

MVC+WebClient spike와 WebFlux spike 양쪽에서 Reactor Core/Reactor Netty/Netty/Spring Framework
버전이 **완전히 동일**함을 확인 — 동일 Boot BOM을 공유하므로 P3-B/P3-C 간 이 축의 버전 불일치는
없다.

증거 원본(빌드/기동 로그, dependency 목록)은 `docs/test-results/phase3/unit0-runtime-spike/`에
보존.

## 6. Java 8 Native ARM64 — 최종 판정: **PASS**

다음 조건이 모두 실측으로 만족되었다:

- [x] `uname -m` = arm64
- [x] JDK 프로세스 native arm64 (`file(1)` Mach-O arm64, `os.arch=aarch64`)
- [x] Rosetta/emulation 없음 (`sysctl.proc_translated=0`)
- [x] Gradle 7.6.6이 Zulu 8 위에서 native 구동
- [x] Spring Boot 2.7.18 MVC 기동 성공 (Tomcat 9.0.83)
- [x] WebClient dependency/class load 및 MVC 공존 성공
- [x] WebFlux + Reactor Netty native 기동 성공

**결론: Phase 3 공통 Benchmark runtime = Java 8 (Azul Zulu 8.96.0.205, macOS ARM64 native) +
Spring Boot 2.7.18 + Gradle 7.6.6으로 채택(PASS). Java 11 fallback은 사용하지 않는다.**

## 7. Architecture / Environment Policy (Phase 2 원칙 승계)

- Formal Benchmark: native macOS ARM64만 사용
- Rosetta / QEMU / 기타 emulation: 금지
- Mac Docker Desktop: Formal 환경으로 사용 금지 (Phase 2에서 이미 rejected)
- 이 정책은 `docs/decisions/phase2-formal-native-macos-environment.md`와 동일한 원칙을 Phase 3에
  승계한 것이며, 재도출하지 않는다.

## 8. Scope 제한 재확인

이 문서가 확정하는 것은 **런타임 버전 조합**뿐이다. 다음은 이 문서의 범위 밖이며 Unit 1에서
결정한다 (§9 참고). 이 ADR은 P3-A/B/C의 실제 기능 구현, admission 정책, metric 이름을 확정하지
않는다.

## 9. Unit 1 TODO (이 ADR에 남기는 미결정 사항 — Unit 0에서는 구현하지 않음)

- P3-A/P3-B 공통 **AsyncContext + PrintWriter** write path의 구체 구조(승인된 결정: SseEmitter로
  전환하지 않음)
- 공통 **bounded servlet-write executor**(queue capacity, rejection policy)
- **per-request serialized write**(동일 stream 내 chunk ordering 보장, 동시 write 금지, bounded
  buffering, disconnect 시 pending chunk 폐기, timeout/cancel 시 정리, completion 이후 write 금지)
- Admission ceiling(Semaphore `tryAcquire()` 기반, Phase 2 `VirtualLimitedTaskSubmitter` 패턴 재사용)
- WebClient `ConnectionProvider`(maxConnections/pendingAcquireMaxCount/pendingAcquireTimeout) 정책
- **Absolute total stream deadline 구현** — 아래 중요 사항 참고
- Cancellation/resource lifecycle 정리(permit 반환, connection pool pending=0 등)
- Common metric contract 확정(`gateway_*` 이름, Micrometer vs `simpleclient` 조합 방식)

### 중요 — Flux.timeout(Duration)의 의미론

Reactor Core 공식 API 문서 확인 결과, **`Flux.timeout(Duration timeout)`은 absolute total stream
deadline이 아니라 idle/per-signal timeout**이다 — 직전 emission(또는 첫 아이템이면 구독 시점)으로
부터 주어진 Duration 안에 다음 아이템이 없으면 발생하며, 매 emission마다 타이머가 리셋된다.

Phase 1의 "single absolute deadline" 의미론(`docs/decisions/timeout-semantics.md`)을 Phase 3에서도
유지하려면 이 연산자를 그대로 쓸 수 없다 — 별도 구성(예: `Mono.delay(deadline)` 기반 별도 종료
시그널과의 경합)이 필요하며, **Unit 1에서 코드+테스트로 검증**한다. 이 ADR은 결정을 내리지 않고
위험으로만 기록한다.

## 10. Disposable Spike 자산 처리

Unit 0의 spike 코드(`spike-mvc`, `spike-mvc-webclient`, `spike-webflux`)는 정식 Phase 3 module이
아니므로 저장소에 커밋하지 않고 세션 스크래치 디렉터리에만 존재한다(빌드된 jar 포함, git 추적
대상 아님). 검증 근거가 되는 로그/버전 출력만 `docs/test-results/phase3/unit0-runtime-spike/`에
보존했다:

- `spike-mvc-run.log`, `spike-mvc-webclient-run.log`, `spike-webflux-run.log` — 기동 로그 원본
- `spike-mvc-build-tail.log`, `spike-mvc-webclient-build.log`, `spike-webflux-build.log` — 빌드 로그
- `resolved-dependency-versions.txt` — `gradle dependencies` 발췌 + micrometer-core 교차검증 결과
