from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=1, max_length=128, repr=False)


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    display_name: str


class LoginOutput(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_at: datetime
    user: UserOutput


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)


class ProjectOutput(BaseModel):
    id: UUID
    name: str
    description: str
    role: Literal['admin', 'member']


class MemberInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['admin', 'member']


class MemberOutput(BaseModel):
    user: UserOutput
    role: Literal['admin', 'member']
