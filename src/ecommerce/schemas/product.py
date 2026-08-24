"""商品/分类相关 Pydantic schemas"""

from datetime import datetime

from pydantic import BaseModel, Field


# ─── 分类 ──────────────────────────────────────────────

class CategoryCreate(BaseModel):
    """创建分类"""

    name: str = Field(..., min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    parent_id: int | None = None  # 父分类 ID，空则为顶级分类


class CategoryUpdate(BaseModel):
    """更新分类"""

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = None


class CategoryResponse(BaseModel):
    """分类响应"""

    id: int
    name: str
    description: str | None
    parent_id: int | None
    children: list["CategoryResponse"] = []

    model_config = {"from_attributes": True}


# ─── 商品 ──────────────────────────────────────────────

class ProductCreate(BaseModel):
    """创建商品"""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    price: float = Field(..., gt=0, description="价格（元）")
    stock: int = Field(..., ge=0, description="库存数量")
    image_url: str | None = None
    category_id: int | None = None


class ProductUpdate(BaseModel):
    """更新商品"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    price: float | None = Field(default=None, gt=0)
    stock: int | None = Field(default=None, ge=0)
    image_url: str | None = None
    category_id: int | None = None
    is_active: bool | None = None


class ProductResponse(BaseModel):
    """商品详情响应"""

    id: int
    name: str
    description: str | None
    price: float
    stock: int
    image_url: str | None
    is_active: bool
    category_id: int | None
    category: CategoryResponse | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductListResponse(BaseModel):
    """商品列表响应（带分页）"""

    items: list[ProductResponse]
    total: int
    page: int
    page_size: int
