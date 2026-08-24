"""schemas 包 - Pydantic 请求/响应模型"""

from .user import (
    UserCreate,
    UserLogin,
    UserResponse,
    UserUpdate,
    TokenResponse,
)
from .product import (
    CategoryCreate,
    CategoryUpdate,
    CategoryResponse,
    ProductCreate,
    ProductUpdate,
    ProductResponse,
    ProductListResponse,
)
from .cart import CartItemCreate, CartItemUpdate, CartItemResponse, CartListResponse
from .order import (
    OrderCreate,
    OrderResponse,
    OrderItemResponse,
    OrderListResponse,
    OrderStatusUpdate,
)
from .payment import PaymentCreate, PaymentResponse

__all__ = [
    # 用户
    "UserCreate",
    "UserLogin",
    "UserResponse",
    "UserUpdate",
    "TokenResponse",
    # 分类
    "CategoryCreate",
    "CategoryUpdate",
    "CategoryResponse",
    # 商品
    "ProductCreate",
    "ProductUpdate",
    "ProductResponse",
    "ProductListResponse",
    # 购物车
    "CartItemCreate",
    "CartItemUpdate",
    "CartItemResponse",
    "CartListResponse",
    # 订单
    "OrderCreate",
    "OrderResponse",
    "OrderItemResponse",
    "OrderListResponse",
    "OrderStatusUpdate",
    # 支付
    "PaymentCreate",
    "PaymentResponse",
]
