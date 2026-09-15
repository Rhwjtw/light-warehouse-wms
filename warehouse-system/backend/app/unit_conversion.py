"""系统统一单位换算；只按 units 中的维度和倍率换算，不进行密度或计数单位猜测。"""
from decimal import Decimal, localcontext

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Unit

MAX_QUANTITY = Decimal('99999999999999.999999')


def get_compatible_units(session, source: str, target: str) -> tuple[Unit, Unit]:
    source_unit, target_unit = session.get(Unit, source), session.get(Unit, target)
    if not source_unit or not target_unit:
        raise HTTPException(422, '单位不存在或不受支持')
    if source_unit.dimension != target_unit.dimension:
        raise HTTPException(422, f'单位不兼容：不能从 {source} 换算为 {target}')
    return source_unit, target_unit


def are_compatible(session, source: str, target: str) -> bool:
    source_unit, target_unit = session.get(Unit, source), session.get(Unit, target)
    return bool(source_unit and target_unit and source_unit.dimension == target_unit.dimension)


def convert_quantity(session, quantity: Decimal, source: str, target: str, *, require_six_places: bool = True) -> Decimal:
    source_unit, target_unit = get_compatible_units(session, source, target)
    with localcontext() as context:
        context.prec = 60
        result = quantity * source_unit.factor / target_unit.factor
        if result <= 0:
            raise HTTPException(422, '数量必须大于0')
        if require_six_places and (result > MAX_QUANTITY or result != result.quantize(Decimal('0.000001'))):
            raise HTTPException(422, '换算后数量超出范围或超过六位小数')
        return result


def to_base_unit(session, quantity: Decimal, unit: str) -> tuple[Decimal, str]:
    source = session.get(Unit, unit)
    if not source:
        raise HTTPException(422, '单位不存在或不受支持')
    base = session.scalar(select(Unit).where(Unit.dimension == source.dimension).order_by(Unit.factor, Unit.code))
    return convert_quantity(session, quantity, unit, base.code, require_six_places=False), base.code


def normalize_quantity(session, quantity: Decimal, source: str, target: str) -> Decimal:
    return convert_quantity(session, quantity, source, target, require_six_places=False)
