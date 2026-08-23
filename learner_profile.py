"""Structured learner profile tools and validation."""

from typing import Any, Literal

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROFILE_NAMESPACE_SUFFIX = "learner_profile"
PROFILE_KEY = "profile"

Level = Literal["unknown", "A1", "A2", "B1", "B2", "C1", "C2"]
CorrectionPreference = Literal["immediate", "end_of_turn", "major_errors_only"]


class LearnerProfile(BaseModel):
    """The structured preferences and level of one learner."""

    model_config = ConfigDict(extra="forbid")

    level: Level = "unknown"
    learning_goals: list[str] = Field(default_factory=list, max_length=20)
    native_language: str | None = None
    interests: list[str] = Field(default_factory=list, max_length=20)
    correction_preference: CorrectionPreference = "end_of_turn"
    interface_language: str = "zh-TW"
    target_language: str = "en"

    @field_validator("learning_goals", "interests")
    @classmethod
    def normalize_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = value.strip()
            if not cleaned or len(cleaned) > 200:
                raise ValueError("list items must be non-empty and at most 200 characters")
            folded = cleaned.casefold()
            if folded not in seen:
                normalized.append(cleaned)
                seen.add(folded)
        return normalized

    @field_validator("native_language", "interface_language", "target_language")
    @classmethod
    def validate_language_tag(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or len(value) > 35 or not value.isascii():
            raise ValueError("language tag must be ASCII and 1-35 characters")
        parts = value.split("-")
        if any(not part.isalnum() for part in parts):
            raise ValueError("language tag must contain alphanumeric subtags")
        if not parts[0].isalpha() or not 2 <= len(parts[0]) <= 8:
            raise ValueError("language tag must start with a language subtag")
        return value


class LearnerProfilePatch(BaseModel):
    """Fields that may be changed in one atomic profile update."""

    model_config = ConfigDict(extra="forbid")

    level: Level | None = None
    learning_goals: list[str] | None = Field(default=None, max_length=20)
    native_language: str | None = None
    interests: list[str] | None = Field(default=None, max_length=20)
    correction_preference: CorrectionPreference | None = None
    interface_language: str | None = None
    target_language: str | None = None

    @model_validator(mode="after")
    def require_a_field(self) -> "LearnerProfilePatch":
        if not self.model_fields_set:
            raise ValueError("at least one profile field must be specified")
        return self


class ProfileRuntimeError(BaseModel):
    error: Literal["context_error"]
    message: str


def profile_namespace(user_id: str) -> tuple[str, str]:
    return (user_id, PROFILE_NAMESPACE_SUFFIX)


def _user_id(runtime: ToolRuntime[Any, Any]) -> str | None:
    context = runtime.context
    user_id = context.get("user_id") if isinstance(context, dict) else None
    if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 128:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in user_id):
        return None
    return user_id


def _context_error() -> dict[str, str]:
    return {"error": "context_error", "message": "user_id is required in invocation Context"}


@tool
def get_profile(runtime: ToolRuntime[Any, Any]) -> dict[str, Any]:
    """Read the current learner profile for the invocation user."""
    user_id = _user_id(runtime)
    if user_id is None:
        return _context_error()
    if runtime.store is None:
        raise RuntimeError("profile store is not configured")
    item = runtime.store.get(profile_namespace(user_id), PROFILE_KEY)
    profile = LearnerProfile.model_validate(item.value) if item else LearnerProfile()
    return {"profile": profile.model_dump(), "exists": item is not None}


@tool(args_schema=LearnerProfilePatch)
def update_profile(runtime: ToolRuntime[Any, Any], **patch: Any) -> dict[str, Any]:
    """Atomically update specified learner profile fields for the invocation user."""
    user_id = _user_id(runtime)
    if user_id is None:
        return _context_error()
    if runtime.store is None:
        raise RuntimeError("profile store is not configured")
    validated_patch = LearnerProfilePatch.model_validate(patch)
    item = runtime.store.get(profile_namespace(user_id), PROFILE_KEY)
    current = LearnerProfile.model_validate(item.value) if item else LearnerProfile()
    updated = LearnerProfile.model_validate(
        {**current.model_dump(), **validated_patch.model_dump(exclude_unset=True)}
    )
    runtime.store.put(profile_namespace(user_id), PROFILE_KEY, updated.model_dump())
    return {"profile": updated.model_dump(), "created": item is None}


@tool
def delete_profile(runtime: ToolRuntime[Any, Any]) -> dict[str, Any]:
    """Delete only the invocation user's learner profile."""
    user_id = _user_id(runtime)
    if user_id is None:
        return _context_error()
    if runtime.store is None:
        raise RuntimeError("profile store is not configured")
    item = runtime.store.get(profile_namespace(user_id), PROFILE_KEY)
    if item is not None:
        runtime.store.delete(profile_namespace(user_id), PROFILE_KEY)
    return {"deleted": item is not None}
