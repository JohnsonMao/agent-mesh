# 以 PostgreSQL 自建輕量 Execution Trace Viewer，取代導入 Langfuse

Assistant 的每個 Slack Turn 必須能由 localhost 回看其執行歷程，且追蹤資料不得離開本機／未來的內網；但此專案沒有費用與維運多項觀測基礎設施的預算。因此以既有 PostgreSQL 保存 Execution Trace 與 Execution Step，並由 app 提供維運者使用的唯讀檢視器，不導入 Langfuse 所需的 ClickHouse、Redis/Valkey、Blob Storage 和 worker。

第一版只支援完成後回看與短週期輪詢，不做 WebSocket/SSE；預設完整保存內容 14 天、metadata 90 天，並限制 `execute_command` 的輸出及遮罩名稱含 `TOKEN`、`SECRET`、`PASSWORD`、`KEY` 的值、Bearer token 和 Slack token。每個 Turn 不論完成、失敗或被後續訊息取消，都保留為 `completed`、`failed` 或 `cancelled` 的 Execution Trace。

Viewer 與 Slack Socket Mode 由同一個 Python ASGI 程序在同一個 app service 中啟動，以維持單一服務與單一啟動入口。未來若開放內網，先由反向代理提供既有身分驗證，並維持只有維運者能查看所有軌跡；不在應用程式中自行實作帳號、密碼或逐使用者資料隔離。

Viewer 第一版只提供可依狀態、時間與 Slack Conversation 篩選的列表，以及顯示輸入、輸出、步驟、耗時、錯誤與遮罩後資料的單筆詳情；不做搜尋、圖表、資料匯出或統計儀表板。歷史 OrbStack log 與 Slack 對話不匯入，軌跡從功能上線後才開始累積。為了不引入 worker 或 OS cron，每個 Turn 收尾時以 best-effort 刪除過期資料；若觀測的寫入、清理或 Viewer 自身失敗，必須記錄診斷 log 但不得阻斷 Slack Turn。

Execution Trace／Execution Step 的資料模型保留與 OpenTelemetry trace／span 相容的識別與父子關係（`trace_id`、`step_id`、`parent_step_id`、時間、狀態、attributes、error），但第一版不引入 OpenTelemetry SDK、Collector 或 OTLP exporter。事件直接由既有 graph callback 與 `astream_events()` 持久化至 PostgreSQL；未來需要外接觀測平台時，才在此模型外新增 exporter adapter。
