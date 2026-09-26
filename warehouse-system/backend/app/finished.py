"""成品查询及记账接口；不提供编辑或删除历史记录的入口。"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func, case, or_
from sqlalchemy.orm import aliased

from app.auth import SessionDep, UserDep
from app.models import FinishedProduct, FinishedBatch, FinishedReceipt, StockDocument, InventoryTransaction, User
from app.schemas import Envelope, FinishedStockInput
from app.stock import page_result
from app.finished_service import post_finished

router = APIRouter(prefix='/api', tags=['成品库存'])


def inventory_query():
    # 第一版对外呈现成品汇总库存，不暴露内部 __aggregate__ 库存行。
    balances = select(FinishedBatch.product_id, func.sum(FinishedBatch.remaining_quantity).label('quantity')).group_by(FinishedBatch.product_id).subquery()
    times = select(FinishedBatch.product_id,
                   func.max(case((InventoryTransaction.kind == 'finished_receipt', InventoryTransaction.created_at))).label('last_receipt_at'),
                   func.max(case((InventoryTransaction.kind == 'finished_outbound', InventoryTransaction.created_at))).label('last_outbound_at'))
    times = times.join(InventoryTransaction, InventoryTransaction.finished_batch_id == FinishedBatch.id).group_by(FinishedBatch.product_id).subquery()
    return select(FinishedProduct.id, FinishedProduct.code, FinishedProduct.name, FinishedProduct.unit, FinishedProduct.is_active,
                  func.coalesce(balances.c.quantity, 0).label('quantity'), times.c.last_receipt_at, times.c.last_outbound_at
                  ).outerjoin(balances, balances.c.product_id == FinishedProduct.id).outerjoin(times, times.c.product_id == FinishedProduct.id
                  ).where(FinishedProduct.deleted_at.is_(None))


@router.get('/finished-stock', response_model=Envelope)
def inventory(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    query = inventory_query()
    if q.strip():
        query = query.where(or_(FinishedProduct.name.contains(q.strip(), autoescape=True), FinishedProduct.code.contains(q.strip(), autoescape=True)))
    return page_result(session, query.order_by(FinishedProduct.id), page, page_size)


@router.get('/finished-stock/{identity}', response_model=Envelope)
def inventory_detail(identity: int, user: UserDep, session: SessionDep):
    result = page_result(session, inventory_query().where(FinishedProduct.id == identity), 1, 1)
    if not result.data['items']:
        raise HTTPException(404, '成品不存在')
    return Envelope(data=result.data['items'][0])


def records_query(kind=None, q='', product_id=None):
    reversed_document = aliased(StockDocument)
    query = select(StockDocument.id, StockDocument.document_no, StockDocument.kind, StockDocument.created_at,
                   StockDocument.reverses_document_id, reversed_document.id.label('reversed_by_document_id'),
                   StockDocument.business_date, StockDocument.business_type, StockDocument.reference_no, StockDocument.notes,
                   FinishedProduct.id.label('product_id'), FinishedProduct.code.label('product_code'), FinishedProduct.name.label('product_name'),
                   InventoryTransaction.unit, InventoryTransaction.before_quantity, InventoryTransaction.change_quantity,
                   InventoryTransaction.after_quantity, func.abs(InventoryTransaction.change_quantity).label('quantity'),
                   User.display_name.label('operator_name'), FinishedReceipt.production_order_id)
    query = query.select_from(StockDocument).join(InventoryTransaction, InventoryTransaction.document_id == StockDocument.id)
    query = query.outerjoin(reversed_document, reversed_document.reverses_document_id == StockDocument.id)
    query = query.join(FinishedBatch, FinishedBatch.id == InventoryTransaction.finished_batch_id).join(FinishedProduct)
    query = query.join(User, User.id == StockDocument.operator_id).outerjoin(FinishedReceipt, FinishedReceipt.document_id == StockDocument.id)
    if kind:
        query = query.where(StockDocument.kind == kind)
    if product_id:
        query = query.where(FinishedProduct.id == product_id)
    if q.strip():
        query = query.where(or_(*(col.contains(q.strip(), autoescape=True) for col in
                                (FinishedProduct.name, FinishedProduct.code, StockDocument.document_no, StockDocument.reference_no))))
    return query.order_by(StockDocument.id.desc())


def register_records(path, kind):
    # 普通成品入库和出库共享同一组列表/详情接口；生产入库不会经过这里，而由完工结算调用服务层。
    def create(payload: FinishedStockInput, user: UserDep, session: SessionDep):
        return Envelope(data=post_finished(session, user, payload, kind))

    def records(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
                product_id: int | None = Query(None, gt=0), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        return page_result(session, records_query(kind, q, product_id), page, page_size)

    def detail(identity: int, user: UserDep, session: SessionDep):
        result = page_result(session, records_query(kind).where(StockDocument.id == identity), 1, 1)
        if not result.data['items']:
            raise HTTPException(404, '成品单据不存在')
        return Envelope(data=result.data['items'][0])

    router.add_api_route('/' + path, create, methods=['POST'], response_model=Envelope, name=path+'_create')
    router.add_api_route('/' + path, records, methods=['GET'], response_model=Envelope, name=path+'_list')
    router.add_api_route('/' + path+'/{identity}', detail, methods=['GET'], response_model=Envelope, name=path+'_detail')


register_records('finished-receipts', 'finished_receipt')
register_records('finished-outbounds', 'finished_outbound')


@router.get('/finished-transactions', response_model=Envelope)
def transactions(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
                 product_id: int | None = Query(None, gt=0), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return page_result(session, records_query(q=q, product_id=product_id), page, page_size)
