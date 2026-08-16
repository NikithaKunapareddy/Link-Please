from pydantic import BaseModel
from typing import Optional


# ─── Rule models ────────────────────────────────────────────────────────────

class RuleCreate(BaseModel):
    keyword: str
    dm_message: str


class RuleResponse(BaseModel):
    rule_id: str
    keyword: str
    dm_message: str


# ─── Webhook payload models ──────────────────────────────────────────────────

class CommentFrom(BaseModel):
    user_id: str
    username: str


class CommentData(BaseModel):
    comment_id: str
    post_id: Optional[str] = None
    text: Optional[str] = None
    created_at: Optional[str] = None
    from_: Optional[CommentFrom] = None

    class Config:
        # The webhook field is named "from" which is a Python keyword
        populate_by_name = True

    @classmethod
    def model_validate_from_raw(cls, data: dict) -> "CommentData":
        if "from" in data:
            data = dict(data)
            data["from_"] = data.pop("from")
        return cls.model_validate(data)


class WebhookEvent(BaseModel):
    event_id: str
    event_type: str
    sent_at: str
    data: dict  # Keep raw dict to handle both event types


# ─── Stats model ─────────────────────────────────────────────────────────────

class StatsResponse(BaseModel):
    sent: int
    failed: int
    queued: int
    duplicates_blocked: int


# ─── DM Send models ──────────────────────────────────────────────────────────

class DMSendRequest(BaseModel):
    recipient_user_id: str
    message: str
    comment_id: str


class DMSendResponse(BaseModel):
    dm_id: str
    status: str


class DMStatusResponse(BaseModel):
    dm_id: str
    status: str
    recipient_user_id: str
    updated_at: str
