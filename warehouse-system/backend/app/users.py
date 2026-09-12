from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.auth import SessionDep, UserDep, audit, hash_password, public_user, require_admin
from app.catalog import is_referenced
from app.models import Role, User, utcnow
from app.schemas import Envelope, PasswordReset, UserCreate, UserUpdate

router = APIRouter(prefix='/api', tags=['用户管理'])


@router.get('/roles', response_model=Envelope)
def roles(user: UserDep, session: SessionDep):
    require_admin(user, session)
    return Envelope(data=[{'id': role.id, 'code': role.code, 'name': role.name} for role in session.scalars(select(Role).order_by(Role.id))])


@router.get('/users', response_model=Envelope)
def list_users(user: UserDep, session: SessionDep, q: str = Query('', max_length=120), page: int = Query(1, ge=1),
               page_size: int = Query(20, ge=1, le=100), is_active: bool | None = None):
    require_admin(user, session)
    conditions = [User.deleted_at.is_(None)]
    if q.strip():
        conditions.append(or_(User.username.contains(q.strip(), autoescape=True), User.display_name.contains(q.strip(), autoescape=True)))
    if is_active is not None:
        conditions.append(User.is_active == is_active)
    total = session.scalar(select(func.count()).select_from(User).where(*conditions))
    rows = session.scalars(select(User).where(*conditions).order_by(User.id.desc()).offset((page-1)*page_size).limit(page_size))
    return Envelope(data={'items': [public_user(row, session) for row in rows], 'total': total, 'page': page, 'page_size': page_size})


def find_user(identity, session):
    row = session.scalar(select(User).where(User.id == identity, User.deleted_at.is_(None)).with_for_update())
    if row is None:
        raise HTTPException(404, '用户不存在')
    return row


def valid_role(role_id, session):
    if session.get(Role, role_id) is None:
        raise HTTPException(422, '角色不存在')


@router.get('/users/{identity}', response_model=Envelope)
def detail(identity: int, user: UserDep, session: SessionDep):
    require_admin(user, session)
    return Envelope(data=public_user(find_user(identity, session), session))


@router.post('/users', response_model=Envelope)
def create(payload: UserCreate, user: UserDep, session: SessionDep):
    require_admin(user, session, serialize_users=True)
    valid_role(payload.role_id, session)
    row = User(**payload.model_dump(exclude={'password'}), password_hash=hash_password(payload.password))
    session.add(row)
    session.flush()
    audit(session, user.id, 'create_user', 'users', row.id)
    return Envelope(data=public_user(row, session))


@router.put('/users/{identity}', response_model=Envelope)
def update(identity: int, payload: UserUpdate, user: UserDep, session: SessionDep):
    require_admin(user, session, serialize_users=True)
    row = find_user(identity, session)
    valid_role(payload.role_id, session)
    if row.id == user.id and (not payload.is_active or payload.role_id != row.role_id):
        raise HTTPException(409, '不能停用自己或修改自己的管理员角色')
    if row.role_id != payload.role_id or row.is_active != payload.is_active:
        row.token_version += 1
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    audit(session, user.id, 'update_user', 'users', row.id)
    session.flush()
    return Envelope(data=public_user(row, session))


@router.post('/users/{identity}/password', response_model=Envelope)
def reset_password(identity: int, payload: PasswordReset, user: UserDep, session: SessionDep):
    require_admin(user, session, serialize_users=True)
    row = find_user(identity, session)
    if row.id == user.id:
        raise HTTPException(409, '请使用右上角“修改密码”修改自己的密码')
    row.password_hash = hash_password(payload.new_password)
    row.token_version += 1
    row.failed_login_count = 0
    row.locked_until = None
    audit(session, user.id, 'reset_password', 'users', row.id)
    return Envelope(message='密码已重置，旧登录已失效')


@router.delete('/users/{identity}', response_model=Envelope)
def remove(identity: int, user: UserDep, session: SessionDep):
    require_admin(user, session, serialize_users=True)
    row = find_user(identity, session)
    if row.id == user.id:
        raise HTTPException(409, '不能删除自己')
    if is_referenced(session, User, identity):
        raise HTTPException(409, '该用户已有操作记录，请停用而不是删除')
    row.is_active = False
    row.deleted_at = utcnow()
    row.token_version += 1
    audit(session, user.id, 'archive_user', 'users', row.id)
    return Envelope(message='用户已软删除，用户名保留')
