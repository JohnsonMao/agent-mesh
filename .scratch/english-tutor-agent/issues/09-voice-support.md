# 09 — 加入語音學習能力

Type: task
Status: needs-triage
Priority: P2

**What to build:** 在不影響文字模式的前提下，提供口說練習、語音轉文字、文字轉語音與專門服務提供的發音回饋。

**Blocked by:** 07 — 重構為可恢復的導師 graph

## Acceptance criteria

- [ ] 語音服務不可用時，文字模式仍可正常使用
- [ ] 原始音訊與轉錄資料有保存期限與刪除機制
- [ ] 發音分數由專門語音服務提供，不由 LLM 虛構
- [ ] 語音流程有 mock integration test