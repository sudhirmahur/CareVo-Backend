from typing import Optional

from pydantic import BaseModel, Field


class PlanIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    description: Optional[str] = Field(default=None, max_length=300)
    price: float = Field(gt=0, le=1000000)  # rupees
    duration_days: int = Field(ge=1, le=3650)
    features: list[str] = Field(default_factory=list, max_length=12)
    is_active: bool = True
    is_popular: bool = False


class PlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=60)
    description: Optional[str] = Field(default=None, max_length=300)
    price: Optional[float] = Field(default=None, gt=0, le=1000000)
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    features: Optional[list[str]] = Field(default=None, max_length=12)
    is_active: Optional[bool] = None
    is_popular: Optional[bool] = None


class OrderIn(BaseModel):
    plan_id: str


class VerifyIn(BaseModel):
    razorpay_order_id: str = Field(min_length=3, max_length=60)
    razorpay_payment_id: str = Field(min_length=3, max_length=60)
    razorpay_signature: str = Field(min_length=10, max_length=200)


class RefundIn(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=200)
