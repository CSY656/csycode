"""支付相关 Pydantic schemas"""

from datetime import datetime

from pydantic import BaseModel, Field


class PaymentCreate(BaseModel):
    """发起支付"""

    method: str = Field(..., description="card / alipay / wechat")


class PaymentResponse(BaseModel):
    """支付记录响应"""

    id: int
    order_id: int
    amount: float
    method: str
    status: str
    paid_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
