# Slack DM 改用官方 Thinking Steps streaming 呈現 Thinking Step，取代單次 say() + 獨立 setStatus

Slack DM 原本用 `assistant_threads_setStatus` 顯示單行「思考中…」文字、Turn 結束後才用一次性 `say()` 發送完整回覆，使用者看不到 Assistant 呼叫了哪些 Tool、進度如何。改為使用 Slack 官方 [Thinking Steps](https://slack.dev/slack-thinking-steps-ai-agents/) 機制：每個 Turn 開始時呼叫 `chat.startStream` 開啟一則訊息，收到 `on_tool_start`/`on_tool_end` 事件時用 `TaskUpdateChunk` 逐步 append/更新 Task Card（`pending → in_progress → complete`/`error`），最後把回覆文字併入同一則訊息、呼叫 `chat.stopStream` 收尾——思考過程與最終答案是同一則訊息隨時間展開，而非兩則獨立訊息。

這個決定把訊息的生命週期從「一次性發送」變成「開啟－逐步更新－收尾」，取消重來（見 ADR `0003`）與例外處理都必須跟著這個生命週期走：中斷 Turn 前要先把卡在 `in_progress` 的 Task Card 標記為 `error`、呼叫 `stop()` 收尾舊 stream，否則會留下永遠停在「進行中」的孤兒訊息；例外發生時同樣要 `stop()` 收尾，不能改發一則獨立的錯誤訊息。曾考慮過維持原本「單行狀態文字 + 事後 `context` block 標記」的簡單做法，但只能顯示一句話、看不到逐步進度，也無法顯示 Task Card 的 `output`/`sources`，故不採用。呈現模式選 Timeline（逐項累積顯示）而非 Plan（先列出完整計畫再打勾），因為這個 Assistant 的 graph 是逐步決定下一步呼叫哪個 Tool，沒有「先規劃再執行」的上游步驟可呈現。
