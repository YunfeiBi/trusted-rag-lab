from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
import re
import unicodedata


def normalize_username(value):
    if not isinstance(value,str):
        raise ValueError('用户名必须是字符串')
    value=unicodedata.normalize('NFKC',value).strip()
    if not re.fullmatch(r'[a-zA-Z0-9_.\-\u3400-\u9fff]{1,64}',value):
        raise ValueError('用户名仅支持中文、英文字母、数字及_.-，最长64字符')
    return value


def normalize_qq_email(value):
    value=value.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._+\-]{0,63}@qq\.com',value):
        raise ValueError('请输入有效的@qq.com邮箱')
    return value


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128, repr=False)
    _username = field_validator('username',mode='before')(normalize_username)


class RegisterInput(LoginInput):
    password: str = Field(min_length=8,max_length=128,repr=False)
    qq_email: str = Field(max_length=80)
    _qq_email = field_validator('qq_email')(normalize_qq_email)


class ProfileInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    display_name: str = Field(min_length=1,max_length=100)
    qq_email: str = Field(max_length=80)
    _qq_email = field_validator('qq_email')(normalize_qq_email)


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    display_name: str


class ContactUserOutput(UserOutput):
    qq_email: str | None = None


class LoginOutput(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_at: datetime
    user: ContactUserOutput


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)


class ProjectOutput(BaseModel):
    id: UUID
    name: str
    description: str
    role: Literal['admin', 'member']


class ProjectUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name: str | None = Field(default=None,min_length=1,max_length=100)
    description: str | None = Field(default=None,max_length=2000)

    @model_validator(mode='after')
    def supplied_fields(self):
        if not self.model_fields_set or any(getattr(self,k) is None for k in self.model_fields_set):
            raise ValueError('Provide at least one non-null field')
        return self


class MemberInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['admin', 'member']


class MemberOutput(BaseModel):
    user: ContactUserOutput
    role: Literal['admin', 'member']
