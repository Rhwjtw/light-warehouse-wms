"""Read-only preview and atomic completion using frozen BOM and actual material records."""
from decimal import Decimal, localcontext, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import select, func

from app.auth import audit, require_permission
from app.finished_service import post_finished
from app.models import MaterialIssueRecord, utcnow
from app.production_service import find_order, material_rows, material_data, order_data, decimal_text
from app.schemas import FinishedStockInput
from app.stock_service import business_today
from app.unit_conversion import convert_quantity, MAX_QUANTITY
from app.reversal_rules import effective_material_records
from collections import defaultdict


def loss_values(theoretical, actual):
    # 损耗可为负数；理论量为零时损耗率为空，不能除零，也不能把异常差异强制归零。
    with localcontext() as context:
        context.prec = 60
        loss = actual - theoretical
        rate = ((loss / theoretical * 100).quantize(Decimal('0.000000000001'), rounding=ROUND_HALF_UP)
                if theoretical else None)
    return loss, rate


def prepare(session, user, identity, actual_quantity, *, completing=False):
    if completing:
        require_permission(user, session, 'production.manage')
    # 与领补退共用订单行锁；预览只持锁读取，不写数据库。
    order = find_order(session, identity, lock=True)
    if order.status in ('completed', 'received'):
        raise HTTPException(409, '该生产单已完成，不能重复结算。')
    if order.status not in ('ready', 'in_progress'):
        raise HTTPException(409, '该生产单当前状态不能完成生产')
    rows = material_rows(session, order.id)
    if not rows:
        raise HTTPException(409, '生产单缺少BOM快照，不能结算')
    totals = defaultdict(lambda: Decimal('0'))
    for record in effective_material_records(session, [row.id for row in rows]):
        totals[record.order_material_id, record.kind] += record.quantity
    if not any(kind in ('issue', 'supplement') for _, kind in totals):
        raise HTTPException(409, '生产单至少需要一次有效领料或补料记录')
    results = []
    for row in rows:
        # 实际用量以逐笔事实重新汇总：领料 + 补料 - 退料；汇总字段只用于一致性校验。
        issued, supplemented, returned = (totals.get((row.id, kind), Decimal('0'))
                                           for kind in ('issue', 'supplement', 'return'))
        actual = issued + supplemented - returned
        if actual < 0 or actual > MAX_QUANTITY:
            raise HTTPException(409, '真实物料记录汇总异常，不能结算')
        if (issued, supplemented, returned) != (row.issued_quantity, row.supplemented_quantity, row.returned_quantity):
            raise HTTPException(409, '物料记录与领补退汇总不一致，请核查')
        with localcontext() as context:
            context.prec = 60
            raw = actual_quantity * row.quantity_per_unit
        # 最终理论量必须用实际成品数量乘创建时冻结的 BOM 单位用量，不能沿用计划产量。
        theoretical = convert_quantity(session, raw, row.unit, row.base_unit) if raw else Decimal('0')
        loss, rate = loss_values(theoretical, actual)
        results.append({**material_data(row), 'final_theoretical_quantity': decimal_text(theoretical),
                        'actual_used_quantity': decimal_text(actual), 'loss_quantity': decimal_text(loss),
                        'loss_rate': decimal_text(rate) if rate is not None else None})
    return order, rows, {'actual_quantity': decimal_text(actual_quantity), 'finished_product_unit': order.unit,
                         'materials': results}


def preview(session, user, identity, payload):
    # 预览复用正式结算的校验和公式，但只返回计算结果，不写产量、库存、流水或结算字段。
    return prepare(session, user, identity, payload.actual_quantity)[2]


def complete(session, user, identity, payload):
    order, rows, calculation = prepare(session, user, identity, payload.actual_quantity, completing=True)
    order.actual_quantity = payload.actual_quantity
    for row, result in zip(rows, calculation['materials']):
        for field in ('final_theoretical_quantity', 'actual_used_quantity', 'loss_quantity', 'loss_rate'):
            setattr(row, field, Decimal(result[field]) if result[field] is not None else None)
    session.flush()
    post_finished(session, user, FinishedStockInput(
        request_id=f'production-complete-{order.id}', product_id=order.product_id,
        quantity=order.actual_quantity, unit=order.unit, business_date=business_today(),
        business_type='production', reference_no=order.order_no,
    ), 'finished_receipt', production_order=order, production_batches=payload.finished_batches)
    # 成品入库、结算字段、订单状态和审计共享同一个请求事务，任一步失败都会整体回滚。
    order.status = 'completed'
    order.completed_at = order.updated_at = utcnow()
    audit(session, user.id, 'complete_production_order', 'production_orders', order.id,
          {'actual_quantity': decimal_text(order.actual_quantity), 'unit': order.unit})
    session.flush()
    return order_data(session, order)
