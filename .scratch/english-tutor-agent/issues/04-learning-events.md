# 04 — 記錄錯誤與學習事件

Type: task
Status: needs-triage
Priority: P0

**What to build:** 將練習結果、常犯錯誤、單字與文法點保存為可查詢且以使用者隔離的學習資料。

**Blocked by:** 01 — 建立學習者模型; 03 — 建立練習題與答案評估

## Acceptance criteria

- [ ] 相同使用者的學習事件可跨 Thread 查詢
- [ ] 不同使用者無法讀取彼此的資料
- [ ] 重複錯誤能累計而非無限建立重複資料
- [ ] CRUD 與 user isolation 有 unit test