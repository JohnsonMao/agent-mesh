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

**Context**：
一次 graph invocation 的外部執行脈絡，提供該次處理所需、但不屬於對話訊息歷史的身份或環境資料。Context 不會隨 Turn 演進，也不應被 checkpoint 持久化。本專案的 `user_id` 屬於 Context；`thread_id` 是 Thread 的識別值。

**LearnerProfile**：
一位學習者跨多個 Thread 共用的結構化學習偏好與程度資料，由 `user_id` 識別其擁有者。它只描述學習者本身，不包含對話 checkpoint、長期記憶或學習事件。
_Avoid_: learner state, language profile

**Learner level**：
學習者的 CEFR 程度，允許 `unknown` 表示尚未知道，而不是推定為初級。
_Avoid_: skill level, difficulty

**Learning goal**：
學習者明確希望達成的學習結果，可同時存在多個目標。
_Avoid_: task, preference

**Correction preference**：
學習者對錯誤何時及如何被指出的偏好，例如即時糾正或 Turn 結束時整理。
_Avoid_: feedback mode

**Language tag**：
描述語言及其區域變體的 BCP-47 風格標籤，例如 `en` 或 `zh-TW`；不等同於學習者的程度或教學模式。
_Avoid_: language code

**Profile update**：
針對 LearnerProfile 指定欄位的原子變更；未指定欄位保持原值，任何欄位驗證失敗時整次變更不生效。
_Avoid_: profile overwrite

**Profile existence**：
表示 `user_id` 是否有明確保存的 LearnerProfile；不存在時的預設值只供讀取，不會因讀取而建立資料。
_Avoid_: initialized profile
