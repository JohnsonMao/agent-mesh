# 05 — 加入間隔複習排程

Type: task
Status: needs-triage
Priority: P1

**What to build:** 根據學習者的答題結果，安排單字、文法點與錯誤的下一次複習，並提供到期項目與手動操作。

**Blocked by:** 04 — 記錄錯誤與學習事件

## Acceptance criteria

- [ ] 答錯會縮短下一次複習間隔，答對會逐步拉長
- [ ] due query 只回傳目前到期的項目
- [ ] 排程計算為純函式，可用固定時間測試
- [ ] 時區與時間格式有明確規則