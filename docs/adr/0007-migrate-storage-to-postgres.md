# 儲存層全面遷移至 PostgreSQL + pgvector

將對話狀態儲存（Conversation Checkpoints）與長期事實記憶（Long-term Memory Store）全面由本地單機 SQLite（搭配 `sqlite-vec`）遷移至 PostgreSQL（搭配 `pgvector` 擴充套件）。

隨著助理架構擴展與多介面（CLI、Slack 等）並行運作，本地單機 SQLite 檔案模式受限於單機檔案存取與鎖定限制，在多介面與多執行緒環境下無法提供穩定並行的儲存支援。遷移至 PostgreSQL 提供集中式資料管理與原生並行連線支援。

具體決策：
1. **統一儲存後端與驅動**：全面採用 PostgreSQL 作為 Conversation Checkpoints 與 Memory Store 的共用資料庫，並使用 `psycopg[binary,pool]` (v3) 作為驅動。
2. **連線模型分流**：
   - CLI 採用同步連線池（`ConnectionPool`）搭配 `PostgresSaver` 與 `PostgresStore`。
   - Slack App 採用非同步連線池（`AsyncConnectionPool`）搭配 `AsyncPostgresSaver` 與 `AsyncPostgresStore`。
3. **向量搜尋**：Long-term Memory Store 遷移至 PostgreSQL 的 `pgvector` 擴充套件進行向量索引與語意檢索，取代原有的 `sqlite-vec`。
4. **設定簡化**：移除 `CHECKPOINT_DB_PATH` 與 `MEMORY_STORE_PATH`，以單一 `DATABASE_URL` 環境變數統一管理資料庫連線字串。
5. **啟動生命週期**：應用程式啟動時透過 Checkpointer 與 Store 的 `setup()` 自動完成 schema 與 pgvector 擴充套件之冪等初始化。
6. **舊資料策略**：捨棄既有 SQLite 檔案內容，不實施向後相容或舊資料遷移腳本。
7. **測試隔離**：單元測試全面採用 InMemory Checkpointer / Store 與 Mock 隔離實體資料庫，確保離線測試速度與確定性。
