"""Query immutable settlement snapshots, with business-date inclusive filters."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.auth import SessionDep, UserDep
from app.config import get_settings
from app.models import ProductionOrder as Order, ProductionOrderMaterial as Material
from app.schemas import Envelope
from app.stock import page_result

router = APIRouter(prefix='/api/losses', tags=['损耗统计'])


@router.get('', response_model=Envelope)
def losses(user: UserDep, session: SessionDep,
           order_no: str = Query('', max_length=120),
           product: str = Query('', max_length=120), material: str = Query('', max_length=120),
           product_id: int | None = Query(None, gt=0), material_id: int | None = Query(None, gt=0),
           date_from: date | None = None, date_to: date | None = None,
           page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    query = select(Material.id, Order.id.label('production_order_id'), Order.order_no,
                   Order.product_name_snapshot.label('product_name'), Order.actual_quantity,
                   Order.unit.label('product_unit'), Material.material_name_snapshot.label('material_name'),
                   Material.base_unit, Material.final_theoretical_quantity, Material.actual_used_quantity,
                   Material.loss_quantity, Material.loss_rate, Order.completed_at
                   ).join(Order, Order.id == Material.production_order_id).where(Order.status == 'completed')
    for column, value in ((Order.order_no, order_no), (Order.product_name_snapshot, product),
                          (Material.material_name_snapshot, material)):
        if value.strip():
            query = query.where(column.contains(value.strip(), autoescape=True))
    if product_id:
        query = query.where(Order.product_id == product_id)
    if material_id:
        query = query.where(Material.material_id == material_id)
    zone = ZoneInfo(get_settings().business_timezone)
    if date_from:
        query = query.where(Order.completed_at >= datetime.combine(date_from, time.min, zone).astimezone(timezone.utc))
    if date_to:
        if date_to == date.max:
            raise HTTPException(422, '结束日期超出支持范围')
        query = query.where(Order.completed_at < datetime.combine(date_to + timedelta(days=1), time.min, zone).astimezone(timezone.utc))
    return page_result(session, query.order_by(Order.completed_at.desc(), Material.id.desc()), page, page_size)
