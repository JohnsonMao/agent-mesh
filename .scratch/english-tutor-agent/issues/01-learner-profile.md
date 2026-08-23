# 01 — 建立學習者模型

Type: task
Status: resolved
Priority: P0

**What to build:** 建立一份以 `user_id` 隔離、跨 Thread 共用的結構化 `LearnerProfile`，並提供讀取、更新與刪除能力。

**Blocked by:** None — can start immediately.

## Acceptance criteria

- [x] `LearnerProfile` schema 有明確型別與預設值：
	- `level`: `unknown | A1 | A2 | B1 | B2 | C1 | C2`
	- `learning_goals`: 最多 20 個非空字串，每項最多 200 字元
	- `native_language`: BCP-47 風格字串或 `null`
	- `interests`: 最多 20 個非空字串，每項最多 200 字元
	- `correction_preference`: `immediate | end_of_turn | major_errors_only`
	- `interface_language`: 預設 `zh-TW`
	- `target_language`: 預設 `en`
- [x] 預設值為 `level=unknown`、`learning_goals=[]`、`native_language=null`、`interests=[]`、`correction_preference=end_of_turn`。
- [x] `user_id` 只從 invocation `Context` 取得，profile 以 `(user_id, "learner_profile")` 獨立隔離，不能跨使用者讀取。
- [x] `get_profile` 對尚未建立的使用者回傳預設 profile 與 `exists=false`，且不建立資料。
- [x] `update_profile` 在不存在時建立 profile；partial update 不覆蓋未指定欄位，空 list 明確代表清空。
- [x] 更新前完整驗證；未知欄位、無效值或超過上限時整次更新失敗，不寫入部分結果。
- [x] `delete_profile` 只刪除 profile，不連帶刪除 Thread checkpoint、long-term memory、learning event 或複習資料。
- [x] `get_profile`、`update_profile`、`delete_profile` 都有 unit test，覆蓋隔離、不存在 profile、partial update、清空與刪除後重建。

## Comments

### 2026-08-24 Grilling resolution

已確認 `LearnerProfile` 是以 `user_id` 為擁有者、跨 Thread 共用的結構化學習偏好與程度資料；`thread_id` 只識別對話歷史。工具不接受 LLM 自由傳入的 `user_id`，缺少 Context 時回傳結構化 context/authorization error；儲存層例外則直接拋出。

Profile 使用固定 key `profile` 儲存於獨立 `(user_id, "learner_profile")` namespace。語言欄位採 BCP-47 風格標籤並做基本格式驗證；list 去除前後空白、以不分大小寫方式去重並保留第一個值。MVP 採 last-write-wins，不加入版本控制或時間欄位；未知欄位一律拒絕。

## Resolution

已完成 `LearnerProfile` schema、Context-based user isolation、profile CRUD tools 與 unit tests。驗證結果：`pytest tests/unit` 28 tests passed、Ruff check/format passed、mypy passed。