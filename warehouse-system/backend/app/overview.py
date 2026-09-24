"""Database-backed alerts, dashboard, settings and read-only audit queries."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import case, func, or_, select

from app.auth import SessionDep, UserDep, audit, require_permission
from app.config import get_settings
from app.models import (WarningRule, MaterialBatch as Batch, RawMaterial, FinishedProduct,
                        FinishedBatch, ProductionOrder, InventoryTransaction as Ledger,
                        StockDocument, User, OperationLog, Supplier, Location)
from app.schemas import Envelope
from app.stock import page_result
from app.stock_service import business_today
from app.audit_labels import ACTIONS, MODULES

router = APIRouter(prefix='/api', tags=['看板、预警与系统管理'])


class ExpirySettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    urgent_expiry_days: int = Field(gt=0, le=36500, strict=True)
    warning_expiry_days: int = Field(gt=0, le=36500, strict=True)

    @model_validator(mode='after')
    def ordered(self):
        if self.warning_expiry_days <= self.urgent_expiry_days:
            raise ValueError('一般临期天数必须大于严重临期天数')
        return self


def thresholds(session, lock=False):
    query = select(WarningRule).where(WarningRule.code.in_(['expiry_near', 'expiry_early'])).order_by(WarningRule.id)
    rows = {r.code: r for r in session.scalars(query.with_for_update() if lock else query)}
    if len(rows) != 2 or not 0 < rows['expiry_near'].days < rows['expiry_early'].days <= 36500:
        raise HTTPException(503, '有效期规则缺失或无效，请管理员检查设置')
    return rows


def rule_data(rows):
    return {'urgent_expiry_days': rows['expiry_near'].days, 'warning_expiry_days': rows['expiry_early'].days}


def date_bounds(start, end):
    if start and end and start > end:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    if end == date.max or start == date.min:
        raise HTTPException(422, '日期超出支持范围')
    zone = ZoneInfo(get_settings().business_timezone)
    # 将业务自然日转换为左闭右开的 UTC 区间，避免结束日 23:59:59 的精度遗漏。
    return (datetime.combine(start, time.min, zone).astimezone(timezone.utc) if start else None,
            datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(timezone.utc) if end else None)


@router.get('/settings/expiry', response_model=Envelope)
def read_settings(user: UserDep, session: SessionDep):
    return Envelope(data=rule_data(thresholds(session)))


@router.put('/settings/expiry', response_model=Envelope)
def write_settings(payload: ExpirySettings, user: UserDep, session: SessionDep):
    require_permission(user, session, 'settings.manage')
    rows = thresholds(session, lock=True)
    before = rule_data(rows)
    rows['expiry_near'].days = payload.urgent_expiry_days
    rows['expiry_early'].days = payload.warning_expiry_days
    # 第一版设置页管理的两条必需规则始终启用，避免阈值存在但查询口径被局部关闭。
    for row in rows.values():
        row.is_active = True
    audit(session, user.id, 'update_settings', 'warning_rules', details={'before': before, 'after': payload.model_dump()})
    return Envelope(data=payload.model_dump())


def expiry_expression(today, rules):
    # 到期当天仍有效：早于今天才是过期；0～严重阈值为严重临期，之后至一般阈值为一般临期。
    return case((Batch.expiry_date < today, 'EXPIRED'),
                (Batch.expiry_date <= today + timedelta(days=rules['expiry_near'].days), 'URGENT'),
                (Batch.expiry_date <= today + timedelta(days=rules['expiry_early'].days), 'WARNING'),
                else_='NORMAL')


@router.get('/alerts/expiry', response_model=Envelope)
def expiry(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
           status: str | None = Query(None, pattern='^(EXPIRED|URGENT|WARNING|NORMAL)$'),
           date_from: date | None = None, date_to: date | None = None,
           page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    date_bounds(date_from, date_to)
    today, rules = business_today(), thresholds(session)
    state = expiry_expression(today, rules)
    query = select(Batch.id, RawMaterial.name.label('material_name'), Batch.batch_no,
                   Batch.remaining_quantity, Batch.unit, Batch.production_date, Batch.expiry_date,
                   func.datediff(Batch.expiry_date, today).label('days_remaining'), state.label('status'),
                   Location.name.label('location_name'), Supplier.name.label('supplier_name'))\
        .join(RawMaterial, RawMaterial.id == Batch.material_id)\
        .join(Location, Location.id == Batch.location_id).join(Supplier, Supplier.id == Batch.supplier_id)\
        .where(Batch.remaining_quantity > 0)
    # 零余额批次没有可处置库存，不进入过期或临期预警。
    query = query.where(state == status) if status else query.where(state != 'NORMAL')
    if q.strip():
        query = query.where(RawMaterial.name.contains(q.strip(), autoescape=True))
    if date_from:
        query = query.where(Batch.expiry_date >= date_from)
    if date_to:
        query = query.where(Batch.expiry_date <= date_to)
    result = page_result(session, query.order_by(Batch.expiry_date, Batch.id), page, page_size)
    result.data.update(rule_data(rules), business_date=str(today))
    return result


@router.get('/dashboard/summary', response_model=Envelope)
def summary(user: UserDep, session: SessionDep):
    today, rules = business_today(), thresholds(session)
    start, end = date_bounds(today, today)
    def count(model, *conditions):
        return session.scalar(select(func.count()).select_from(model).where(*conditions))
    stock = select(Batch.material_id, func.sum(Batch.remaining_quantity).label('quantity')).group_by(Batch.material_id).subquery()
    # 最低库存按同一种原料的全部批次余额汇总后比较；min_stock=0 表示未启用该项预警。
    low = session.scalar(select(func.count()).select_from(RawMaterial).outerjoin(stock, stock.c.material_id == RawMaterial.id)
                         .where(RawMaterial.deleted_at.is_(None), RawMaterial.min_stock > 0,
                                func.coalesce(stock.c.quantity, 0) < RawMaterial.min_stock))
    result = dict(rule_data(rules), business_date=str(today), timezone=get_settings().business_timezone,
                  materials=count(RawMaterial, RawMaterial.deleted_at.is_(None)),
                  products=count(FinishedProduct, FinishedProduct.deleted_at.is_(None)),
                  stocked_batches=count(Batch, Batch.remaining_quantity > 0),
                  in_progress_orders=count(ProductionOrder, ProductionOrder.status == 'in_progress'), low_stock_materials=low,
                  expired_batches=count(Batch, Batch.remaining_quantity > 0, Batch.expiry_date < today),
                  urgent_batches=count(Batch, Batch.remaining_quantity > 0, Batch.expiry_date >= today,
                                       Batch.expiry_date <= today + timedelta(days=rules['expiry_near'].days)),
                  warning_batches=count(Batch, Batch.remaining_quantity > 0, Batch.expiry_date >= today,
                                        Batch.expiry_date <= today + timedelta(days=rules['expiry_early'].days)))
    # kg、L、个等单位不能相加；今日统计按真实记账时间分单位聚合，而不是按单据补录日期混算。
    groups = session.execute(select(Ledger.kind, Ledger.unit, func.sum(Ledger.change_quantity))
                             .where(Ledger.created_at >= start, Ledger.created_at < end).group_by(Ledger.kind, Ledger.unit)).all()
    buckets = {key: {} for key in ('material_in', 'material_out', 'finished_in', 'finished_out')}
    mapping = {'material_receipt': 'material_in', 'return': 'material_in', 'material_outbound': 'material_out',
               'issue': 'material_out', 'supplement': 'material_out', 'finished_receipt': 'finished_in', 'finished_outbound': 'finished_out'}
    for kind, unit, quantity in groups:
        bucket = buckets[mapping[kind]]
        bucket[unit] = bucket.get(unit, 0) + abs(quantity)
    result['today'] = {key: [{'unit': unit, 'quantity': str(value)} for unit, value in sorted(bucket.items())] for key, bucket in buckets.items()}
    return Envelope(data=result)


@router.get('/dashboard/recent-transactions', response_model=Envelope)
def recent_transactions(user: UserDep, session: SessionDep):
    query = select(Ledger.id, Ledger.created_at, Ledger.kind, Ledger.change_quantity, Ledger.unit,
                   func.coalesce(RawMaterial.name, FinishedProduct.name).label('name'),
                   StockDocument.document_no, StockDocument.reference_no, User.display_name.label('operator'))\
        .join(StockDocument, StockDocument.id == Ledger.document_id).join(User, User.id == Ledger.operator_id)\
        .outerjoin(Batch, Batch.id == Ledger.material_batch_id).outerjoin(RawMaterial, RawMaterial.id == Batch.material_id)\
        .outerjoin(FinishedBatch, FinishedBatch.id == Ledger.finished_batch_id)\
        .outerjoin(FinishedProduct, FinishedProduct.id == FinishedBatch.product_id)
    return page_result(session, query.order_by(Ledger.created_at.desc(), Ledger.id.desc()), 1, 10)


@router.get('/dashboard/recent-production-orders', response_model=Envelope)
def recent_orders(user: UserDep, session: SessionDep):
    query = select(ProductionOrder.id, ProductionOrder.order_no, ProductionOrder.product_name_snapshot.label('product_name'),
                   ProductionOrder.planned_quantity, ProductionOrder.actual_quantity, ProductionOrder.unit,
                   ProductionOrder.status, ProductionOrder.created_at)
    return page_result(session, query.order_by(ProductionOrder.created_at.desc(), ProductionOrder.id.desc()), 1, 10)


@router.get('/operation-logs', response_model=Envelope)
def logs(user: UserDep, session: SessionDep, username: str = Query('', max_length=64),
         module: str = Query('', max_length=64), action: str = Query('', max_length=80),
         q: str = Query('', max_length=120), date_from: date | None = None, date_to: date | None = None,
         page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    start, end = date_bounds(date_from, date_to)
    query = select(OperationLog.id, OperationLog.user_id, OperationLog.username_snapshot,
                   OperationLog.entity_type.label('module'), OperationLog.action,
                   OperationLog.entity_type.label('target_type'), OperationLog.entity_id.label('target_id'),
                   OperationLog.description, OperationLog.created_at)
    for column, value in ((OperationLog.username_snapshot, username), (OperationLog.entity_type, module), (OperationLog.action, action)):
        if value.strip():
            query = query.where(column.contains(value.strip(), autoescape=True))
    if q.strip():
        query = query.where(or_(OperationLog.description.contains(q.strip(), autoescape=True),
                               OperationLog.entity_id.contains(q.strip(), autoescape=True)))
    if start:
        query = query.where(OperationLog.created_at >= start)
    if end:
        query = query.where(OperationLog.created_at < end)
    result = page_result(session, query.order_by(OperationLog.created_at.desc(), OperationLog.id.desc()), page, page_size)
    for row in result.data['items']:
        row['module_name'] = MODULES.get(row['module'], row['module'])
        row['action_name'] = ACTIONS.get(row['action'], row['action'])
    result.data.update(modules=MODULES, actions=ACTIONS)
    return result
