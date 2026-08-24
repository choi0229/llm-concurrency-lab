# Unit 6 Attempt 2 — Discarded

**이 디렉터리의 raw run 데이터는 Git에 커밋하지 않는다**(`.gitignore` 참고, 약 31MB) — 이 README만
버전 관리된다.

## 폐기 이유

Attempt 1의 clock-domain 수정 후 재실행. 21/21 완료, 그중 2개 run(`A-LOW-run1`, `E-LOW-run1`)이
다시 invariant 위반 — post-run clock drift는 0~2초로 정상이었으므로 clock-domain은 진짜 원인이
아니었다. 수동 재현 결과, `docker compose up --force-recreate`가 **Prometheus의 익명 volume(TSDB
데이터)을 기본적으로 재사용**해 config 전환 후에도 이전 config의 값이 최대 5분간 그대로 서빙됨을
확인 — 이 문제가 invariant를 통과한 다른 run에서도 더 미묘하게 발생했을 가능성을 배제할 수 없어
**전체 21 run을 폐기**하고, `--renew-anon-volumes`(`-V`)를 추가한 뒤 처음부터 재실행했다(→
Attempt 3, 공식 채택).

## 공식 문서에서의 기록 위치

- `docs/test-results/phase1/unit6-formal-benchmark-results.md` §10 "Experiment Integrity" — Attempt 2
- `docs/test-results/phase1/unit6-benchmark-plan.md` §9-1
- `docs/test-results/phase1/phase1-final-report.md` §18 "Experiment Integrity"
