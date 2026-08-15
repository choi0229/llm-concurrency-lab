# Unit 6.6 — A's Old Rerun (pre-`PRE_ALLOCATED_VUS` fix) — Discarded

**이 디렉터리의 raw run 데이터는 Git에 커밋하지 않는다**(`.gitignore` 참고, 약 14MB) — 이 README만
버전 관리된다.

## 폐기 이유

Attempt 3(공식 채택)에서 Config A만 모든 부하 수준(R2/R5/R12)에서 k6 `dropped_iterations`가
2.5~2.8% 발생(B/E는 0%). `vus_max`가 `MAX_VUS`의 15~20%에 불과해 VU 개수 상한 문제가 아니었고,
A의 응답 지연 분산이 유난히 넓어 k6 constant-arrival-rate 스케줄러의 VU spin-up 속도가 못 따라간
것으로 진단했다. `PRE_ALLOCATED_VUS=100→800`으로 올려 재검증(720/720 iteration 전부 시작,
dropped_iterations=0 확인) 후 **A의 formal run 7개를 재실행** — 이 디렉터리는 그 **수정 전** rerun
결과이며 공식 결과로 사용하지 않는다.

## 공식 문서에서의 기록 위치

- `docs/test-results/phase1/unit6-formal-benchmark-results.md` §6, §10 "Experiment Integrity" —
  Unit 6.6
- `docs/test-results/phase1/phase1-final-report.md` §18 "Experiment Integrity"
