# ADR-0002: Isolate Learner Profiles from Long-Term Memory

## Status

Accepted

## Context

The English Tutor needs structured learner data such as CEFR level, learning goals, language tags, interests, and correction preference. The existing long-term memory is an independently owned semantic-memory collection, while `thread_id` identifies checkpointed conversation history. Treating profile data as free-form memory would make exact updates, deletion, and validation unreliable.

## Decision

Store one structured `LearnerProfile` per `user_id` in the independent `(user_id, "learner_profile")` namespace under the fixed key `profile`. The profile is shared across Threads, while `thread_id` remains scoped to checkpointed conversation history. In accordance with ADR-0001, profile tools obtain `user_id` only from invocation Context and reject calls without it.

Reading a missing profile returns defaults without creating a record. A partial update preserves unspecified fields, validates the complete result before an atomic write, and creates the record when needed. Deletion removes only the profile; it does not remove checkpoints, long-term memories, learning events, or review data. The MVP accepts last-write-wins and does not add version or timestamp fields.

## Consequences

Profile fields have a stable typed contract and can be tested independently from semantic memory. Data deletion boundaries remain explicit, and a missing profile is distinguishable from a saved profile containing default values. Concurrent updates are not conflict-detected in the MVP, so callers that need stronger guarantees must serialize updates or define a future concurrency contract.
