# ADR: Phase 2 모듈 구조 — 단일 모듈 + Executor Bean 전환

Status: Accepted (Phase 2 Unit 1)
Date: 2026-08-16

## 문제

Phase 2는 P-E(Platform `ThreadPoolExecutor`), VT-Limited, VT-Unlimited 세 구성을 비교한다
(`docs/test-plan/phase2-design.md`). 이 세 구성이 Controller/AsyncContext lifecycle/MockLlmClient/
metric naming/error response/timeout semantics를 **정말로 동일하게** 유지하는지를 어떻게 구조적으로
보장할 것인가.

## 검토한 대안

| 대안 | 장점 | 단점 |
|---|---|---|
| **A. 독립 모듈 3개**(`gateway-p-e`, `gateway-vt-limited`, `gateway-vt-unlimited`) | 모듈별로 완전히 독립된 배포 단위, 실수로 다른 모듈에 영향을 주지 않음 | `ChatController`/`AsyncRequestState`/`GatewayMetrics`/`MockLlmClient`를 3번 복붙하거나 별도 공유 라이브러리 모듈이 필요 — 복붙하면 코드 drift(한쪽만 고치고 잊음) 위험이 "Chat Executor만 바꾼다"는 Phase 2의 핵심 비교 원칙을 구조적으로 깨뜨릴 수 있다 |
| **B. 단일 모듈 + `THREAD_MODE` 환경변수로 Executor Bean 전환**(채택) | Controller/lifecycle/metric 코드가 물리적으로 하나뿐이라 drift가 원천적으로 불가능 — "같은 코드, 다른 executor Bean"이 컴파일 타임에 보장됨 | 실행 시점에 하나의 config만 활성화되므로 3개를 동시에 띄워 비교하려면 컨테이너를 3번 다르게 기동해야 함(어차피 Phase 1도 config마다 컨테이너를 재생성했으므로 새로운 제약이 아님) |
| **C. 단일 모듈 + 커스텀 `ChatTaskExecutor` 추상 인터페이스** | Executor 구현체를 완전히 자유롭게 설계 가능 | Java 21의 `ThreadPoolExecutor`와 `Executors.newVirtualThreadPerTaskExecutor()`가 **이미 둘 다 `java.util.concurrent.ExecutorService`를 구현**하므로, 이 이상의 추상화는 이 프로젝트에 불필요한 계층이다(과도한 추상화) |

## 결정: B(단일 모듈) + JDK 표준 `ExecutorService` 타입(신규 인터페이스 없음)

- 모듈은 하나(`gateway-mvc-java21/`)로 유지한다.
- `ChatController`는 `ThreadPoolExecutor`(Phase 1의 구체 타입)가 아니라 **`java.util.concurrent.
  ExecutorService`**를 주입받는다 — `ThreadPoolExecutor`와 `Executors.newVirtualThreadPerTaskExecutor()`
  가 만드는 `ExecutorService`(내부적으로 `ThreadPerTaskExecutor`)가 모두 이 인터페이스를 구현하므로,
  이 이상의 커스텀 인터페이스(`ChatTaskExecutor` 등)를 새로 만들지 않는다 — JDK가 이미 제공하는
  공통 타입을 재사용하는 것이 가장 단순하다.
- `THREAD_MODE` 환경변수(`PLATFORM` / `VIRTUAL_LIMITED` / `VIRTUAL_UNLIMITED`)로 어떤 `ExecutorService`
  Bean이 활성화되는지 전환한다 — Spring `@ConditionalOnProperty` 또는 단순 `if/switch` 기반 `@Bean`
  팩토리 메서드로 구현한다(Unit 2~4에서 실제 구현).
- **Unit 1 범위**: `THREAD_MODE` 분기 자체는 아직 만들지 않는다 — 골격 검증용 임시 executor 하나만
  둔다(§ 아래 "Unit 1 placeholder" 참고). Executor별 계측(queue wait, caller-runs, virtual task
  active 등)도 아직 만들지 않는다 — 공통 request/outcome 계측만 재사용한다.

## Unit 1 Placeholder Executor

Unit 1은 골격의 end-to-end 동작(healthz, SSE relay, /metrics)만 검증하므로, 정식 P-E/VT 설정이
아닌 **임시 executor**(`Executors.newCachedThreadPool()`, 코드에 "Unit 1 skeleton-only, Unit 2에서
교체" 주석 명시)를 둔다. 이 placeholder는 Unit 2에서 `THREAD_MODE=PLATFORM`(P-E: core=10/max=50/
SynchronousQueue/AbortPolicy)으로 교체되고, Unit 3/4에서 VT-Limited/VT-Unlimited가 추가된다.

## 이 결정이 지키는 것

`docs/test-plan/phase2-design.md` §1의 비교 원칙("Chat Executor 하나만 바꾼다")이 코드 구조로
강제된다 — Controller/AsyncContext lifecycle/MockLlmClient/공통 metric은 물리적으로 하나의 소스
파일 집합이라 세 구성 사이에 절대 갈라지지 않는다.

## Unit 3 추가 결정: `ChatTaskSubmitter` — mode-aware submission을 위한 최소 seam

Unit 2까지는 `ChatController`가 `chatExecutor.execute(new InstrumentedRunnable(...))`를 직접
호출했다. P-E와 VT-Limited는 admission 결정 시점·방식이 근본적으로 다르다 — P-E는
`ThreadPoolExecutor.execute()`가 동기적으로 accept/reject를 결정하지만(`RejectedExecutionException`),
VT-Limited는 그보다 먼저 `Semaphore.tryAcquire()`로 admission을 결정하고 성공한 경우에만 virtual
thread를 만든다. 이 차이를 `ChatController` 안에 `if (threadMode == ...)`로 직접 넣으면 조건문이
Controller 전체에 퍼지므로, 대신 **단일 메서드 인터페이스** `ChatTaskSubmitter.trySubmit(Runnable)
-> boolean`을 도입했다 — `PlatformTaskSubmitter`(P-E)와 `VirtualLimitedTaskSubmitter`(VT-Limited)
두 구현체만 있고, `ChatController`는 이 인터페이스 하나만 알며 THREAD_MODE를 전혀 알지 못한다.

이것이 "과도한 추상화"가 아니라고 판단한 근거: 메서드가 하나뿐이고, 두 구현체 각각이 자기
mode-specific instrumentation(P-E는 `executor_task_start_delay_seconds`, VT-Limited는
`chat_virtual_tasks_*` + permit lifecycle)을 스스로 캡슐화해 `ChatController`가 그 세부사항을
전혀 알 필요가 없어졌다 — Controller의 rejected-path 처리(503 + `outcome="rejected"`)는 두 모드
모두에서 완전히 동일한 코드로 남는다.

`ExecutorService chatExecutor` bean도 계속 존재하지만(Unit 1의 결정 유지), `ChatController`는
더 이상 이를 직접 주입받지 않는다 — `ChatTaskSubmitter` 구현체 내부에서만 쓰인다.
