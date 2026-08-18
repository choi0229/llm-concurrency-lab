# Unit 6 Attempt 1 — Discarded

**이 디렉터리의 raw run 데이터는 Git에 커밋하지 않는다**(`.gitignore` 참고, 약 6.3MB) — 이 README만
버전 관리된다.

## 폐기 이유

21/21 run 완료, 그중 3개 run(`A-OVERLOAD-run1`, `E-LOW-run1`, `A-LOW-run1`)이 accounting invariant
위반. 원인 조사 결과 host 시계와 Docker VM(Prometheus가 실제로 실행되는 clock domain)이 preflight
통과 이후에도 벌어질 수 있음을 확인 — **전체 21 run을 폐기**하고, 모든 측정 타임스탬프를 Docker VM
자신의 시계로 캡처하도록 수정한 뒤 처음부터 재실행했다(→ Attempt 2).

## 공식 문서에서의 기록 위치

자세한 내용과 원인 규명 과정은 다음 canonical 문서를 참고한다(이 폐기 데이터를 결과 계산에 다시
쓰지 않는다):

- `docs/test-results/phase1/unit6-formal-benchmark-results.md` §10 "Experiment Integrity" — Attempt 1
- `docs/test-results/phase1/unit6-benchmark-plan.md` §9
- `docs/test-results/phase1/phase1-final-report.md` §18 "Experiment Integrity"
