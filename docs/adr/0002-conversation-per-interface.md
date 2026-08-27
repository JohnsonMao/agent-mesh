# Conversation 依 Interface 分流，取代單一對話串

超越：`0001-single-conversation-thread.md`

新增 Slack 作為第二個 Interface 後，「單一持續對話串」的假設不再成立：CLI 與 Slack 是不同的互動情境，把它們的歷史混在同一條 Conversation 反而會互相干擾。改為每個 Interface 各自維護獨立的 Conversation；Slack 進一步以 Slack thread（`thread_ts`）為切分單位，允許同時存在多條 Conversation。CLI 不受影響，維持原本單一固定的 Conversation。

跨 Interface 共用的仍然是長期 Memory（同一個 `user_id`）——只有短期對話歷史（Conversation）依 Interface 分流，Assistant 的長期記憶不會因為換了介面而失憶。

代價：`docs/adr/0001` 描述的模型已不成立，之後若要再檢視「單一 vs 多個對話串」的取捨，應以本 ADR 為準；`0001` 保留作為歷史紀錄，不刪除。
