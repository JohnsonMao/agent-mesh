# 03 — 建立練習題與答案評估

Type: task
Status: needs-triage
Priority: P0

**What to build:** 讓 Agent 產生可解析的結構化練習題，並以明確 schema 評估學習者答案。

**Blocked by:** 01 — 建立學習者模型; 02 — 支援教學模式與策略

## Acceptance criteria

- [ ] LLM 輸出通過 schema validation 才能進入後續流程
- [ ] malformed output 有明確錯誤處理，不會靜默寫入錯誤資料
- [ ] 評估結果能區分 grammar、vocabulary、meaning、naturalness
- [ ] 以 fake model 覆蓋至少一條成功與一條失敗路徑