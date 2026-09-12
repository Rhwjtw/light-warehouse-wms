"""四类基础档案共用简单的搜索、分页和审计逻辑，不提供库存数字编辑。"""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.auth import SessionDep, UserDep, audit, require_admin
from app.database import Base
from app.models import FinishedProduct, Location, RawMaterial, Supplier, Unit, utcnow
from app.schemas import ArchiveInput, Envelope, MaterialInput, ProductInput, SupplierInput

router = APIRouter(prefix='/api', tags=['基础资料'])


def is_referenced(session, model, identity):
    """检查数据库真实外键，包括将来已注册的业务表，历史引用的档案只允许停用。"""
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table is model.__table__:
                if session.execute(select(fk.parent).where(fk.parent == identity).limit(1)).first():
                    return True
    return False


def public_record(record):
    from datetime import datetime
    from decimal import Decimal
    result = {}
    for column in record.__table__.columns:
        if column.name == 'deleted_at':
            continue
        value = getattr(record, column.name)
        if isinstance(value, Decimal):
            value = format(value, 'f')
        elif isinstance(value, datetime):
            value = value.isoformat()
        result[column.name] = value
    return result


@router.get('/units', response_model=Envelope)
def units(user: UserDep, session: SessionDep):
    return Envelope(data=[{'code': row.code, 'dimension': row.dimension, 'factor': str(row.factor)}
                          for row in session.scalars(select(Unit).order_by(Unit.code))])


def register_catalog(path, model, schema, label):
    def validate(payload, session, existing=None):
        if hasattr(payload, 'unit'):
            unit = session.get(Unit, payload.unit)
            allowed = ('kg', 'g', 'L', 'mL', '个') if model is RawMaterial else ('盒', '瓶', '支', '套', '个')
            if unit is None or payload.unit not in allowed:
                raise HTTPException(422, '请选择支持的单位')
            if existing and existing.unit != payload.unit and is_referenced(session, model, existing.id):
                raise HTTPException(409, '该档案已被库存或配方引用，不能更改单位；请新建档案')

    def find(identity, session, lock=False):
        query = select(model).where(model.id == identity, model.deleted_at.is_(None))
        if lock:
            query = query.with_for_update()
        record = session.scalar(query)
        if record is None:
            raise HTTPException(404, f'{label}不存在')
        return record

    def list_records(user: UserDep, session: SessionDep, q: str = Query('', max_length=120),
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), is_active: bool | None = None):
        conditions = [model.deleted_at.is_(None)]
        if q.strip():
            # autoescape 将 %、_ 当作普通查询字符，避免通配符扩大范围。
            conditions.append(or_(model.name.contains(q.strip(), autoescape=True), model.code.contains(q.strip(), autoescape=True)))
        if is_active is not None:
            conditions.append(model.is_active == is_active)
        total = session.scalar(select(func.count()).select_from(model).where(*conditions))
        records = session.scalars(select(model).where(*conditions).order_by(model.id.desc()).offset((page-1)*page_size).limit(page_size))
        return Envelope(data={'items': [public_record(row) for row in records], 'total': total, 'page': page, 'page_size': page_size})

    def get_record(identity: int, user: UserDep, session: SessionDep):
        return Envelope(data=public_record(find(identity, session)))

    def create_record(payload: schema, user: UserDep, session: SessionDep):
        require_admin(user, session)
        validate(payload, session)
        record = model(**payload.model_dump())
        session.add(record)
        session.flush()
        audit(session, user.id, 'create', model.__tablename__, record.id, {'code': record.code})
        return Envelope(data=public_record(record))

    def update_record(identity: int, payload: schema, user: UserDep, session: SessionDep):
        require_admin(user, session)
        record = find(identity, session, True)
        validate(payload, session, record)
        for field, value in payload.model_dump().items():
            setattr(record, field, value)
        audit(session, user.id, 'update', model.__tablename__, record.id, {'code': record.code})
        session.flush()
        return Envelope(data=public_record(record))

    def delete_record(identity: int, user: UserDep, session: SessionDep):
        require_admin(user, session)
        record = find(identity, session, True)
        if is_referenced(session, model, identity):
            raise HTTPException(409, '该档案已被业务引用，请使用停用，不能删除')
        record.is_active = False
        record.deleted_at = utcnow()
        audit(session, user.id, 'archive', model.__tablename__, identity, {'code': record.code})
        return Envelope(message='已软删除；历史编码保留，不可重复使用')

    for suffix, method, endpoint, title in (
        ('', 'GET', list_records, '列表'), ('/{identity}', 'GET', get_record, '详情'),
        ('', 'POST', create_record, '新增'), ('/{identity}', 'PUT', update_record, '编辑'),
        ('/{identity}', 'DELETE', delete_record, '软删除'),
    ):
        router.add_api_route('/'+path+suffix, endpoint, methods=[method], response_model=Envelope,
                             name=f'{path}_{method}_{suffix}', summary=label+title)


register_catalog('materials', RawMaterial, MaterialInput, '原材料')
register_catalog('products', FinishedProduct, ProductInput, '成品')
register_catalog('suppliers', Supplier, SupplierInput, '供应商')
register_catalog('locations', Location, ArchiveInput, '库位')
