from decimal import Decimal
from datetime import date
from typing import Annotated, Any

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
