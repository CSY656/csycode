"""购物车相关 Pydantic schemas"""

from pydantic import BaseModel, Field

from .product import ProductResponse


class CartItemCreate(BaseModel):
    """添加到购物车"""

    product_id: int
    quantity: int = Field(default=1, ge=1, description="数量")


class CartItemUpdate(BaseModel):
    """更新购物车项数量"""

    quantity: int = Field(..., ge=1, description="新数量")


class CartItemResponse(BaseModel):
    """购物车项响应"""

    id: int
    product_id: int
    quantity: int
    product: ProductResponse | None = None

    model_config = {"from_attributes": True}


class CartListResponse(BaseModel):
    """购物车列表响应"""

    items: list[CartItemResponse]
    total_price: float = 0.0
