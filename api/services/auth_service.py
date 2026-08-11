"""V1.8.0 认证（T02）· 认证服务（登录 / 刷新 / 登出 / me / 改密 / dev-login）。

**职责**（架构 auth-architecture §3.5 AuthService + PRD §5.1-5.4/5.8）：
- ``login``：校验 + 锁定 + 防枚举（dummy bcrypt 时序均衡）+ 签发双 token + 审计；
- ``refresh``：轮换（旧行 revoked_at + replaced_by 成链）+ 禁用/改密撤销拒绝；
- ``logout``：按 hash revoke（幂等）+ 审计；
- ``get_me``：当前用户 + 密码过期信息；
- ``change_password``：验证旧密码 → 策略校验 → 更新 hash → 撤销全部 refresh；
- ``dev_login``：**仅非生产**签发带 role claim 的真实 JWT（生产 404）。

**JWT claims（共享知识 #1）**：
``sub``/``user_id`` = users.id；``role`` = 5 角色之一；``name`` = 展示名；
``iss`` = settings.jwt_issuer；``iat``/``exp`` 必备；**绝不注入 ``thread_id``**
（否则 verify_thread_ownership._claim_fast_path 会对无绑定会话的请求 403）。

**防枚举（共享知识 #3）**：账号不存在也执行一次 dummy bcrypt 比对（时序均衡），
失败统一 401「用户名或密码错误」；禁用 403（仅密码验证通过后返回）；
锁定 423 + Retry-After。

**循环依赖规避（共享知识 #11）**：对 UserService / rbac 一律函数内 lazy import。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import HTTPException

from api.config import settings
from api.services.auth_audit_service import AuthAuditService
from mcp_tools.db.database import get_connection

#: 防枚举 dummy hash（懒加载单例；账号不存在时也执行一次 bcrypt 比对）
_DUMMY_BCRYPT_HASH: str | None = None


def _dummy_bcrypt_hash() -> str:
    """获取防枚举用 dummy bcrypt hash（首次调用时生成，避免拖慢 import）。"""
    global _DUMMY_BCRYPT_HASH
    if _DUMMY_BCRYPT_HASH is None:
        _DUMMY_BCRYPT_HASH = bcrypt.hashpw(
            b"gridmind-dummy-password-for-timing",
            bcrypt.gensalt(rounds=12),
        ).decode("utf-8")
    return _DUMMY_BCRYPT_HASH


def _now_iso() -> str:
    """当前 UTC ISO 时间串。"""
    return datetime.now(timezone.utc).isoformat()


def _now_iso_plus(*, minutes: int = 0, seconds: int = 0) -> str:
    """当前 UTC ISO 时间 + 偏移（分钟/秒）。"""
    return (
        datetime.now(timezone.utc)
        + timedelta(minutes=minutes, seconds=seconds)
    ).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    """解析 ISO 时间串（无时区视为 UTC）；失败 → None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _display_name(user: dict[str, Any]) -> str:
    """展示名：users 表无独立 display_name 列（PRD 契约要求）→ 用 username。"""
    return user.get("username") or user.get("id") or "访客"


