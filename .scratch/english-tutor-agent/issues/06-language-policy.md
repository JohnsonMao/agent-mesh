# 06 — 實作英文與繁中語言策略

Type: task
Status: needs-triage
Priority: P1

**What to build:** 讓回覆語言依 LearnerProfile 的 level、teaching mode 與使用者偏好調整，而不是固定使用繁體中文。

**Blocked by:** 01 — 建立學習者模型; 02 — 支援教學模式與策略

## Acceptance criteria

- [ ] 語言策略是結構化設定，可在測試中直接驗證
- [ ] system prompt 不再與動態語言策略互相衝突
- [ ] 回覆中包含糾正時，英文原文與繁中解釋格式穩定
- [ ] 為初、中、高程度各建立至少一個測試情境