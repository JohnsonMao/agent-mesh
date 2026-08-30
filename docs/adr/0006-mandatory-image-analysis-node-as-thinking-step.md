 # 圖片解析改用強制執行的處理步驟，而非 Assistant 自行判斷的 Tool，並擴大 Thinking Step 定義涵蓋它

使用者在 Slack 傳圖片時，圖片分析改成 Turn 一開始就強制執行的處理步驟：偵測到訊息帶原生圖片檔案上傳，就無條件呼叫本來已支援視覺輸入的模型做針對性分析（參考使用者當輪文字當作分析方向），再用分析結果文字取代掉該訊息裡的原始圖片內容，才交給主 Agent 邏輯處理。原始圖片資料只在這個處理步驟內短暫存在，從頭到尾不寫進 `MessagesState`，因此也不會進入 `data/checkpoints.sqlite` 這個 checkpoint。

這打破了 `CONTEXT.md` 對 `Tool`「呼叫與否由 Assistant 自行判斷，不是每輪都固定執行」的定義——圖片分析不是模型決定要不要看，而是圖片一出現就一定要處理，語意上更接近「輸入前處理」而非「Assistant 主動呼叫的外部能力」。曾考慮過硬是包成一個 Tool、讓圖流程強制呼叫它，這樣可以直接沿用既有 `ToolNode`／`on_tool_start`／`on_tool_end` 的 Task Card streaming 機制，不用改事件監聽邏輯；但這樣會讓 `Tool` 這個詞彙出現「由 Assistant 決定」跟「被迫呼叫」兩種矛盾語意混在一起，對之後讀 `CONTEXT.md` 的人來說更難理解，故不採用，改成獨立於 Tool 之外的處理步驟。

為了讓使用者依然能在 Slack 看到「正在讀圖片」的 Task Card 回饋，`Thinking Step` 的定義從「呼叫某個 Tool 這件事本身」放寬為「執行某個可觀察、有明確起訖的中間處理步驟」，`slack_stream.py`/`slack_status.py` 也要額外監聽 graph 的 node-level 事件（不只 `on_tool_start`/`on_tool_end`）才能讓這個步驟一併產生 Task Card。這代表 Thinking Step 不再天生等於「Tool 呼叫」，之後任何新增的強制型處理步驟都可能比照要求要顯示 Task Card，是刻意接受、之後會持續影響事件監聽範圍的先例。

另外考慮過讓分析結果額外插入一則新訊息（保留原始 `HumanMessage` 含圖片不動），這樣圖片理論上還能在同一輪被主 Agent 直接「重新看一次」；但這樣圖片資料還是會被永久存進 checkpoint，且需要引入新的訊息型別。權衡「歷史圖片可回溯」與「checkpoint 不無限膨脹、每輪不重複燒圖片 token」，選擇後者，接受「原始圖片一旦被這個步驟處理過就不能再被回頭查看」的代價。
