# Slack 訊息處理改用可取消的回合（cancel-and-restart），因此改用 async

Slack DM 目前用同步的 `graph.invoke()` 處理每一則訊息；但使用者常常在上一個 Turn 還沒回覆完（可能長達 1~3 分鐘）就補充或修改想法，若照原本序列化排隊處理，使用者會先看到一則答非所問的舊回覆，才看到根據補充訊息修正的新回覆，體驗混亂。改為：同一 Conversation 內收到新訊息時，中斷仍在執行的 Turn、改用最新訊息重新起一個 Turn，只回一則整合後的回覆。

這個決定的前提是已查證 LM Studio（llama.cpp server）在連線中斷時會真的停止該次推論並釋放運算資源，`ChatOpenAI.ainvoke()` 的取消也會正確傳遞到底層連線，LangGraph 的 `ainvoke()` 被取消時 checkpoint 不會留下損毀狀態——因此技術上可行。代價是 `slack_app.py` 必須整個改用 async（`AsyncApp` + async 版 `SocketModeHandler` + `graph.ainvoke()`），與 `main.py`（CLI，維持同步 `graph.invoke()`）從此執行模型分岔；曾考慮過的替代方案是單純序列化排隊（不取消、只是讓新訊息等前一輪回覆完再處理），實作更簡單、不需要換成 async，但無法解決「答非所問的舊回覆」這個使用者體感問題，故不採用。
