# 將可觀測能力拆為獨立平台，並以增量更新呈現執行軌跡

Assistant 的維運需要即時查看執行中與已完成的 Execution Trace，但完整頁面每五秒重載會破壞檢視工作流，且把查詢與 UI 綁在 Slack app 會限制可靠性與演進。因此採用 self-hosted Arize Phoenix + PostgreSQL 作為 Agent 專用的 Observability Platform，與 Slack app 分開部署且僅在 localhost／私人內網運作，承接軌跡查詢與儀表板；Assistant 以 OpenTelemetry 相容的 trace/span 資料與可替換的 OTLP exporter adapter 發送追蹤資料。平台 UI 必須在不強制整頁重載的前提下自動呈現執行中的變更；平台內部採用的傳輸協定不由 Assistant 承諾。

## Consequences

這取代 ADR-0009「Viewer 與 Slack Socket Mode 同一 ASGI process」及「第一版不做 WebSocket/SSE」兩項決定，但保留其資料不得離開本機／內網、遮罩敏感資料與 retention 原則。Trace Content 經集中式遮罩後保存 14 天，Trace Metadata 以 allowlist 保存 90 天，原始內容永不送出 Assistant。採直接遷移，不做 dual-write：新平台驗收通過後即成為唯一 UI，舊 viewer 退役且不作為回退路徑；不回填舊 PostgreSQL Trace。第一期不排程平台資料庫備份。平台服務目標為：99% 結束 Trace 60 秒內可查、執行中變更 5 秒內顯示、平台故障不降低 Slack Turn 成功率、每筆持久化 Trace 均可展開詳情。app 直接以 bounded batch OTLP exporter 輸出至 Phoenix，並在手動 Slack Turn root span 下，以 OpenInference／LangGraph instrumentation 建立模型、graph node 與 tool child spans。Phoenix 使用與 Assistant 共用的 PostgreSQL 實例，但擁有獨立 database 與最小權限角色，從不共用 Assistant schema 或資料表。平台的健康檢查、認證與資料保留必須獨立於 Slack Turn 的可用性。追蹤輸出為 non-blocking、best-effort：平台、網路或 exporter 失敗不得中斷 Slack Turn；第一期記錄 diagnostic logs、drop-event metrics、queue saturation 與 exporter failure；任一訊號連續五分鐘超過門檻時，送 Slack 維運頻道告警。Phoenix 初期僅綁 loopback；將來開放私人內網時，由整合既有 OIDC IdP 的反向代理認證，平台不自行管理帳密，並在開放多人前定義角色與 audit 要求。
