# Personal AI Assistant

本地大模型驅動的個人 AI 助理，以持續累積的長期記憶為核心，優先聚焦純被動對話與練習 AI agent 相關技術（tool calling、observability），暫不處理筆記問答或待辦提醒。

## Language

**Assistant（助理）**:
單一供個人使用的 AI agent 實例，透過 CLI 與使用者互動，推理由本機執行的模型驅動。純被動運作，不主動觸發任何背景動作。
_Avoid_: Agent（太籠統，容易與 sub-agent/多 agent 協作概念混淆）、Bot

**Interface（介面）**:
使用者與 Assistant 互動的管道，例如 CLI、Slack。每個 Interface 各自維護獨立的 Conversation，但共用同一份 Memory。
_Avoid_: Channel（與 Slack 自己的 channel 概念混淆）、Client

**Conversation（對話）**:
使用者與 Assistant 之間一段持續累積的互動歷史，依 Interface 各自獨立維護；同一個 Interface 底下也可能同時存在多條（例如 Slack 以 Slack thread 為切分單位）。是 Assistant 短期上下文的來源，與跨 Conversation 都能查回的 Memory 不同。
_Avoid_: Thread（與 Slack 自己的 thread 概念容易混淆，指稱 Conversation 時避免用這個字）, Session

**Memory（記憶）**:
Assistant 判斷值得保留時，主動呼叫工具顯式存下的長期事實，可在未來任何一輪 Conversation 中被查回。與 Conversation 的逐輪歷史是不同概念——Memory 是被 Assistant 篩選後保留的內容，不是每輪自動產生的摘要，也不是完整逐字紀錄。
_Avoid_: History（History 屬於 Conversation 的一部分，不是 Memory）

**Tool（工具）**:
Assistant 可主動呼叫、用來完成單純對話無法達成之任務的外部能力（例如查詢即時資訊）。呼叫與否由 Assistant 自行判斷，不是每輪都固定執行。呼叫會產生真實副作用或對外查詢結果，這點與 Skill 不同。也包含 Skill 正文指示 Assistant 呼叫的輔助腳本執行（例如 `run_skill_script`）——即使腳本本身放在某個 Skill 的目錄底下，實際會產生副作用的仍是這個 Tool，不是 Skill 自己。
_Avoid_: Function, Action, Skill（Skill 不執行外部動作，只提供指示）

**Skill（技能）**:
開發者預先撰寫、存放在 repo 內的固定文件，內容是 Assistant 完成特定任務所需的額外指示或工作流程知識，本身不對外產生副作用。每個 Skill 都有 `name` 與 `description`，隨時列在 system prompt 中供 Assistant 判斷是否相關；正文（instructions）則等 Assistant 主動載入時才進入 context（漸進式載入）。除了正文，Skill 的目錄底下也可以放 `references/`（延伸文件）、`scripts/`（輔助腳本）等輔助檔案，同樣採漸進式載入——只有正文預設隨載入回傳，輔助檔案要等 Assistant 主動讀取或執行才會進入 context。跨 Interface 共用同一套。
_Avoid_: Tool（Skill 沒有外部副作用；即使 Skill 目錄下的腳本被執行，產生副作用的是 Tool，不是 Skill 本身）, Memory（Skill 是開發者預先撰寫的靜態知識，不是 Assistant 在對話中判斷保留的動態事實）

**Turn（回合）**:
Conversation 內一次使用者輸入到 Assistant 產生對應回覆為止的單位。取消重來（例如使用者在上一個 Turn 還沒回覆完就補充新訊息）是以 Turn 為粒度中斷、重新開始，不影響該 Turn 之前已經存在的 Conversation 歷史。
_Avoid_: Message（Message 是 Turn 的輸入或輸出之一，不是 Turn 本身）, Round

**Thinking Step（思考步驟）**:
Turn 內呼叫某個 Tool 這件事本身，不包含「model 還在生成、尚未呼叫工具」的狀態（那仍算單純的思考中）。是 Assistant 對外呈現執行過程的最小單位。
_Avoid_: Status（Status 是呈現用的文字，Thinking Step 是背後代表的事件本身）
