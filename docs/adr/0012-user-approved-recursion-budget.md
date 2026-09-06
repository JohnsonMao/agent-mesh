# 推理步數預算耗盡時暫停 Turn，讓使用者以 Slack 按鈕決定是否繼續

Tool-calling graph 在 20 個 superstep 時不等待 `GraphRecursionError`，而是持久化為 Paused Turn，結束當前 stream 並附上「繼續處理／停止」按鈕。選擇繼續後，以同一 Conversation 的 checkpoint 恢復並新增 Thinking Stream；每次增加 20 步，單一 Turn 的絕對上限為 60 步。按鈕僅對允許的使用者與當前暫停 Turn 有效，15 分鐘後失效；停止、逾期或新的使用者訊息都會取消該 Paused Turn。

這避免單純提高 recursion limit 將無終止的工具循環延長成更昂貴的失敗，也不會把未完成內容偽裝成成功回覆。以按鈕而非自然語言指令承接決策，讓 resume 意圖不會和新的 Conversation 輸入混淆。
