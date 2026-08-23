# English Tutor Agent

Status: needs-triage

## Problem Statement

目前的 Agent 具備 Thread checkpoint、長期記憶與工具呼叫，但沒有結構化的學習者模型，也沒有可恢復的教學流程。它無法穩定根據學習者程度、目標、錯誤歷史與待複習項目，提供一致的英文對話、練習、批改與複習體驗。

## Solution

將現有聊天 Agent 擴充為英文導師，先完成不依賴語音的 MVP。MVP 會建立 LearnerProfile、conversation/grammar/vocabulary/writing 教學模式、結構化練習與答案評估、learning event、錯誤與單字紀錄、間隔複習，以及依程度與 teaching mode 切換英文/繁體中文的回覆策略。

教學流程必須可測試、可觀測並能從 checkpoint 恢復。評分、排程與資料寫入由結構化 schema 和明確工具控制，不由 LLM 自由文字直接決定。

## User Stories

1. As a learner, I want the tutor to remember my level and learning goals, so that future Turns can adapt to my needs.
2. As a learner, I want to choose conversation, grammar, vocabulary, or writing mode, so that I can practise a specific skill.
3. As a learner, I want the tutor to select a sensible default mode when I do not specify one, so that I can start without configuring every Turn.
4. As a learner, I want to receive structured exercises, so that I know what answer is expected.
5. As a learner, I want my answers evaluated consistently, so that feedback does not depend on parsing arbitrary prose.
6. As a learner, I want grammar, vocabulary, meaning, and naturalness issues distinguished, so that I know what to improve.
7. As a learner, I want recurring errors and learned vocabulary recorded, so that the tutor can use my history in later Turns.
8. As a learner, I want review items scheduled after practice, so that I can revisit weak areas at an appropriate time.
9. As a learner, I want the tutor to respond in a language strategy suited to my level and preferences, so that explanations remain understandable.
10. As a learner, I want an interrupted Turn to resume without duplicate learning records, so that recovery does not corrupt my progress.
11. As a learner, I want to view my progress and saved learning data, so that I can understand my development.
12. As a learner, I want to delete my saved learning data, so that I remain in control of my privacy.
13. As a learner, I want to add voice practice later without losing text-mode support, so that unavailable speech services do not block normal learning.

## Implementation Decisions

- Keep Thread checkpoint, LearnerProfile, learning events, and review items as separate data concepts.
- Identify learner-owned data by `user_id`; keep `thread_id` responsible for checkpointed conversation history.
- Use typed schemas for profile, exercises, evaluations, learning events, errors, vocabulary, review items, and tutor graph state.
- Keep profile data separate from semantic long-term memory; profile updates are validated partial updates and do not overwrite unspecified fields.
- Make the tutor graph explicit about intent classification, learner context loading, strategy selection, exercise/evaluation branches, event recording, review scheduling, and final response.
- Keep scoring and scheduling deterministic at their boundaries; LLM output must pass schema validation before persistence.
- Use a simplified Leitner schedule for the MVP and pure functions for schedule calculation.
- Preserve the current tool-call fallback and checkpoint recovery behavior while expanding the graph.

## Testing Decisions

- Test externally observable behavior through schemas, tools, graph state, and persisted results rather than private implementation details.
- Use fake models and in-memory stores for unit tests, following the existing unit-test pattern.
- Cover successful and malformed structured model output, including the failure path that prevents invalid data from being persisted.
- Cover user isolation, CRUD behavior, checkpoint recovery, duplicate-event prevention, language strategy selection, evaluation categories, and due-item calculation.
- Add integration tests for one conversation, one exercise/evaluation flow, and one cross-Thread review flow.

## Out of Scope

- A complete frontend or learning dashboard in the first phase.
- Voice input, text-to-speech, pronunciation scoring, and audio replay in the first phase; these belong to the second phase.
- Storing all learning data as untyped free-form long-term memory.
- Allowing unconstrained LLM prose to decide scores, schedules, or persistence.

## Further Notes

The implementation should proceed through the tickets in `issues/`, respecting their `Blocked by` relationships. The current local tracker stores one ticket per Markdown file; completed tickets use `Status: resolved`, while untriaged tickets remain `Status: needs-triage` until their scope is confirmed.