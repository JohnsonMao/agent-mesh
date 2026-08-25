# Personal AI Assistant

本地大模型驅動的個人 AI 助理，以持續累積的長期記憶為核心，優先聚焦純被動對話與練習 AI agent 相關技術（tool calling、observability），暫不處理筆記問答或待辦提醒。

## Language

**Assistant（助理）**:
單一供個人使用的 AI agent 實例，透過 CLI 與使用者互動，推理由本機執行的模型驅動。純被動運作，不主動觸發任何背景動作。
_Avoid_: Agent（太籠統，容易與 sub-agent/多 agent 協作概念混淆）、Bot

**Conversation（對話）**:
使用者與 Assistant 之間單一、持續累積的互動歷史，不切分成多個獨立對話串。是 Assistant 短期上下文的來源。
_Avoid_: Thread, Session

**Memory（記憶）**:
Assistant 判斷值得保留時，主動呼叫工具顯式存下的長期事實，可在未來任何一輪 Conversation 中被查回。與 Conversation 的逐輪歷史是不同概念——Memory 是被 Assistant 篩選後保留的內容，不是每輪自動產生的摘要，也不是完整逐字紀錄。
_Avoid_: History（History 屬於 Conversation 的一部分，不是 Memory）

**Tool（工具）**:
Assistant 可主動呼叫、用來完成單純對話無法達成之任務的外部能力（例如查詢即時資訊）。呼叫與否由 Assistant 自行判斷，不是每輪都固定執行。
_Avoid_: Function, Action
