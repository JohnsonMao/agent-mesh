# 08 — 提供學習進度與資料控制

Type: task
Status: needs-triage
Priority: P1

**What to build:** 讓學習者查看自己的進度與保存資料，並能在不影響其他使用者的前提下控制與刪除資料。

**Blocked by:** 01 — 建立學習者模型; 04 — 記錄錯誤與學習事件; 05 — 加入間隔複習排程

## Acceptance criteria

- [ ] 統計結果可由保存的 learning events 重算
- [ ] 刪除操作不影響其他 `user_id`
- [ ] CLI 至少提供查看與清除資料的入口
- [ ] log 不記錄完整使用者答案或不必要的個人資料