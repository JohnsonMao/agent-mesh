# 單一持續對話串取代多執行緒對話

> **Status**: superseded by [0002-conversation-per-interface](./0002-conversation-per-interface.md)

個人 AI 助理的 Conversation 採單一、持續累積的歷史，不像典型聊天應用支援多個獨立對話串（thread）。這是因為「個人助理」的核心價值來自跨對話持續累積的上下文與長期記憶，切分成多個獨立 thread 反而會稀釋這個價值；若未來真的需要區分場景，改用 metadata/tag 標記，而不是新增 thread。

代價是：若之後真的需要多執行緒，需要重新設計 checkpoint 的 key 策略，並處理既有單一對話資料的遷移。