class AuthService:
    """认证全业务（登录 / 注册 / 刷新 / 登出 / me / 改密 / dev-login）。"""

    # ── 登录 ─────────────────────────────────────────────────

    def login(
        self,
        username: str,
        password: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """校验 + 锁定 + 防枚举 + 签发 access/refresh + 审计。

        Raises:
            HTTPException 401: 用户名或密码错误（统一文案，防枚举）。
            HTTPException 403: 账号已被禁用（密码验证通过后）。
            HTTPException 423: 账号已锁定（Retry-After 头带剩余秒）。
        """
        from api.services.user_service import UserService

        us = UserService()
        uname = (username or "").strip().lower()
        user = us.get_by_username(uname)

        # 1) 账号不存在：dummy bcrypt 时序均衡 + 审计（不暴露存在性）
        if user is None:
            us._verify_password(password, _dummy_bcrypt_hash())
            AuthAuditService.record(
                "login_failed",
                username=uname,
                ip_address=ip,
                user_agent=user_agent,
                detail="user_not_found",
            )
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        # 2) 锁定检查（先于密码校验：锁定期即使密码正确也拒绝）
        if _is_locked(user):
            remaining = _lock_remaining_seconds(user)
            AuthAuditService.record(
                "account_locked",
                user_id=user["id"],
                username=user["username"],
                ip_address=ip,
                user_agent=user_agent,
                detail="locked_until_active",
            )
            raise HTTPException(
                status_code=423,
                detail="尝试次数过多，账号已锁定，请稍后再试",
                headers={"Retry-After": str(max(1, int(remaining)))},
            )

        # 3) 密码校验（失败 → 计数递增 + 可能锁定 + 401 统一文案）
        if not us._verify_password(password, user["password_hash"]):
            self._record_failed_attempt(user, ip, user_agent)
            raise HTTPException(status_code=401, detail="用户名或密码错误")

        # 4) 密码正确但禁用：403（仅在验证通过后泄漏状态，防枚举）
        if int(user.get("disabled") or 0) == 1:
            raise HTTPException(status_code=403, detail="账号已被禁用")

        # 5) 成功：清零失败计数 + 更新 last_login_at
        self._mark_login_success(user)
        user = us.get_by_username(uname) or user  # 重新读取更新后的行

        return self._issue_tokens(user, ip, user_agent, event="login_success")

    # ── 注册（开放自助注册，默认 dispatcher，注册即登录）──────────────

    def register(
        self,
        username: str,
        password: str,
        email: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """开放注册：默认 ``dispatcher`` 最小权限 + 注册即登录。

        编排（架构 register-rbac §1.1 + 共享知识 #7，**零复制 login 逻辑**）：
        1. ``UserService.create_user(role="dispatcher", must_change_password=False,
           actor_id="register", ...)`` —— 409/422 语义直接继承；内部另记
           ``user_created``（detail="actor=register role=dispatcher"）；
        2. 成功 → ``_issue_tokens(event="register_success")`` —— 签发 access/
           refresh（claims 与 login 同构，**不含 thread_id**）+ refresh 落库
           + 审计一条龙复用；
        3. 失败（HTTPException 409/422）→ 审计 ``register_failed``
           （detail 记状态码+文案，不存密码/明文 token）后 re-raise。

        Args:
            username: 登录名（小写唯一）。
            password: 密码（须满足策略：≥8 位 + 数字 + 字母）。
            email: 可选邮箱（非空时唯一；与 create_user 一致不校验格式）。
            ip / user_agent: 审计上下文。

        Returns:
            LoginResponse 同构 dict（access+refresh+user{role=dispatcher}）。

        Raises:
            HTTPException 409: 用户名/邮箱已存在。
            HTTPException 422: 用户名非法 / 密码不满足策略。
        """
        from api.services.user_service import UserService

        try:
            user = UserService().create_user(
                username=username,
                password=password,
                role="dispatcher",
                email=email,
                actor_id="register",
                ip_address=ip,
                user_agent=user_agent,
                must_change_password=False,
            )
        except HTTPException as exc:
            AuthAuditService.record(
                "register_failed",
                username=(username or "").strip().lower(),
                ip_address=ip,
                user_agent=user_agent,
                detail=f"{exc.status_code}: {exc.detail}",
            )
            raise

        return self._issue_tokens(user, ip, user_agent, event="register_success")

    # ── 刷新（轮换）──────────────────────────────────────────

    def refresh(
        self,
        refresh_token: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """用 refresh token 换新 access + **轮换**新 refresh（旧行 revoked）。

        轮换链（共享知识 #2）：旧行 ``revoked_at=now`` + ``replaced_by=新行id``，
        旧 token 二次使用 → 401。

        Raises:
            HTTPException 401: refresh 无效/过期/已撤销/用户被禁用。
        """
        if not refresh_token:
            raise HTTPException(status_code=401, detail="refresh token 无效或已过期")

        token_hash = self._hash_refresh(refresh_token)
        conn = get_connection()
        try:
            # ── 原子轮换（P1 安全修复）：BEGIN IMMEDIATE 在 SELECT 前获取写锁 ──
            # 原实现（deferred 事务）存在并发竞态：两个携带**同一 refresh token**
            # 的并发请求可在任一者写库前都通过 revoked_at 检查 → 双双轮换成功
            # （同一 refresh 产生两个有效会话，构成 replay 窗口）。BEGIN IMMEDIATE
            # 使第二个请求在 BEGIN 处阻塞，直到第一个 commit 后才读到 revoked_at
            # → 401，保证「同一 token 至多成功轮换一次」（前端 401 拦截器并发
            # 去重 + 本后端原子性双保险，见 auth.ts::refreshInFlight）。
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=401, detail="refresh token 无效或已过期")
            row = dict(row)
            if row.get("revoked_at"):
                raise HTTPException(status_code=401, detail="refresh token 无效或已过期")
            expires_dt = _parse_iso(row.get("expires_at"))
            if expires_dt is None or expires_dt <= datetime.now(timezone.utc):
                raise HTTPException(status_code=401, detail="refresh token 无效或已过期")

            user_row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (row["user_id"],)
            ).fetchone()
            if user_row is None:
                raise HTTPException(status_code=401, detail="refresh token 无效或已过期")
            user = dict(user_row)
            if int(user.get("disabled") or 0) == 1:
                # 禁用用户 refresh 拒绝（共享知识 #2）
                raise HTTPException(status_code=401, detail="refresh token 无效或已过期")

            # 轮换：先插新行（拿新 id），再撤销旧行并成链
            rotated_token = secrets.token_urlsafe(48)
            new_hash = self._hash_refresh(rotated_token)
            now = _now_iso()
            expires_at = _now_iso_plus(seconds=settings.jwt_refresh_ttl_seconds)
            cur = conn.execute(
                "INSERT INTO refresh_tokens "
                "(user_id, token_hash, expires_at, created_at, user_agent, ip_address) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], new_hash, expires_at, now, user_agent, ip),
            )
            new_id = cur.lastrowid
            conn.execute(
                "UPDATE refresh_tokens SET revoked_at = ?, replaced_by = ? WHERE id = ?",
                (now, new_id, row["id"]),
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception:
            # 任何写异常 → 回滚，保持轮换原子性（不留半成品链）
            conn.rollback()
            raise
        finally:
            conn.close()

        AuthAuditService.record(
            "refresh",
            user_id=user["id"],
            username=user["username"],
            ip_address=ip,
            user_agent=user_agent,
            detail=f"rotated:{row['id']}->{new_id}",
        )
        return self._build_token_response(
            user,
            access_token=self._build_access_token(user),
            refresh_token=rotated_token,
        )

    # ── 登出（幂等）──────────────────────────────────────────

    def logout(self, refresh_token: str) -> None:
        """按 hash revoke 对应 refresh 行（幂等——不存在/已撤销也成功）。"""
        if not refresh_token:
            return
        token_hash = self._hash_refresh(refresh_token)
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id, user_id FROM refresh_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = ? WHERE id = ?",
                    (_now_iso(), row["id"]),
                )
                conn.commit()
                AuthAuditService.record(
                    "logout",
                    user_id=row["user_id"],
                    ip_address=None,
                    user_agent=None,
                    detail="revoked",
                )
        finally:
            conn.close()

    # ── 当前用户（me）────────────────────────────────────────

    def get_me(self, user_id: str) -> dict[str, Any]:
        """当前用户信息 + 密码过期信息（90 天策略，拍板 #2）。

        Raises:
            HTTPException 401: 用户不存在或已被禁用。
        """
        from api.services.user_service import UserService

        user = UserService().get_user(user_id)
        if user is None or int(user.get("disabled") or 0) == 1:
            raise HTTPException(status_code=401, detail="用户不存在或已被禁用")

        expires_at, expiring = _password_expiry(user)
        return {
            "id": user["id"],
            "username": user["username"],
            "display_name": _display_name(user),
            "role": user["role"],
            "must_change_password": bool(int(user.get("must_change_password") or 0)),
            "last_login_at": user.get("last_login_at"),
            "password_expires_at": expires_at,
            "password_expiring": expiring,
        }

    # ── 改密 ─────────────────────────────────────────────────

    def change_password(
        self,
        user_id: str,
        old_password: str,
        new_password: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """验证旧密码 → 策略校验 → 更新 hash → 撤销该用户全部 refresh。

        Raises:
            HTTPException 401: 旧密码错误 / 用户不存在。
            HTTPException 422: 新密码不满足策略。
        """
        from api.services.user_service import UserService

        us = UserService()
        user = us.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在")

        if not us._verify_password(old_password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="当前密码错误")

        us._validate_password(new_password)  # 422
        new_hash = us._hash_password(new_password)
        now = _now_iso()

        conn = get_connection()
        try:
            history = _append_password_history(user.get("password_history"), new_hash)
            conn.execute(
                "UPDATE users SET password_hash = ?, password_changed_at = ?, "
                "must_change_password = 0, password_history = ?, updated_at = ? "
                "WHERE id = ?",
                (new_hash, now, history, now, user_id),
            )
            # 改密后撤销该用户全部 refresh（AC8-5 安全兜底）
            conn.execute(
                "UPDATE refresh_tokens SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
            conn.commit()
        finally:
            conn.close()

        AuthAuditService.record(
            "password_changed",
            user_id=user["id"],
            username=user["username"],
            ip_address=ip,
            user_agent=user_agent,
            detail="self_change",
        )

    # ── dev-login（仅非生产）─────────────────────────────────

    def dev_login(
        self,
        role: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """dev 联调：签发带 role claim 的真实 JWT（生产必须 404，fail-closed）。

        dev 用户行（``id=f"dev-{role}"``）懒创建——refresh_tokens.user_id 有
        外键约束（PRAGMA foreign_keys=ON），必须先有 users 行才能生成 refresh，
        从而让 dev 模式完整支持 401→refresh 联调链路。
        """
        if settings.is_production:
            raise HTTPException(status_code=404, detail="Not Found")

        from api.services.rbac import ROLE_VALUES
        from api.services.user_service import UserService

        role = (role or "").strip().lower()
        if role not in ROLE_VALUES:
            raise HTTPException(status_code=422, detail="无效角色")

        us = UserService()
        # 优先按用户名查（兼容历史 dev 行：旧代码曾用随机 UUID id 创建）
        user = us.get_by_username(f"dev-{role}")
        if user is None:
            # 不存在 → 以稳定 id = f"dev-{role}" 创建（幂等复用，refresh FK 可引用）
            user = us.create_user(
                username=f"dev-{role}",
                password=secrets.token_urlsafe(24),
                role=role,
                email=None,
                actor_id="dev-login",
                ip_address=ip,
                user_agent=user_agent,
                must_change_password=False,
                user_id=f"dev-{role}",
            )
        else:
            user = dict(user)

        return self._issue_tokens(user, ip, user_agent, event="dev_login")

    # ── 内部：token 签发 / 刷新存储 ──────────────────────────

    def _issue_tokens(
        self,
        user: dict[str, Any],
        ip: str | None,
        user_agent: str | None,
        event: str,
    ) -> dict[str, Any]:
        """登录/登出后签发 access + refresh（统一响应结构）。"""
        access_token = self._build_access_token(user)
        refresh_token = self._generate_refresh_token(user, ip, user_agent)
        AuthAuditService.record(
            event,
            user_id=user["id"],
            username=user["username"],
            ip_address=ip,
            user_agent=user_agent,
        )
        return self._build_token_response(user, access_token=access_token, refresh_token=refresh_token)

    def _build_token_response(
        self,
        user: dict[str, Any],
        access_token: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        """组装 LoginResponse 同构 dict（access/refresh 显式传入）。"""
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": settings.jwt_access_ttl_seconds,
            "mfa_required": False,
            "user": {
                "id": user["id"],
                "username": user["username"],
                "display_name": _display_name(user),
                "role": user["role"],
            },
        }

    def _build_access_token(self, user: dict[str, Any]) -> str:
        """签发 access JWT（claims 与 issue_test_token 同构 + role/name）。

        **绝不注入 thread_id**（共享知识 #1）——否则
        ``verify_thread_ownership._claim_fast_path`` 会对无绑定会话的请求 403。
        """
        now = int(time.time())
        payload: dict[str, Any] = {
            "sub": user["id"],
            "user_id": user["id"],
            "role": user["role"],
            "name": _display_name(user),
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + settings.jwt_access_ttl_seconds,
        }
        return jwt.encode(
            payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )

    def _generate_refresh_token(
        self,
        user: dict[str, Any],
        ip: str | None,
        ua: str | None,
    ) -> str:
        """生成 opaque refresh token，DB 只存 SHA-256 hash（明文仅返回一次）。"""
        token = secrets.token_urlsafe(48)
        token_hash = self._hash_refresh(token)
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO refresh_tokens "
                "(user_id, token_hash, expires_at, created_at, user_agent, ip_address) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    user["id"],
                    token_hash,
                    _now_iso_plus(seconds=settings.jwt_refresh_ttl_seconds),
                    _now_iso(),
                    ua,
                    ip,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return token

    def _revoke_refresh(self, token_hash: str, replaced_by: int | None = None) -> None:
        """按 hash revoke（轮换时附 replaced_by 成链；登出/改密直接置值）。"""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE refresh_tokens SET revoked_at = ?, replaced_by = ? "
                "WHERE token_hash = ?",
                (_now_iso(), replaced_by, token_hash),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _hash_refresh(token: str) -> str:
        """SHA-256(refresh_token) hexdigest（DB 只存 hash，不存明文）。"""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # ── 内部：失败计数 / 锁定 ────────────────────────────────

    def _record_failed_attempt(
        self,
        user: dict[str, Any],
        ip: str | None,
        ua: str | None,
    ) -> None:
        """连续失败计数递增；达到阈值 → 锁定 ACCOUNT_LOCK_MINUTES 分钟。"""
        conn = get_connection()
        try:
            failed = int(user.get("failed_attempts") or 0) + 1
            locked_until = None
            detail = "password_mismatch"
            if failed >= settings.account_lock_threshold:
                locked_until = _now_iso_plus(minutes=settings.account_lock_minutes)
                detail = "account_locked"
            conn.execute(
                "UPDATE users SET failed_attempts = ?, locked_until = ?, updated_at = ? "
                "WHERE id = ?",
                (failed, locked_until, _now_iso(), user["id"]),
            )
            conn.commit()
        finally:
            conn.close()
        AuthAuditService.record(
            "login_failed",
            user_id=user["id"],
            username=user["username"],
            ip_address=ip,
            user_agent=ua,
            detail=detail,
        )

    def _mark_login_success(self, user: dict[str, Any]) -> None:
        """登录成功：清零失败计数 + 解除锁定 + 更新 last_login_at。"""
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL, "
                "last_login_at = ?, updated_at = ? WHERE id = ?",
                (_now_iso(), _now_iso(), user["id"]),
            )
            conn.commit()
        finally:
            conn.close()


# ═══════════════════════════════════════════════════════
# 模块级工具（锁定判断 / 密码过期）
# ═══════════════════════════════════════════════════════


def _is_locked(user: dict[str, Any]) -> bool:
    """锁定判断：locked_until 存在且未过期。"""
    locked_dt = _parse_iso(user.get("locked_until"))
    if locked_dt is None:
        return False
    return locked_dt > datetime.now(timezone.utc)


def _lock_remaining_seconds(user: dict[str, Any]) -> int:
    """锁定剩余秒数（Retry-After 头用；解析失败回退整个锁定期）。"""
    locked_dt = _parse_iso(user.get("locked_until"))
    if locked_dt is None:
        return settings.account_lock_minutes * 60
    remaining = (locked_dt - datetime.now(timezone.utc)).total_seconds()
    return max(1, int(remaining))


def _password_expiry(
    user: dict[str, Any],
) -> tuple[str | None, bool]:
    """计算密码过期时间与「临近过期/已过期」标记（≤7 天或已过期 → expiring）。"""
    changed = user.get("password_changed_at")
    if not changed:
        return None, False
    changed_dt = _parse_iso(changed)
    if changed_dt is None:
        return None, False
    expires_dt = changed_dt + timedelta(days=settings.password_expiry_days)
    expires_at = expires_dt.isoformat()
    now = datetime.now(timezone.utc)
    expiring = now >= (expires_dt - timedelta(days=7))
    return expires_at, expiring


def _append_password_history(existing_json: str | None, new_hash: str) -> str:
    """向 password_history JSON 追加新 hash（保留最近 3 条，P2 去重预留）。"""
    import json

    try:
        history = json.loads(existing_json) if existing_json else []
    except (TypeError, ValueError):
        history = []
    if not isinstance(history, list):
        history = []
    history.append(new_hash)
    history = history[-3:]
    return json.dumps(history)
