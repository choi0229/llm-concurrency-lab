# ADR: Version & Runtime Compatibility Decisions

Status: Accepted (Phase 1 scope)
Date: 2026-08-10 (2026-08-10 재검토: Gradle 버전 결정을 공식 문서 기준으로 정정)

이 문서는 프로젝트 설계 원칙(추측 금지, 공식 문서 확인 후 결정)에 따라 확인한 버전 결정을 기록한다.
버전은 조사 시점(2026-08) 기준이며, 다른 Phase 착수 시 재확인한다.

**근거 우선순위 (이 문서 전체에 적용):** Spring 공식 문서 → Gradle 공식 문서 → OpenJDK/JEP → Docker 공식
이미지/manifest → 각 라이브러리 공식 문서/GitHub. mvnrepository 등 제3자 사이트는 호환성 결정의 근거로
사용하지 않는다 (존재 여부 확인 등 보조적 참고에만 과거 사용했던 것을 이번 재검토에서 공식 소스로 교체함).

## 0. 이 프로젝트의 성격 — Production 권장이 아니다

`gateway-mvc-executor-java8`에서 쓰는 Java 8 / Spring Framework 4.3.x / Spring Boot 1.5.x는 모두
**공식 보안 지원이 종료된(EOL) 버전**이다. 이 조합을 선택한 이유는 "이 스택을 프로덕션에 권장"하기 위해서가
아니라, **실무에서 실제로 관찰됐던 특정 실행 모델(Servlet AsyncContext + 전용 ThreadPoolExecutor +
Blocking HTTP I/O)을 정확히 재현해 그 한계를 정량적으로 측정하기 위한 Benchmark Lab**이기 때문이다.
이 프로젝트의 최종 산출물(Decision Matrix, Modernization Roadmap)은 오히려 이 레거시 조합에서 벗어나기 위한
근거를 제공하는 것을 목표로 한다. 신규 프로덕션 시스템에 이 버전 조합을 그대로 채택해서는 안 된다.

## 1. Host / Build Environment

- Host: macOS (Darwin), CPU Architecture = **arm64 (Apple Silicon)**
- 결론: Eclipse Temurin은 `linux/arm64/v8`을 공식 지원하며, **Java 8 이미지도 arm64 native 빌드가 존재**한다
  (`eclipse-temurin:8-jdk-jammy` / `8-jre-jammy` 계열, multi-arch manifest에 arm64v8 포함).
  → **Java 8 컨테이너도 QEMU Emulation 없이 host와 동일 architecture로 실행 가능** (설계 문서 8번 항목의 위험이 해소됨).
- 원칙: 모든 Gateway/Mock LLM 컨테이너는 **linux/arm64/v8 이미지를 우선 사용**하고, 특정 이미지가 arm64를 지원하지 않는 경우에만
  그 사실을 이 문서에 별도로 기록하고 예외 처리한다 (Emulation을 쓰는 컨테이너와 안 쓰는 컨테이너를 같은 벤치마크 런에 섞지 않는다).
- 각 테스트 실행마다 `docs/test-results/**/environment.json`에 `uname -m`(컨테이너 내부), `docker inspect .Architecture`,
  이미지 digest를 기록한다.

