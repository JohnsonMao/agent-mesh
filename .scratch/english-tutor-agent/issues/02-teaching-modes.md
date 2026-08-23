# 02 — 支援教學模式與策略

Type: task
Status: needs-triage
Priority: P0

**What to build:** 讓學習者選擇或由 Agent 判斷 conversation、grammar、vocabulary、writing 教學模式，並產生可供後續流程使用的教學策略。

**Blocked by:** 01 — 建立學習者模型

## Acceptance criteria

- [ ] 相同輸入在不同 mode 下會產生可預期的回覆格式
- [ ] 模式判斷失敗時有穩定的預設模式
- [ ] 糾正策略不只存在於 system prompt，而是可測試的 state / schema
- [ ] 關鍵模式路由有 unit test