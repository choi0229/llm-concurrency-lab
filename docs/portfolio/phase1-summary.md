# Phase 1 Portfolio Summary — LLM Concurrency Lab

Source: `docs/test-results/phase1/phase1-final-report.md` §23. 이 문서는 그 보고서의 요약을 그대로
재사용 가능한 형태로 분리해 둔 것이다 — 새 주장을 추가하지 않는다.

## README용 (5~8줄)

Java 8 Spring MVC + `ThreadPoolExecutor` 기반 LLM 스트리밍 Gateway의 concurrency 한계를 실측한
Benchmark Lab. Bounded-queue/Abort, SynchronousQueue/Thread-expansion, CallerRuns 세 Executor 전략을
constant-arrival-rate 부하(1~12 req/s)로 24회 반복 측정해, "blocking I/O를 유지하는 한 overload
비용은 사라지지 않고 Queue Wait/Reject/Thread 증가/Caller Thread 전파 중 다른 형태로 이동한다"는
결론을 정량적으로 검증했다. Docker VM 시계 drift, Prometheus anonymous volume 재사용 등 실제로 발생한
측정 인프라 문제를 원인 규명 후 재실행해 신뢰성을 확보했다. 다음 Phase에서는 이 결론을 Java 21 Virtual
Thread와 비교한다.

## 면접 설명용 스크립트 (1~2분)

"LLM 스트리밍 API를 Java 8 Spring MVC로 구현할 때, request thread는 비동기로 반환되지만 실제 upstream
호출을 담당하는 executor의 platform thread는 응답이 끝날 때까지 blocking된다는 문제가 있었습니다. 이
프로젝트는 그 executor를 세 가지 방식으로 구성해봤습니다 — 작은 queue에 즉시 reject하는 방식, queue
없이 thread를 늘리는 방식, queue를 크게 잡고 넘치면 호출자 스레드에서 직접 실행하는 방식입니다. 각
방식을 1~12 req/s 부하로, warm-up 2분에 5분씩 세 번 반복해서 24번 측정했더니, 세 방식 모두 여유 있을
때는 똑같이 동작하지만 capacity를 넘으면 서로 다른 대가를 치른다는 걸 확인했습니다. 하나는 요청을
거절하고, 하나는 스레드와 메모리를 계속 늘리고, 마지막 하나는 reject는 없지만 그 부하를 Tomcat 자체
스레드로 떠넘겨서 원래 격리하려던 목적을 스스로 깨뜨렸습니다. 그리고 이 셋 중 뭐가 '정답'이 아니라,
blocking I/O 구조를 유지하는 한 overload의 비용은 없어지는 게 아니라 항상 어딘가로 옮겨간다는 게 핵심
결론이었습니다. 측정 과정에서 Docker VM 시계가 실제로 어긋나거나 Prometheus가 이전 설정 값을 계속
서빙하는 문제도 발견해서, 원인을 찾아 고치고 다시 실행하는 과정 자체도 기록으로 남겼습니다. 다음
단계는 이 구조를 그대로 두고 Java 21의 Virtual Thread로 바꾸면 이 비용이 실제로 사라지는지 확인하는
겁니다."

## 핵심 수치 (근거: `phase1-final-report.md`)

- Formal dataset: 24 runs (config당 8) + calibration-only 2 runs
- B capacity 추정 10/7.8s≈1.28req/s ↔ 실측 ≈1.33req/s (±4%)
- E capacity 추정 50/7.8s≈6.41req/s ↔ 실측 ≈6.32req/s (±4%)
- HttpURLConnection blocking read: absolute deadline 이후 platform worker가 약 2.24초 추가 점유
- A-R12: caller_runs 2975건, platform_thread_peak 124, RSS peak 310.9MB(세 config 중 최대)
