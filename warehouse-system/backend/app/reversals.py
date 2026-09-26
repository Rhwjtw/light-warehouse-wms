"""Read-only stock document details and first-batch outbound reversals."""
from fastapi import APIRouter, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from app.auth import SessionDep, UserDep
from app.models import StockDocument, User
from app.schemas import Envelope, StockReversalInput
from app.stock import page_result
from app.reversal_service import document_detail, reversal_preview, reverse_document

router = APIRouter(prefix='/api/stock-documents', tags=['库存单据冲销'])


@router.get('/reversals', response_model=Envelope)
def list_reversals(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    original = aliased(StockDocument)
    query = select(StockDocument.id, StockDocument.document_no, StockDocument.created_at,
                   StockDocument.reverses_document_id, StockDocument.original_document_no_snapshot,
                   StockDocument.reverses_kind, StockDocument.reversal_reason, StockDocument.notes,
                   User.display_name.label('operator_name'))
    query = query.join(original, original.id == StockDocument.reverses_document_id)
    query = query.join(User, User.id == StockDocument.operator_id).where(StockDocument.kind == 'reversal')
    if q.strip():
        query = query.where(or_(StockDocument.document_no.contains(q.strip(), autoescape=True),
                                StockDocument.original_document_no_snapshot.contains(q.strip(), autoescape=True)))
    return page_result(session, query.order_by(StockDocument.id.desc()), page, page_size)


@router.get('/{identity}', response_model=Envelope)
def detail(identity: int, user: UserDep, session: SessionDep):
    return Envelope(data=document_detail(session, identity))


@router.get('/{identity}/reversal-preview', response_model=Envelope)
def preview(identity: int, user: UserDep, session: SessionDep):
    return Envelope(data=reversal_preview(session, identity))


@router.post('/{identity}/reverse', response_model=Envelope)
def reverse(identity: int, payload: StockReversalInput, user: UserDep, session: SessionDep):
    return Envelope(data=reverse_document(session, user, identity, payload))
