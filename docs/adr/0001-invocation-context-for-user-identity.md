# ADR-0001: Use Invocation Context for User Identity

## Status

Accepted

## Context

The agent has two identity-related values with different responsibilities. `thread_id` identifies checkpointed conversation history, while `user_id` selects long-term memory shared across Threads. The graph also has execution metadata and callbacks that are not domain state.

Keeping `user_id` in `RunnableConfig["configurable"]` made the memory tools depend on ambient configuration and blurred the boundary between checkpointer configuration and invocation data.

## Decision

Use a typed invocation context containing `user_id`.

- `user_id` is supplied through the graph invocation context and is not part of checkpointed state.
- `thread_id` remains in `config["configurable"]` because the checkpointer requires it to locate a Thread.
- Memory tools receive `ToolRuntime` and read the user identity and store from that runtime.
- The application boundary rejects missing or blank `user_id` values instead of supplying a shared default.
- Identity authenticity and Thread ownership are responsibilities of the external caller; this context is not an authorization mechanism.

## Consequences

Memory tools have explicit, typed runtime dependencies and cannot accidentally read a different user identity from ambient configurable data. Callers and tests must pass `context={"user_id": ...}` for graph invocations. `thread_id` and `user_id` remain independently testable: one controls checkpoint continuity and the other controls cross-Thread memory sharing.
