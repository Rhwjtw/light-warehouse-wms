"""BOM 配方维护与只读理论算料；不创建生产单，也不修改库存。"""
from decimal import Decimal, localcontext

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import delete, func, or_, select

from app.auth import SessionDep, UserDep, audit, require_permission
from app.models import BomHeader, BomItem, FinishedProduct, ProductionOrder, RawMaterial, utcnow
from app.schemas import BomCalculateInput, BomInput, Envelope
from app.unit_conversion import get_compatible_units, normalize_quantity

router = APIRouter(prefix='/api', tags=['BOM 配方'])


def decimal_text(value: Decimal) -> str:
    # 不使用 normalize()：它会受默认 28 位上下文影响，可能把大数量试算结果意外舍入。
    value_text = format(value, 'f')
    return value_text.rstrip('0').rstrip('.') if '.' in value_text else value_text


def find_bom(session, identity: int, *, lock: bool = False) -> BomHeader:
    query = select(BomHeader).where(BomHeader.id == identity, BomHeader.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    bom = session.scalar(query)
    if bom is None:
        raise HTTPException(404, 'BOM不存在')
    return bom


def active_catalog_record(session, model, identity: int, label: str, *, lock: bool = False):
    query = select(model).where(model.id == identity)
    if lock:
        query = query.with_for_update()
    record = session.scalar(query)
    if record is None or record.deleted_at is not None or not record.is_active:
        raise HTTPException(422, f'{label}不存在、已停用或已删除')
    return record


def validate_payload(session, payload: BomInput, *, current_id: int | None = None):
    # 锁住成品可把“校验单一 ACTIVE”和写入串行化；数据库唯一约束继续兜住并发竞态。
    product = active_catalog_record(session, FinishedProduct, payload.product_id, '成品', lock=True)
    material_ids = [item.material_id for item in payload.items]
    if len(material_ids) != len(set(material_ids)):
        raise HTTPException(422, '同一个BOM中不能重复添加同一种原材料')
    materials = {}
    for item in payload.items:
        material = active_catalog_record(session, RawMaterial, item.material_id, '原材料')
        get_compatible_units(session, item.unit, material.unit)
        materials[material.id] = material
    if payload.status == 'active':
        active = session.scalar(select(BomHeader.id).where(
            BomHeader.product_id == product.id,
            BomHeader.status == 'active',
            BomHeader.deleted_at.is_(None),
            BomHeader.id != (current_id or 0),
        ))
        if active is not None:
            raise HTTPException(409, '该成品已有启用中的BOM，请先停用原BOM')
    return product, materials


def item_data(item: BomItem, material: RawMaterial) -> dict:
    return {
        'id': item.id,
        'material_id': material.id,
        'material_code': material.code,
        'material_name': material.name,
        'material_stock_unit': material.unit,
        'quantity_per_unit': decimal_text(item.quantity_per_unit),
        'unit': item.unit,
        'sort_order': item.sort_order,
        'notes': item.notes,
        'created_at': item.created_at.isoformat(),
        'updated_at': item.updated_at.isoformat(),
    }


def bom_data(session, bom: BomHeader) -> dict:
    product = session.get(FinishedProduct, bom.product_id)
    rows = session.execute(
        select(BomItem, RawMaterial)
        .join(RawMaterial, RawMaterial.id == BomItem.material_id)
        .where(BomItem.bom_id == bom.id)
        .order_by(BomItem.sort_order, BomItem.id)
    ).all()
    return {
        'id': bom.id,
        'product_id': product.id,
        'product_code': product.code,
        'product_name': product.name,
        'product_unit': product.unit,
        'version': bom.version,
        'status': bom.status,
        'notes': bom.notes,
        'created_at': bom.created_at.isoformat(),
        'updated_at': bom.updated_at.isoformat(),
        'items': [item_data(item, material) for item, material in rows],
    }


@router.get('/boms', response_model=Envelope)
def list_boms(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
              status: str | None = Query(None, pattern='^(draft|active|inactive|archived)$'),
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    conditions = [BomHeader.deleted_at.is_(None), FinishedProduct.deleted_at.is_(None)]
    if q.strip():
        conditions.append(or_(FinishedProduct.name.contains(q.strip(), autoescape=True),
                              FinishedProduct.code.contains(q.strip(), autoescape=True)))
    if status:
        conditions.append(BomHeader.status == status)
    base = select(BomHeader.id).join(FinishedProduct).where(*conditions)
    total = session.scalar(select(func.count()).select_from(base.subquery()))
    item_counts = select(BomItem.bom_id, func.count().label('item_count')).group_by(BomItem.bom_id).subquery()
    rows = session.execute(
        select(BomHeader, FinishedProduct, func.coalesce(item_counts.c.item_count, 0))
        .join(FinishedProduct).outerjoin(item_counts, item_counts.c.bom_id == BomHeader.id)
        .where(*conditions).order_by(BomHeader.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [{
        'id': bom.id, 'product_id': product.id, 'product_code': product.code,
        'product_name': product.name, 'product_unit': product.unit, 'version': bom.version,
        'status': bom.status, 'notes': bom.notes, 'item_count': item_count,
        'created_at': bom.created_at.isoformat(), 'updated_at': bom.updated_at.isoformat(),
    } for bom, product, item_count in rows]
    return Envelope(data={'items': items, 'total': total, 'page': page, 'page_size': page_size})


@router.post('/boms/calculate', response_model=Envelope)
def calculate(payload: BomCalculateInput, user: UserDep, session: SessionDep):
    # 试算只读取当前 ACTIVE BOM 并组装结果，不创建生产单、库存单据或流水，也不保存计算结果。
    product = session.scalar(select(FinishedProduct).where(
        FinishedProduct.id == payload.finished_product_id,
        FinishedProduct.deleted_at.is_(None), FinishedProduct.is_active.is_(True),
    ))
    if product is None:
        raise HTTPException(422, '成品不存在、已停用或已删除')
    bom = session.scalar(select(BomHeader).where(
        BomHeader.product_id == product.id, BomHeader.status == 'active',
        BomHeader.deleted_at.is_(None),
    ))
    if bom is None:
        raise HTTPException(422, '该成品尚未配置有效BOM。')
    if bom.output_unit != product.unit:
        raise HTTPException(422, '当前有效BOM的成品单位与成品档案不一致')
    rows = session.execute(
        select(BomItem, RawMaterial).join(RawMaterial).where(BomItem.bom_id == bom.id)
        .order_by(BomItem.sort_order, BomItem.id)
    ).all()
    if not rows:
        raise HTTPException(422, '当前有效BOM没有原材料明细')
    requirements = []
    for item, material in rows:
        if material.deleted_at is not None or not material.is_active:
            raise HTTPException(422, f'BOM原材料“{material.name}”已失效')
        get_compatible_units(session, item.unit, material.unit)
        with localcontext() as context:
            # 先在 BOM 原单位中高精度相乘，再按原料档案单位换算，避免中间值精度损失。
            context.prec = 60
            total = item.quantity_per_unit * payload.planned_quantity
        suggested = normalize_quantity(session, total, item.unit, material.unit)
        requirements.append({
            'material_id': material.id,
            'material_code': material.code,
            'material_name': material.name,
            'quantity_per_unit': decimal_text(item.quantity_per_unit),
            'bom_unit': item.unit,
            'total_quantity': decimal_text(total),
            'total_unit': item.unit,
            'suggested_quantity': decimal_text(suggested),
            'suggested_unit': material.unit,
        })
    return Envelope(data={
        'finished_product_id': product.id,
        'finished_product_code': product.code,
        'finished_product_name': product.name,
        'finished_product_unit': product.unit,
        'planned_quantity': decimal_text(payload.planned_quantity),
        'bom_id': bom.id,
        'bom_version': bom.version,
        'requirements': requirements,
    })


@router.get('/boms/{identity}', response_model=Envelope)
def get_bom(identity: int, user: UserDep, session: SessionDep):
    return Envelope(data=bom_data(session, find_bom(session, identity)))


@router.post('/boms', response_model=Envelope)
def create_bom(payload: BomInput, user: UserDep, session: SessionDep):
    require_permission(user, session, 'bom.write')
    product, _ = validate_payload(session, payload)
    bom = BomHeader(product_id=product.id, version=payload.version, status=payload.status,
                    output_unit=product.unit, notes=payload.notes)
    session.add(bom)
    session.flush()
    for item in payload.items:
        session.add(BomItem(bom_id=bom.id, material_id=item.material_id,
                            quantity_per_unit=item.quantity_per_unit, unit=item.unit,
                            sort_order=item.sort_order,
                            notes=item.notes))
    session.flush()
    audit(session, user.id, 'create_bom', 'bom_headers', bom.id,
          {'product_id': product.id, 'version': bom.version, 'status': bom.status, 'item_count': len(payload.items)})
    session.flush()
    return Envelope(data=bom_data(session, bom))


@router.put('/boms/{identity}', response_model=Envelope)
def update_bom(identity: int, payload: BomInput, user: UserDep, session: SessionDep):
    require_permission(user, session, 'bom.write')
    bom = find_bom(session, identity, lock=True)
    referenced = session.scalar(select(ProductionOrder.id).where(ProductionOrder.bom_id == bom.id).limit(1)) is not None
    current_items = session.scalars(select(BomItem).where(BomItem.bom_id == bom.id).order_by(BomItem.sort_order, BomItem.id)).all()
    current_formula = [(row.material_id, row.quantity_per_unit, row.unit, row.sort_order, row.notes) for row in current_items]
    requested_formula = [(row.material_id, row.quantity_per_unit, row.unit, row.sort_order, row.notes) for row in payload.items]
    if referenced and (payload.product_id != bom.product_id or payload.version != bom.version or requested_formula != current_formula):
        # 被生产单引用的版本已成为历史依据；改配方必须新建版本，避免历史理论用量失真。
        raise HTTPException(409, '该BOM已被生产单引用，只能停用或修改备注；配方变更请新建版本')
    if referenced:
        if payload.status == 'active':
            validate_payload(session, payload, current_id=bom.id)
        bom.status = payload.status
        bom.notes = payload.notes
        bom.updated_at = utcnow()
        audit(session, user.id, 'update_bom_status', 'bom_headers', bom.id,
              {'status': bom.status, 'referenced': True})
        session.flush()
        return Envelope(data=bom_data(session, bom))
    product, _ = validate_payload(session, payload, current_id=bom.id)
    bom.product_id = product.id
    bom.version = payload.version
    bom.status = payload.status
    bom.output_unit = product.unit
    bom.notes = payload.notes
    bom.updated_at = utcnow()
    session.execute(delete(BomItem).where(BomItem.bom_id == bom.id))
    session.flush()
    for item in payload.items:
        session.add(BomItem(bom_id=bom.id, material_id=item.material_id,
                            quantity_per_unit=item.quantity_per_unit, unit=item.unit,
                            sort_order=item.sort_order,
                            notes=item.notes))
    session.flush()
    audit(session, user.id, 'update_bom', 'bom_headers', bom.id,
          {'product_id': product.id, 'version': bom.version, 'status': bom.status, 'item_count': len(payload.items)})
    session.flush()
    return Envelope(data=bom_data(session, bom))
