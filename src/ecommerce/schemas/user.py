"""用户相关 Pydantic schemas"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """注册请求"""

    username: str = Field(..., min_length=3, max_length=50, description="用户名")
    email: EmailStr = Field(..., description="邮箱")
    password: str = Field(..., min_length=6, max_length=100, description="密码")
    phone: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=200)


class UserLogin(BaseModel):
    """登录请求"""

    username: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码")


class UserUpdate(BaseModel):
    """更新个人信息"""

    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None


class UserResponse(BaseModel):
    """用户信息响应"""

    id: int
    username: str
    email: str
    role: str
    phone: str | None
    address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """登录令牌响应"""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse
