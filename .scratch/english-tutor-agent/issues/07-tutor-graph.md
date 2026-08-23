# 07 — 重構為可恢復的導師 graph

Type: task
Status: needs-triage
Priority: P1

**What to build:** 將目前的 `model -> tools -> model` 流程擴充成可觀測、可測試、可恢復的教學流程，涵蓋對話與練習分支。

**Blocked by:** 01 — 建立學習者模型; 02 — 支援教學模式與策略; 03 — 建立練習題與答案評估; 04 — 記錄錯誤與學習事件; 05 — 加入間隔複習排程; 06 — 實作英文與繁中語言策略

## Acceptance criteria

- [ ] 每個節點有清楚的 state input / output
- [ ] 中斷後能從 checkpoint 恢復，不重複寫入 learning event
- [ ] 每個分支可使用 fake model 測試
- [ ] integration test 覆蓋一次對話、一次練習與一次跨 Thread 複習