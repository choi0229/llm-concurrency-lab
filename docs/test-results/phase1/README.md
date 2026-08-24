# Phase 1 Test Results — Index

이 디렉터리는 Phase 1(Baseline: `gateway-mvc-executor-java8`)의 실행 결과를 담는다. Unit 1~4는 개발/구현
중 반복적으로 수동 검증하며 진행했고 그 raw 결과(k6 summary, Prometheus snapshot 등)를 파일로 남기지
않았다 — 이 디렉터리에 결과 파일이 쌓이기 시작하는 것은 Unit 5(Executor Matrix Screening)부터다. Unit
4까지의 결과 중 이후 Benchmark 해석에 직접 영향을 주는 사실만 아래에 소급 기록한다.

## Unit 4-3에서 이월된 사실: RATE=10에서 upstream_failure 11건

Unit 4-3 Screening(RATE=10, `gateway_upstream_failure_total`, 라벨 없는 단일 counter였던 시점) 실행에서
`upstream_failure`가 11건 관찰됐다. 이 11건은 **Mock LLM 장애가 아니라, remaining-budget clamp
(`docs/decisions/timeout-semantics.md`)에 의해 발생한 read timeout**이다 — 즉 `MockLlmClient.openStream()`
호출 시점에 이미 absolute deadline까지 남은 시간(`remainingMs`)이 `CHAT_READ_TIMEOUT_MS`보다 작아서
`effectiveReadTimeout = min(CHAT_READ_TIMEOUT_MS, remainingMs)`이 remainingMs 쪽으로 clamp된 결과, 정상
동작 중이던 Mock LLM으로부터의 응답을 그 clamp된 시간 안에 못 받아 `SocketTimeoutException`이 발생한
것이다. Mock LLM 자체의 실패나 connect 실패가 아니다.

이 결론을 다음 Screening부터 metric 상에서 직접 구분할 수 있도록 `gateway_upstream_failure_total`에
`reason` label(`connect_timeout` / `read_timeout` / `io_error`)을 추가했다 —
`docs/decisions/timeout-semantics.md` 및 Unit 5 착수 전 "Benchmark 전 마지막 Metric 정리" 작업(0-1) 참고.
Unit 4-3 당시에는 이 label이 없어 위 11건이 세 원인 중 무엇인지 metric만으로는 구분할 수 없었고, 이 문서에
사후적으로 원인을 기록하는 것으로 대신한다 — Unit 4-3의 나머지 raw 결과(TTFC, 처리량 등 전체 표)는 별도
파일로 보존되어 있지 않으므로 이 문서에도 재구성해 넣지 않는다.

## 앞으로의 구조

- `docs/test-results/phase1/unit5-executor-matrix-screening.md` — Unit 5 Screening 결과 (본 문서 작성
  시점 기준 다음 커밋에서 추가 예정)
- `docs/test-results/phase1/absolute-deadline-blocking-read.md` — Unit 5 착수 전 0-3 검증 결과
- Unit 6부터는 `docs/test-results/phase1/unit6-formal-benchmark/` 아래 run별 `environment.json` +
  결과 표를 남긴다 (`docs/decisions/monitoring-baseline.md` 섹션 5 형식을 따름).
