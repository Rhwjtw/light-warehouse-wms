"""Authenticated read-only trace entrances; write authority is never granted here."""
from fastapi import APIRouter, Query, HTTPException
from app.auth import UserDep, SessionDep
from app.schemas import Envelope
from app import traceability_service as service

router = APIRouter(prefix='/api/traceability', tags=['批次追溯'])


@router.get('/search', response_model=Envelope)
def search(user: UserDep, session: SessionDep, q: str = Query(min_length=1, max_length=120), limit: int = Query(30, ge=1, le=100)):
    if not q.strip():
        raise HTTPException(422, '请输入批次或生产单搜索内容')
    with service.read_snapshot(session) as snapshot:
        return Envelope(data=service.search(snapshot, q.strip(), limit))


@router.get('/material-batches/{identity}', response_model=Envelope)
def material(identity: int, user: UserDep, session: SessionDep):
    with service.read_snapshot(session) as snapshot:
        return Envelope(data=service.material_trace(snapshot, identity))


@router.get('/finished-batches/{identity}', response_model=Envelope)
def finished(identity: int, user: UserDep, session: SessionDep):
    with service.read_snapshot(session) as snapshot:
        return Envelope(data=service.finished_trace(snapshot, identity))


@router.get('/production-orders/{identity}', response_model=Envelope)
def production(identity: int, user: UserDep, session: SessionDep):
    with service.read_snapshot(session) as snapshot:
        trace = service.order_trace(snapshot, identity)
        return Envelope(data={'entry_type': 'production', 'subject': {'order_no': trace['order_no']},
                              'production_orders': [trace], 'documents': trace['documents'], 'warnings': trace['warnings']})
