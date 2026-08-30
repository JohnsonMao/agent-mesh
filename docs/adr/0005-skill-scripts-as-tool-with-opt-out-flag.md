# Skill 的 references/scripts 走漸進式載入，腳本執行走 Tool、預設開放並用旗標退場

Skill 原本只有單一 `SKILL.md`，本身不對外產生副作用（見 `CONTEXT.md`）。為了讓 Skill 可以夾帶 `references/*.md`（延伸文件）與 `scripts/*.py`（輔助腳本），新增 `read_skill_resource`（唯讀讀取該 Skill 目錄下任一檔案）與 `run_skill_script`（用 `uv run python` 執行該 Skill `scripts/` 目錄下的腳本，30 秒 timeout、輸出截斷 4000 字元）兩個 Tool，`load_skill` 額外回傳該 Skill 目錄下的檔案清單（manifest）供 Assistant 判斷是否要進一步讀取或執行。

這個決定刻意不去修改 Skill 本身「無副作用」的定義：腳本執行的副作用歸在新增的 Tool 身上，Skill 正文只是「指示」Assistant 去呼叫這個 Tool，維持 Tool／Skill 既有的分界，避免未來又混淆兩者。曾考慮過反過來重新定義 Skill 允許有副作用，但這樣會讓「Skill 不對外產生副作用」這條既有規則變得不成立，對讀 `CONTEXT.md` 的人來說更難理解，故不採用。

腳本執行能力用 `DISABLE_SKILL_SCRIPTS` 環境變數控制，預設 `false`（放行），需要顯式設成 `true` 才關閉；關閉時 `run_skill_script` 直接不註冊進 Assistant 可用的 Tool 清單，`read_skill_resource` 不受影響。預設開放的理由是這是本地個人助理，Skill 底下的腳本都是開發者自己寫在 repo 裡的內容，不是使用者可控的攻擊面；旗標本身則是為了因應 Slack 訊息可能包含誘導 Assistant 亂跑腳本的 prompt injection，保留一個低成本的總開關。曾考慮過預設關閉、需要顯式開啟才能執行，這樣「預設安全」更保守，但會讓多數情境下都要多一個設定步驟才能用到新功能的價值，權衡後選擇預設開放。
