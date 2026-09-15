from decimal import Decimal
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints


class Envelope(BaseModel):
    code: str = 'OK'
    message: str = '成功'
    data: Any = None


class InputModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


Code = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
Name = Annotated[str, StringConstraints(min_length=1, max_length=120)]
Username = Annotated[str, StringConstraints(min_length=3, max_length=64, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
# 不裁剪密码；用户输入的空格也是密码的一部分。
Password = Annotated[str, StringConstraints(strip_whitespace=False, min_length=10, max_length=128)]


def exact_decimal(value):
    if isinstance(value, (float, bool)):
        raise ValueError('数量请使用十进制字符串，不要使用浮点数')
    return value


Quantity = Annotated[Decimal, BeforeValidator(exact_decimal), Field(ge=0, max_digits=20, decimal_places=6)]


class ArchiveInput(InputModel):
    code: Code
    name: Name
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=2000)


class MaterialInput(ArchiveInput):
    unit: str = Field(min_length=1, max_length=16)
    specification: str | None = Field(default=None, max_length=200)
    min_stock: Quantity = Decimal('0')


class ProductInput(MaterialInput):
    unit: str = Field(default='盒', min_length=1, max_length=16)


class SupplierInput(ArchiveInput):
    contact: str | None = Field(default=None, max_length=80)
    phone: str | None = Field(default=None, max_length=40)


class UserCreate(InputModel):
    username: Username
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    password: Password
    role_id: int = Field(gt=0)
    is_active: bool = True


class UserUpdate(InputModel):
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    role_id: int = Field(gt=0)
    is_active: bool = True


class LoginInput(InputModel):
    username: Username
    password: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=128)]


class PasswordChange(InputModel):
    current_password: Annotated[str, StringConstraints(strip_whitespace=False, min_length=1, max_length=128)]
    new_password: Password


class PasswordReset(InputModel):
    new_password: Password


PositiveQuantity = Annotated[Decimal, BeforeValidator(exact_decimal), Field(gt=0, le=Decimal('99999999999999.999999'), max_digits=20, decimal_places=6)]


class MaterialOutboundInput(InputModel):
    request_id: Annotated[str, StringConstraints(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')]
    material_id: int = Field(gt=0)
    batch_id: int = Field(gt=0)
    quantity: PositiveQuantity
    unit: str = Field(min_length=1, max_length=16)
    notes: str | None = Field(default=None, max_length=2000)


class MaterialReceiptInput(InputModel):
    request_id: Annotated[str, StringConstraints(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')]
    material_id: int = Field(gt=0)
    batch_no: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    quantity: PositiveQuantity
    unit: str = Field(min_length=1, max_length=16)
    production_date: date
    expiry_date: date
    supplier_id: int = Field(gt=0)
    location_id: int = Field(gt=0)
    notes: str | None = Field(default=None, max_length=2000)


class FinishedStockInput(InputModel):
    request_id: Annotated[str, StringConstraints(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')]
    product_id: int = Field(gt=0)
    quantity: PositiveQuantity
    unit: str = Field(min_length=1, max_length=16)
    business_date: date = Field(ge=date(1000, 1, 1))
    business_type: Literal['normal', 'production'] = 'normal'
    reference_no: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class BomItemInput(InputModel):
    material_id: int = Field(gt=0)
    quantity_per_unit: PositiveQuantity
    unit: str = Field(min_length=1, max_length=16)
    sort_order: int = Field(default=0, ge=0, le=1000000)
    notes: str | None = Field(default=None, max_length=2000)


class BomInput(InputModel):
    product_id: int = Field(gt=0)
    version: int = Field(default=1, gt=0, le=2147483647)
    status: Literal['draft', 'active', 'inactive'] = 'draft'
    notes: str | None = Field(default=None, max_length=2000)
    items: list[BomItemInput] = Field(min_length=1, max_length=500)


class BomCalculateInput(InputModel):
    finished_product_id: int = Field(gt=0)
    planned_quantity: PositiveQuantity