Source: [Eclipse Temurin 8u492 release notes](https://adoptium.net/news/2026/05/eclipse-temurin-8u492-11031-17019-21011-2503-2601-available), [eclipse-temurin Docker Hub](https://hub.docker.com/_/eclipse-temurin)

## 2. gateway-mvc-executor-java8 (Baseline)

| 항목 | 결정 | 근거 |
|---|---|---|
| JDK | **Java 8** (Eclipse Temurin 8, 최신 8u 패치) | 원 실무 환경의 실행 모델(Servlet AsyncContext + 전용 ThreadPoolExecutor + Blocking HTTP I/O)을 동일 JVM 세대에서 재현하기 위함 |
| Base Image (build) | `eclipse-temurin:8-jdk-jammy` (linux/arm64/v8) | 공식 multi-arch 이미지, host와 동일 architecture |
| Base Image (runtime) | `eclipse-temurin:8-jre-jammy` (linux/arm64/v8) | JRE만 포함해 런타임 이미지 최소화 |
| Application Framework | **Spring Boot 1.5.22.RELEASE** (1.5.x 라인의 최종 릴리스) | **중요한 명시: 이 선택은 "실제 업무 시스템과 동일 제품 구성을 복제한다"는 의미가 아니다.** 원 실무 환경은 Java 8 + Spring 4.x + Spring MVC이며, Spring Boot 사용 여부는 그 환경의 필수 조건이 아니었다(Spring Boot를 안 쓰고 WAR+외부 Tomcat으로 배포했을 수도 있음 — 알 수 없음). 여기서 Spring Boot 1.5.x를 쓰는 이유는 **AsyncContext + ThreadPoolExecutor + Blocking HTTP I/O라는 동일한 실행 모델을 독립 실행 가능한(Docker Runtime Isolation 원칙, 섹션 8) 형태로 가장 낮은 구현 난이도로 재현하기 위한 수단**일 뿐이다. 두 대안(순수 Spring Framework+Servlet/Tomcat 직접 구성 vs Spring Boot)의 비교는 아래 2-1절 참고. 특정 patch 버전(1.5.21 등)을 써야 할 근거가 없으므로 1.5.x 라인의 최종 릴리스인 **1.5.22.RELEASE**를 채택 |
| 하위 Spring Framework 정확 버전 | **Spring Framework 4.3.25.RELEASE** | `./gradlew dependencies --configuration compile`로 실측 확정 (2026-08-10, arm64 컨테이너). Spring Boot 1.5.22 공식 System Requirements 문서에도 "Spring Framework 4.3.25.RELEASE 이상 요구"로 명시되어 일치함 |
| Servlet Container | **Embedded Tomcat 8.5.43** | 위와 동일하게 실측 확정 |
| Jackson (JSON) | **2.8.11.3** | 위와 동일하게 실측 확정 |
| Build Tool | Gradle (버전은 아래 3번 항목 참조) | - |
| Metrics → Prometheus | `io.prometheus:simpleclient:0.16.0` + `simpleclient_common:0.16.0`(TextFormat) + 커스텀 `/metrics` Controller | Spring 4.x/Boot 1.x 시대는 Micrometer 이전이라 Prometheus 공식 Java client(`simpleclient`)를 직접 사용. **Dropwizard MetricRegistry + `simpleclient_dropwizard` 브릿지는 채택하지 않음** — Dropwizard 메트릭은 이름에 label 차원이 없어서, 이 프로젝트가 요구하는 `mode="pool"/"caller"` 같은 label 기반 메트릭을 표현하려면 메트릭 이름 자체를 쪼개는 우회가 필요해진다. `simpleclient`의 네이티브 `Histogram`/`Counter`(`.labels(...)`)가 label을 직접 지원하므로 이쪽이 더 정확하고 단순하다. **0.16.0이 legacy `simpleclient` 라인의 마지막 버전임을 공식 GitHub(`github.com/prometheus/client_java`, README + `simpleclient` 브랜치)로 확인** |
| Outbound HTTP | `HttpURLConnection` (JDK 표준) | 설계 원칙상 Baseline은 외부 HTTP 라이브러리 없이 JDK 표준 API만 사용 |

Source: [Spring Boot 1.5.22 System Requirements](https://docs.spring.io/spring-boot/docs/1.5.22.RELEASE/reference/html/getting-started-system-requirements.html), [Spring Boot 1.5 Release Notes](https://github.com/spring-projects/spring-boot/wiki/Spring-Boot-1.5-Release-Notes), [Spring Framework 4.3 EOL](https://spring.io/blog/2019/12/03/spring-framework-maintenance-roadmap-in-2020-including-4-3-eol/), [Prometheus client_java (official GitHub)](https://github.com/prometheus/client_java)

### 2-1. 대안 비교: Spring Boot 1.5.22 vs 순수 Spring Framework 4.3.x + Servlet/Tomcat 직접 구성

| 기준 | 순수 Spring Framework + Servlet/Tomcat | Spring Boot 1.5.22 |
|---|---|---|
| 원 실무 환경과의 근접성 | `web.xml`/`WebApplicationInitializer` + 외부 또는 수동 embed Tomcat으로, "Spring Boot를 언급하지 않은" 원 설명과 형태상 더 가까울 수 있음(단, 원 설명에 Boot 미사용이 확정된 것도 아님) | 내부적으로는 동일한 Spring MVC(DispatcherServlet) + Servlet 3.1 AsyncContext + Tomcat을 사용 — **런타임 실행 모델은 동일**, 부트스트랩 방식만 다름 |
| 독립 실행 가능한 Docker 이미지 확보 난이도 | WAR 빌드 + 외부 Tomcat 이미지 조합 또는 수동 embedded Tomcat 부트스트랩 코드 필요 — 설정 코드가 늘어나고 실수 여지가 큼 | embedded Tomcat 기본 제공, `bootRepackage`로 즉시 실행 가능한 단일 jar 생성 — Docker Runtime Isolation 원칙(섹션 8)과 바로 맞음 |
| 의존성 버전 관리 | Spring/Tomcat/Jackson 등 버전을 모두 수동 지정, 서로 호환되는 조합을 직접 조사해야 함 | Spring Boot 1.5.22 BOM이 상호 호환 검증된 버전 세트(Spring 4.3.25 / Tomcat 8.5.43 / Jackson 2.8.11.3 등)를 자동 고정 — 버전 조합 실수 위험이 낮음 |
| 연구 질문과의 관련성 | 이 Lab이 측정하려는 것은 AsyncContext/ThreadPoolExecutor/HttpURLConnection의 동시성 특성이지 Spring의 부트스트랩 방식이 아님 — 부트스트랩 방식은 연구 질문과 무관 | 동일하게 무관 |
| 구현 난이도(Phase 1 관점) | 높음 — 본 실험과 무관한 Servlet/Tomcat 배선 코드에 시간 소요 | 낮음 — 이미 검증됨(아래 3절), 바로 핵심 로직(AsyncContext/Executor/HttpURLConnection) 구현으로 진입 가능 |

**결정: Spring Boot 1.5.22.RELEASE 채택.** 근거: (1) 실행 모델(Servlet AsyncContext + Blocking I/O)은 두 방식에서 동일하게 재현되므로 부트스트랩 방식 차이가 연구 결과에 영향을 주지 않는다, (2) Phase 1의 목적은 Executor/HTTP Client 동시성 실험이지 Servlet 배선 실험이 아니므로 구현 난이도가 낮은 쪽을 택하는 것이 합리적이다, (3) 이미 Gradle 3.5.1 + Spring Boot 1.5.22 조합으로 빌드~기동까지 실측 검증을 마쳤다(3절 참고).

## 3. Gradle Wrapper 전략 (Java 8 프로젝트) — 재정정 (공식 System Requirements 기준)

**1차 정정 (실행 JVM 요구사항):** "Gradle 실행에 JDK17+가 필요하다"는 설명은 최신 Gradle(9.0+)에만 해당한다.
Gradle 공식 Compatibility Matrix(docs.gradle.org/current/userguide/compatibility.html) 기준:

| 실행 JVM | Gradle 구동 가능 버전 범위 |
|---|---|
| JDK 8 | **Gradle 2.0 ~ 8.14.x** |
| JDK 11 | Gradle 5.0 ~ 8.14.x |
| JDK 17 | Gradle 7.3 이후 |
| JDK 21 | Gradle 8.5 이후 |

**2차 정정 (Spring Boot 1.5.22의 공식 지원 Gradle 버전):** 이전 버전 문서에서는 "Spring Boot 1.5.x 플러그인이
Gradle 4.0 초과를 요구한다"는 비공식 검색 요약을 근거로 **Gradle 4.10.3**을 채택했었다. 그러나 이는 제3자
사이트 기반 추정이었고, **공식 문서 확인 결과 사실이 아니었다.**

`docs.spring.io/spring-boot/docs/1.5.22.RELEASE/reference/html/getting-started-system-requirements.html`
(Spring Boot 1.5.22 공식 System Requirements 페이지, 1.5.9.RELEASE 페이지로도 교차 확인)의 "Build Tool
Compatibility" 표에 명시된 **공식 지원 Build Tool 버전은 다음과 같다:**

| Build Tool | 공식 지원 버전 |
|---|---|
| Maven | 3.2+ |
| **Gradle** | **2.9 ~ 3.x** (Gradle 4.x 이상은 공식 문서에 명시된 지원 범위 밖) |

Gradle 3.x 라인의 마지막 릴리스는 **3.5.1** (2017-06-16 릴리스, `gradle.org/releases` 공식 페이지로 확인,
`docs.gradle.org/3.5.1/release-notes.html` 존재 확인)이다. JDK 8로 Gradle 3.5.1을 구동하는 것은 위 표의
JDK8 지원 범위(2.0~8.14.x) 안에 있으므로 문제 없다.

**최신 Gradle(4.10.3)이 실제로는 빌드에 성공했더라도(우연히 하위 호환 API만 사용됐을 뿐), 공식 문서가
명시하지 않은 조합에 의존하는 것은 재현성 리스크다 — 향후 Gradle이나 플러그인의 사소한 변경으로 깨질 수
있는 "우연히 동작하는" 조합이기 때문이다. 따라서 공식 지원 범위 안의 버전으로 되돌린다.**

**✅ Gradle 3.5.1로 재검증 완료 (2026-08-10, `eclipse-temurin:8-jdk-jammy`, linux/arm64 native, 에뮬레이션 없음):**
- Wrapper를 `./gradlew wrapper --gradle-version 3.5.1`로 재생성 (기존 Gradle 4.10.3 wrapper가 스스로 3.5.1용 wrapper를 만들어줌 — `wrapper` 태스크 자체는 어떤 버전의 Gradle로도 실행 가능하므로 문제 없음)
- `java -version` → `openjdk version "1.8.0_492"` (Temurin, build 1.8.0_492-b09)
- `./gradlew clean build -x test --no-daemon` → **BUILD SUCCESSFUL** (Gradle 3.5.1이 자체적으로 다운로드되어 실행됨), `bootRepackage` 태스크로 실행 가능한 fat jar 생성 확인
- 동일하게 resolve된 하위 버전(Spring Framework 4.3.25.RELEASE, Tomcat 8.5.43, Jackson 2.8.11.3)도 Gradle 4.10.3때와 100% 동일 — BOM이 고정하므로 Gradle 버전과 무관함을 재확인
- `Dockerfile` 전체 빌드(멀티스테이지) → 컨테이너 기동 → `GET /healthz` 200 재확인
- 빌드 스타일: Spring Boot 1.5.x 시대의 classic `buildscript{} + apply plugin:` 방식(2.x 이후 표준인 `plugins{}` DSL 대신) — `compile`/`testCompile` configuration이 Gradle 3.5.1에서 정상 동작(애초에 이 configuration들은 이 시대 Gradle의 기본값)

**최종 확정: Gradle 3.5.1** (Spring Boot 1.5.22 공식 System Requirements 지원 범위 내 최신 버전).

Source: [Spring Boot 1.5.22 System Requirements](https://docs.spring.io/spring-boot/docs/1.5.22.RELEASE/reference/html/getting-started-system-requirements.html), [Spring Boot 1.5.9 System Requirements (교차 확인)](https://docs.spring.io/spring-boot/docs/1.5.9.RELEASE/reference/html/getting-started-system-requirements.html), [Gradle Compatibility Matrix](https://docs.gradle.org/current/userguide/compatibility.html), [Gradle 공식 릴리스 목록](https://gradle.org/releases/), [Gradle 3.5.1 Release Notes](https://docs.gradle.org/3.5.1/release-notes.html)

## 4. mock-llm-fastapi

**아래 버전은 Docker 이미지 빌드/실행으로 실측 확정된 값이다 (2026-08-10, `docker build` + `pip freeze` 실행 결과).
로컬 venv(Python 3.9.6, 시스템 기본 Python)로도 기능 검증을 했지만, 그 결과는 개발 중 빠른 반복(iteration)용일 뿐
공식 Benchmark 근거로 쓰지 않는다 — 공식 Benchmark는 항상 아래 Docker 이미지만 사용한다.**

| 항목 | 결정 | 근거 |
|---|---|---|
| Base Image | `python:3.12-slim` (linux/arm64/v8), digest `sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36`로 pin | Dockerfile에 digest까지 고정해 재현성 확보. 컨테이너 내부 `uname -m` = `aarch64` 확인, 에뮬레이션 없이 host(arm64)와 동일 architecture로 빌드/실행됨 |
| Python (컨테이너 내부) | 3.12.13 | `docker exec ... python --version`으로 실측 |
| FastAPI | **0.141.1** | `pip freeze` 실측. 0.135 미만이 아니므로 내장 `EventSourceResponse`도 가능했으나, 아래 이유로 수동 포맷팅 유지 |
| uvicorn | **0.52.1** (`[standard]` extra 포함 — uvloop, httptools, websockets 등) | `pip freeze` 실측 |
| pydantic | **2.13.4** | `pip freeze` 실측 |
| prometheus-client | **0.26.0** | `pip freeze` 실측 |
| SSE 구현 | **`starlette.responses.StreamingResponse` + 수동 `event:`/`data:` 포맷팅** (외부 SSE 라이브러리 미사용) | `delta`/`final`/`error` 세 종류의 event type과 forced-disconnect(제너레이터를 이벤트 없이 그냥 종료) 같은 비표준 동작을 정밀 제어하려면 래퍼 라이브러리보다 수동 포맷팅이 더 명확하고 의존성도 줄어듦 |
| Metrics | `prometheus-client` (Python 공식 클라이언트) | FastAPI/Starlette 미들웨어로 간단히 노출 가능. `/metrics`, Docker `HEALTHCHECK`(`/healthz`) 모두 컨테이너에서 정상 동작(`healthy`) 확인 |

로컬 venv(Python 3.9.6)에서는 FastAPI 0.128.8 / uvicorn 0.39.0으로 다르게 resolve되었음 — 이것이 "로컬 검증"과 "공식 Benchmark용 Docker 검증"을 반드시 구분해야 하는 이유를 보여주는 실제 사례.

Source: [FastAPI SSE tutorial](https://fastapi.tiangolo.com/tutorial/server-sent-events/)

## 5. 재확인 필요 항목 체크리스트 (구현 진행 중 채워야 함)

- [x] Spring Boot 1.5.22가 실제 resolve하는 Spring Framework 정확 버전 — **4.3.25.RELEASE** (Tomcat 8.5.43, Jackson 2.8.11.3)
- [x] Gradle 3.5.1(Spring Boot 1.5.22 공식 지원 범위 내) + Spring Boot 1.5.22 플러그인 실제 빌드 성공 여부 — **성공** (arm64 native, `eclipse-temurin:8-jdk-jammy`). 최초에는 비공식 근거로 Gradle 4.10.3을 썼다가, 공식 System Requirements 확인 후 3.5.1로 정정
- [x] FastAPI/uvicorn/pydantic/prometheus-client 버전 — **Docker 이미지(python:3.12-slim, linux/arm64)에서 실측 및 pin 완료**: FastAPI 0.141.1, uvicorn 0.52.1, pydantic 2.13.4, prometheus-client 0.26.0 (`requirements.txt` 참고). 로컬 venv(Python 3.9.6)는 FastAPI 0.128.8/uvicorn 0.39.0로 다르게 resolve됨 — 공식 Benchmark는 Docker 버전만 사용
- [x] Python base image 정확 태그/digest — `python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36` (linux/arm64), Dockerfile에 digest pin 완료
- [ ] eclipse-temurin:8 이미지의 정확 태그(예: `8u492-b08-jdk-jammy`) 및 arm64 manifest 존재 여부 `docker manifest inspect`로 실측 (다음 단위: gateway-mvc-executor-java8 구현 시 검증 예정)

## 6. Phase 2 — Java 21 / Spring Boot 4.x 버전 (Unit 1→2, 실측 확정 + exact pin)

Phase 1과 동일 원칙(공식 문서 우선, 추측 금지, 실제 빌드로 검증)을 Phase 2에도 적용한다. 아래는
**Phase 2 Unit 1(2026-08-16)에서 실제 Docker 빌드/`java -version`/`./gradlew dependencies`로 확정한
값**이며, **Unit 2에서 JDK 이미지를 floating tag에서 exact tag로 재pin**했다.

| 항목 | 조사 시점 후보 | **실측 확정값** | 확인 방법 |
|---|---|---|---|
| Gateway build/runtime JDK | Temurin 21, 후보 patch `21.0.12` | **Eclipse Temurin 21.0.11+10-LTS** — **후보와 다름, 실측이 우선** | `docker run eclipse-temurin:21.0.11_10-jdk-jammy java -version` |
| Spring Boot | `4.1.0` | **4.1.0** (후보와 일치) | `./gradlew dependencies --configuration compileClasspath` |
| Spring Framework | `7.0.8` | **7.0.8** (후보와 일치) | 위와 동일 — `org.springframework:spring-core:7.0.8`/`spring-web:7.0.8`/`spring-webmvc:7.0.8` 실측 |
| Embedded Tomcat | `11.0.22` | **11.0.22** (후보와 일치) | 위와 동일 — `org.apache.tomcat.embed:tomcat-embed-core:11.0.22` |
| Jakarta Servlet | `6.1` 베이스라인 | Tomcat 11.0.22에 번들 — `ChatController`가 `jakarta.servlet.AsyncContext` 등을 직접 import해 실제 컴파일 성공으로 간접 확인(`gradle compileJava` BUILD SUCCESSFUL) | 위 컴파일 성공 자체가 증거 |
| Gradle Wrapper | `8.14 이상 또는 9.x` | **8.14** | `./gradlew --version`, 실제 `./gradlew clean build -x test` BUILD SUCCESSFUL |
| Virtual Thread 최소 JDK | 21 | 21(Unit 1~2는 아직 VT 코드 없음 — Unit 3/4에서 실측 재확인) | Spring Boot 4.0.0 공식 문서 |

**JDK patch 불일치 사례(Unit 1에서 발견)**: WebSearch 조사(Unit 1 착수 전)는 `21.0.12`(2026-08-04
공개, Adoptium 뉴스)를 후보로 제시했지만, Unit 1 착수 시점에 pull한 floating tag(`eclipse-temurin:
21-jdk-jammy`/`21-jre-jammy`) 이미지는 `21.0.11+10-LTS`를 담고 있었다.

**Unit 2 exact pin(중요한 정정)**: Unit 1의 Dockerfile은 이 실측값(21.0.11)을 문서에 "확정"이라고
적었지만 **Dockerfile 자체는 여전히 floating tag(`21-jdk-jammy`/`21-jre-jammy`)를 쓰고 있었다** — 즉
문서와 실제 pin이 불일치했다(다음 `docker build`에서 Temurin이 21.0.12를 배포하면 조용히 patch가
바뀔 수 있는 상태). Unit 2에서 **exact tag로 재pin**했다:

```
eclipse-temurin:21.0.11_10-jdk-jammy   (build stage)
eclipse-temurin:21.0.11_10-jre-jammy   (runtime stage)
```

`docker manifest inspect`로 두 태그 모두 linux/arm64/v8 manifest 존재를 사전 확인했고, digest는:

| 이미지 | Repo Digest |
|---|---|
| `eclipse-temurin:21.0.11_10-jdk-jammy` | `sha256:55fb9bf738f5d9b4a6c01b39337e3070d3e27370dd3c478fd1d5d3cd2233c6d8` |
| `eclipse-temurin:21.0.11_10-jre-jammy` | `sha256:3097cbbebb7d490494a98aed2301f284b38f79eba158eef098c6fc8c8af11c23` |

(이 digest는 Unit 1이 pull했던 floating tag 시점의 digest와 **동일**했다 — 즉 조사~Unit 2 사이에는
아직 실제 patch 갱신이 없었다는 뜻이지만, floating tag를 계속 썼다면 향후 언제든 바뀔 수 있었다.)
exact tag 재pin 후 `java -version`/`uname -m`을 재확인해 21.0.11+10-LTS·arm64(aarch64)가 그대로임을
검증했다 — Dockerfile 변경이 실제 JDK를 바꾸지 않았음을 재확인.

**Gradle wrapper 생성 환경과 Gateway runtime JDK는 서로 다른 것이다(Unit 2 정정)**: `gradle:
8.14.0-jdk21`(wrapper 생성/검증용 Docker 이미지)의 Launcher JVM은 `Eclipse Adoptium 21.0.7+6-LTS`다
— 이것은 **Gradle Wrapper 자체를 생성한 일회성 환경**의 JVM이며 Phase 2 Gateway가 실행되는 JVM이
아니다. 명확히 구분해 기록한다:

| 구분 | 값 | 비고 |
|---|---|---|
| Gradle Wrapper 버전 | 8.14 | `gradle/wrapper/gradle-wrapper.properties`에 고정, 이후 모든 빌드가 이 버전으로 실행 |
| Wrapper 생성 환경(참고 정보, formal metadata 아님) | `gradle:8.14.0-jdk21` 이미지, Launcher JVM Eclipse Adoptium 21.0.7+6-LTS | wrapper jar/스크립트를 한 번 생성하는 데만 쓰인 일회성 컨테이너 — Gateway 빌드/실행과 무관 |
| **Gateway build JDK**(formal) | Eclipse Temurin **21.0.11+10-LTS** | `Dockerfile` build stage(`FROM eclipse-temurin:21.0.11_10-jdk-jammy`), `./gradlew clean build`가 실제 실행되는 JVM |
| **Gateway runtime JDK**(formal) | Eclipse Temurin **21.0.11+10-LTS** | `Dockerfile` runtime stage(`FROM eclipse-temurin:21.0.11_10-jre-jammy`), 컨테이너 기동 시 `java -jar app.jar`를 실행하는 JVM |

Formal environment.json에는 build/runtime JDK(21.0.11+10-LTS)만 기록한다 — wrapper 생성 환경의
21.0.7은 재현성/문제 진단용 참고 정보일 뿐 Phase 2 결과에 영향을 주는 값이 아니다.

**Status**: Unit 2에서 exact pin 완료. `gateway-mvc-java21/Dockerfile`이 이 버전들을 그대로 사용한다.

**중요한 발견 — Phase 2를 JDK 21 하나로 한정하는 근거**: **[JEP 491: Synchronize Virtual Threads
without Pinning](https://openjdk.org/jeps/491)**이 JDK 24에서 `synchronized` 블록/메서드로 인한
virtual thread pinning 문제 자체를 해소했다(monitor를 carrier가 아닌 virtual thread에 결속). Spring
공식 문서도 이를 근거로 pinning 처리 개선을 위해 **JDK 24 이상을 권장**한다. 즉 **JDK 21에서
관측되는 pinning 양상은 JDK 24+에서는 재현되지 않을 수 있다** — 이것이 `docs/test-plan/
phase2-design.md` §6이 "Phase 2 결과를 모든 최신 JDK로 일반화하지 않는다"고 명시하는 근거이며,
추측이 아니라 이 조사에서 확인된 사실이다. Phase 2는 현재 널리 배포된 LTS(21)의 실제 동작을 측정하는
것이 목적이므로 21을 유지하고, JDK 24+ 비교는 Phase 2 범위 밖의 후속 과제로 남긴다.
