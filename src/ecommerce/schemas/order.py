"""订单相关 Pydantic schemas"""

from datetime import datetime

from pydantic import BaseModel, Field


class OrderItemResponse(BaseModel):
    """订单项响应"""

    id: int
    product_id: int | None
    product_name: str
    quantity: int
    unit_price: float

    model_config = {"from_attributes": True}


class OrderCreate(BaseModel):
    """创建订单（从购物车结算）"""

    shipping_address: str = Field(..., min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=300)


class OrderResponse(BaseModel):
    """订单响应"""

    id: int
    user_id: int
    status: str
    total_amount: float
    shipping_address: str
    note: str | None
    items: list[OrderItemResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    """订单列表响应"""

    items: list[OrderResponse]
    total: int


class OrderStatusUpdate(BaseModel):
    """更新订单状态（管理员）"""

    status: str = Field(..., description="pending / paid / shipped / delivered / cancelled")
