# LangGraph Practice Agent

一個練習用的 LangGraph agent：帶工具呼叫、長期記憶（semantic search + 去重合併）、checkpoint 持久化，串接本機 LM Studio 服務。

## Language

**Thread**：
由 `thread_id` 識別的一條多輪對話，`SqliteSaver` 以此為單位持久化訊息歷史。同一個 thread 內的多次 Turn 共用累積的對話上下文。
_Avoid_: 對話、session

**Turn**：
一次 `app.invoke()`，即使用者輸入到 agent 產出最終回覆之間的一次完整處理（可能內含多次模型呼叫與工具呼叫）。是統計資料（token 用量、耗時）小計的聚合單位。
_Avoid_: case、回合（除非上下文已明確）。`tests/` 底下 pytest 的「test case」是另一個獨立概念（一次自動化測試斷言），與 Turn 不是同一件事，不要混用。

**Run**：
`main()` 執行一次的生命週期，橫跨多個 Turn（可能分屬不同 Thread）。是統計資料全域總計的聚合單位。
_Avoid_: session、執行
