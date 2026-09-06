# Slack Turn 以一則 Thinking Stream 加同一 Conversation 的分段訊息遞送長回覆

Slack `chat.stopStream` 的 `markdown_text` 上限是 12,000 字元，既有單一 stream 回覆的 35,000 字元本地限制會被 API 拒絕。每個 Turn 改以一則 Thinking Stream 承載進度與首段回覆，超出的內容在同一 Slack Conversation 用一般訊息依序遞送；每段最多 11,500 字元並優先在段落邊界切分，Task Card output 最多 1,000 字元且只作摘要。

這修訂 ADR-0004「整個 Turn 是同一則 continuously-updating Slack message」的呈現承諾，但保留其 Thinking Step timeline 與 stream 收尾語意。選擇分段而非截斷，避免遺失模型已完成的內容；選擇 11,500 而非剛好 12,000，為段次標示與 Markdown 安全切分保留空間。
