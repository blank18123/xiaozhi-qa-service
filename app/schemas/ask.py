from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class AskMode(str, Enum):
    text = "text"
    audio = "audio"


class AskRequest(BaseModel):
    query: Optional[str] = Field(None, description="文本输入（mode=text时必填）")
    mode: AskMode = Field(AskMode.text, description="问答模式")
    session_id: Optional[str] = Field(None, description="会话ID，不传则自动创建")
    role_prompt: Optional[str] = Field(None, description="覆盖默认角色提示词")


class ToolCall(BaseModel):
    name: str
    arguments: dict
    result: Optional[str] = None


class TextAskResponse(BaseModel):
    session_id: str
    answer: str


class StreamEvent(BaseModel):
    event: str
    data: Optional[str] = None
    tool_name: Optional[str] = None
