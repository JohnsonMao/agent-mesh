# 透過通用 execute_command Tool 與 playwright-cli Skill 整合瀏覽器自動化能力

## 脈絡

為了讓 Assistant 以及第三方 AI Agent（例如 Claude Code、VS Code Copilot 等）具備網頁操作與自動化能力，需要評估如何封裝瀏覽器控制介面。同時需考量多步驟操作、人機審核（Human-in-the-loop / LangGraph interrupt）的 Session 連線保活，以及未來跨環境與 Docker 容器部署的一致性。

曾考慮過的替代方案：
1. **直接使用 Python Playwright SDK 寫死多個專屬 Tool**：
   優點是在記憶體中控制 Page 物件更直接；缺點是與 Python process 深度綁定，第三方外部 Agent 無法直接跨 Process 復用相同的操作介面，且工具定義數量會膨脹。
2. **包裝成 Playwright MCP Server**：
   優點是有標準協議；但 CLI 形式搭配 Skill 更加輕量與 Token-efficient，且微軟官方已有維護良好的 `playwright-cli` 與現成 `SKILL.md`。

## 決定

1. **Tool 職責**：
   提供泛用、具備副作用的 `execute_command`（或指令執行 Tool），專門負責在系統環境（或容器）內執行命令列指令，供 Assistant 及外部 Agent 調用。
2. **Skill 職責**：
   引入微軟官方 `playwright-cli` 的 Skill 文件，放置於 `skills/playwright-cli/` 目錄。透過漸進式載入（Progressive Loading）提供瀏覽器操作的指示知識（SOP、Snapshot refs 操作流程、常用指令集）。
3. **Session 與 Interrupt 生命週期**：
   依賴 `playwright-cli` 內建的 background daemon / session 機制（`-s=<session_name>`）與閒置 TTL 回收機制。當 LangGraph 流程進入 `interrupt()` 等待使用者授權時，背景瀏覽器 session 保持活躍，resume 後可直接接續操作。
4. **環境與部署**：
   在開發環境與容器中預裝 Node.js 與 `@playwright/cli` / browser 核心依賴，確保本機與 Docker 執行環境一致。

## 後果

- **優點**：
  - 符合 [CONTEXT.md](../../CONTEXT.md) 的 Tool（外部動作副作用）與 Skill（指示知識）職責切分。
  - 介面高度通用，第三方 AI Agent 也能以相同的 CLI 指令與 Skill 協同操作。
  - Token 消耗低：透過 `playwright-cli snapshot` 的 refs 機制互動，避免將過於龐大的 Accessibility Tree 或全量 DOM 強制塞進 prompt。
- **代價與限制**：
  - 依賴底層系統或容器安裝 Node.js 與 Playwright 瀏覽器二進位檔。
  - 需要透過 timeout 與 daemon 自身機制防範背景 Session 洩漏。
