"""简单三角色认证；JWT 每次请求核对数据库，停用/改密立即撤销旧令牌。"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session
from app.models import OperationLog, Role, User
from app.schemas import Envelope, LoginInput, PasswordChange
from app.audit_labels import ACTIONS, MODULES

SessionDep = Annotated[Session, Depends(get_session, scope='function')]
bearer = HTTPBearer(auto_error=False)
router = APIRouter(prefix='/api/auth', tags=['登录与账户'])


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 600_000).hex()
    return f'pbkdf2_sha256$600000${salt}${digest}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, digest = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or int(rounds) != 600_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def audit(session, user_id, action, entity_type, entity_id=None, details=None):
    # 只存业务摘要，绝不记录密码、哈希或令牌。
    user = session.get(User, user_id) if user_id else None
    session.add(OperationLog(user_id=user_id, action=action, entity_type=entity_type,
                             entity_id=str(entity_id) if entity_id is not None else None, details=details,
                             username_snapshot=user.username if user else None,
                             description=f'{ACTIONS.get(action, action)} · {MODULES.get(entity_type, entity_type)}'
                             + (f' #{entity_id}' if entity_id is not None else '')))


def public_user(user, session):
    role = session.get(Role, user.role_id)
    return {'id': user.id, 'username': user.username, 'display_name': user.display_name,
            'business_timezone': get_settings().business_timezone,
            'role_id': user.role_id, 'role': role.code, 'role_name': role.name,
            'is_active': user.is_active, 'created_at': user.created_at.isoformat(),
            'updated_at': user.updated_at.isoformat()}


def secret():
    value = get_settings().auth_secret.get_secret_value()
    if len(value) < 32:
        raise HTTPException(503, '认证密钥未配置，请完成本机初始化')
    return value


def current_user(session: SessionDep, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None:
        raise HTTPException(401, '请先登录')
    try:
        payload = jwt.decode(credentials.credentials, secret(), algorithms=['HS256'], audience='warehouse',
                             issuer='warehouse-api', options={'require': ['exp', 'iat', 'sub', 'ver']})
        user = session.get(User, int(payload['sub']))
        if user is None or not user.is_active or user.deleted_at or user.token_version != payload['ver']:
            raise ValueError('revoked')
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise HTTPException(401, '登录已失效，请重新登录') from None
    return user


UserDep = Annotated[User, Depends(current_user)]


def require_admin(user, session, *, serialize_users=False):
    if serialize_users:
        # 用户权限修改按管理员角色行串行，防止两个管理员相互停用后系统失去管理员。
        session.scalar(select(Role).where(Role.code == 'admin').with_for_update())
        session.refresh(user)
    if not user.is_active or user.deleted_at or session.get(Role, user.role_id).code != 'admin':
        raise HTTPException(403, '仅管理员可以执行此操作')


@router.post('/login', response_model=Envelope)
def login(payload: LoginInput, session: SessionDep):
    user = session.scalar(select(User).where(User.username == payload.username, User.deleted_at.is_(None)).with_for_update())
    now = datetime.now(timezone.utc)
    valid = verify_password(payload.password, user.password_hash if user else DUMMY_HASH)
    locked = user is not None and user.locked_until is not None and user.locked_until > now
    if not user or not valid or not user.is_active or locked:
        if user and user.is_active and not locked:
            if user.locked_until and user.locked_until <= now:
                user.failed_login_count = 0
            user.failed_login_count += 1
            if user.failed_login_count >= 5:
                user.locked_until = now + timedelta(minutes=15)
            audit(session, user.id, 'login_failed', 'users', user.id)
        # 返回响应而非抛异常，确保失败计数与安全日志提交。
        return JSONResponse(status_code=401, content={'code': 'LOGIN_FAILED', 'message': '账号或密码不正确，或账号已停用/暂时锁定', 'data': None})
    user.failed_login_count = 0
    user.locked_until = None
    expires = now + timedelta(hours=get_settings().auth_token_hours)
    token = jwt.encode({'sub': str(user.id), 'ver': user.token_version, 'iat': now, 'exp': expires,
                        'iss': 'warehouse-api', 'aud': 'warehouse'}, secret(), algorithm='HS256')
    audit(session, user.id, 'login', 'users', user.id)
    session.flush()
    return Envelope(data={'access_token': token, 'token_type': 'bearer', 'expires_at': expires.isoformat(), 'user': public_user(user, session)})


@router.get('/me', response_model=Envelope)
def me(user: UserDep, session: SessionDep):
    return Envelope(data=public_user(user, session))


@router.post('/logout', response_model=Envelope)
def logout(user: UserDep, session: SessionDep):
    session.refresh(user, with_for_update=True)
    user.token_version += 1
    audit(session, user.id, 'logout', 'users', user.id)
    return Envelope(message='已退出，当前账户的所有旧令牌均已失效')


@router.post('/password', response_model=Envelope)
def change_password(payload: PasswordChange, user: UserDep, session: SessionDep):
    session.refresh(user, with_for_update=True)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, '原密码不正确')
    if payload.current_password == payload.new_password:
        raise HTTPException(422, '新密码不能与原密码相同')
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    audit(session, user.id, 'change_password', 'users', user.id)
    return Envelope(message='密码已修改，请重新登录')
