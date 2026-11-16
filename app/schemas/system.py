from typing import Optional

from pydantic import BaseModel, Field


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    subject: Optional[str] = Field(default=None, min_length=2, max_length=200)
    body: Optional[str] = Field(default=None, min_length=2, max_length=8000)
    enabled: Optional[bool] = None


class TemplateIn(BaseModel):
    key: str = Field(min_length=2, max_length=40, pattern="^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=2, max_length=80)
    subject: str = Field(min_length=2, max_length=200)
    body: str = Field(min_length=2, max_length=8000)
    enabled: bool = True


class PreviewIn(BaseModel):
    subject: str = Field(max_length=200)
    body: str = Field(max_length=8000)
    context: dict[str, str] = Field(default_factory=dict)


class RoleIn(BaseModel):
    key: str = Field(min_length=2, max_length=40, pattern="^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=2, max_length=60)
    description: Optional[str] = Field(default=None, max_length=200)
    permissions: dict[str, dict[str, bool]] = Field(default_factory=dict)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=60)
    description: Optional[str] = Field(default=None, max_length=200)
    permissions: Optional[dict[str, dict[str, bool]]] = None


class AdminAccountIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=100)
    admin_role: str = Field(default="admin", max_length=40)


class AdminAccountUpdate(BaseModel):
    admin_role: Optional[str] = Field(default=None, max_length=40)
    is_active: Optional[bool] = None
    admin_permissions: Optional[dict[str, dict[str, bool]]] = None
