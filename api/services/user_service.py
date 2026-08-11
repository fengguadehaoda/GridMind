"""V1.8.0 认证（T03）· 用户管理服务。

**职责**（架构 auth-architecture §3.5 UserService + PRD §五 5.5-5.7）：
- ``ensure_initial_admin``：lifespan 幂等创建初始 admin（生产 fail-closed）；
- ``list_users / get_by_username / get_user``：查询（**不含 password_hash**）；
- ``create_user / update_user``：管理员创建/改角色/禁用/改密 + 审计；
- 密码策略：``_validate_password``（≥8 位 + 数字 + 字母）、``_hash_password``
  （bcrypt cost 12 + 72 字节截断）、``_verify_password``；
- 防呆：``_guard_last_admin``（最后一个 admin 禁禁用/降级 409）。

**与 AuthService 的循环依赖规避**（架构共享知识 #11）：
- 本模块对 ``api.config`` 模块级 import（config 不依赖 services，无环）；
- 对 AuthService / 其它 services 一律**函数内 lazy import**；
- 测试隔离：测试文件 monkeypatch 本模块的 ``get_connection`` 属性即可
  （沿用 hitl_audit_service 的既有模式）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from fastapi import HTTPException
from loguru import logger

from api.config import settings
from api.services.auth_audit_service import AuthAuditService
from api.services.rbac import ROLE_VALUES
from mcp_tools.db.database import get_connection

#: 用户名合法字符（小写字母 / 数字 / _ - .；对齐登录标识 username 约定）
_USERNAME_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")

#: 密码强度：至少一个数字 + 至少一个字母（主理人拍板 #2）
_DIGIT_RE = re.compile(r"\d")
_LETTER_RE = re.compile(r"[A-Za-z]")

#: 密码历史保留条数（P2 去重用，本批仅存储不强制）
_PASSWORD_HISTORY_LIMIT = 3


def _now_iso() -> str:
    """当前 UTC ISO 时间串（与 auth_service / auth_audit_service 同格式）。"""
    return datetime.now(timezone.utc).isoformat()


class UserService:
    """用户 CRUD + 初始管理员 + 密码策略（SQLite 直连主库）。"""

    # ── 初始管理员 ────────────────────────────────────────────

    def ensure_initial_admin(self) -> None:
        """幂等：users 表无 ``admin`` 时创建初始管理员。

        策略（架构 §1.3 + 主理人拍板 #8 + 待明确 #1）：
        - 已存在 ``admin`` 用户名 → 直接返回（重复启动零副作用）；
        - 生产（``settings.is_production``）且未配置 ``ADMIN_INITIAL_PASSWORD``
          → **SystemExit**（fail-closed，对齐 JWT_SECRET 门禁）；
        - 生产已配置 → 用 env 密码创建；
        - dev → 用 env 密码或固定 dev 密码 ``Admin@123456`` 并日志告警。

        创建的用户一律 ``must_change_password=1``（首次登录强制改密）。
        """
        if self.get_by_username("admin") is not None:
            return

        if settings.is_production:
            pwd = settings.admin_initial_password
            if not pwd:
                raise SystemExit(
                    "[FATAL] APP_ENV=production 且 users 表无 admin，"
                    "但 ADMIN_INITIAL_PASSWORD 未配置。\n"
                    "        请在 .env 中设置强随机 ADMIN_INITIAL_PASSWORD"
                    "（如 `openssl rand -base64 18`）后重试。"
                )
            logger.info("Creating initial admin (production, env ADMIN_INITIAL_PASSWORD)")
        else:
            pwd = settings.admin_initial_password or "Admin@123456"
            logger.warning(
                "Creating initial admin with dev default password "
                "(set ADMIN_INITIAL_PASSWORD in .env for production)"
            )

        self.create_user(
            username="admin",
            password=pwd,
            role="admin",
            email=None,
            actor_id="system",
            must_change_password=True,
        )

    # ── 查询 ────────────────────────────────────────────────

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        """按用户名查询用户（小写归一化；不存在 → None）。"""
        uname = (username or "").strip().lower()
        if not uname:
            return None
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (uname,)
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """按用户 id 查询（不存在 → None）。"""
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def list_users(
        self,
        role: str | None = None,
        disabled: int | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """分页列出用户（**不含 password_hash**）。

        Args:
            role: 按角色过滤（5 角色之一）。
            disabled: 按状态过滤（0=启用 1=禁用；None=不过滤）。
            q: 关键字（username / email 模糊匹配）。
            page: 页码（≥1）。
            page_size: 每页条数（1-200）。

        Returns:
            ``{"users": [UserSummary...], "total": int}``。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if role:
            clauses.append("role = ?")
            params.append((role or "").strip().lower())
        if disabled is not None:
            clauses.append("disabled = ?")
            params.append(1 if disabled else 0)
        if q and (q or "").strip():
            like = f"%{(q or '').strip()}%"
            clauses.append("(username LIKE ? OR email LIKE ?)")
            params.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        page = max(1, int(page or 1))
        page_size = min(200, max(1, int(page_size or 50)))

        conn = get_connection()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM users {where}", params
            ).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM users {where} "
                "ORDER BY created_at DESC, username ASC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        finally:
            conn.close()
        users = [self._to_summary(dict(r)) for r in rows]
        return {"users": users, "total": int(total)}

    # ── 创建 / 更新 ──────────────────────────────────────────

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        email: str | None = None,
        actor_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        must_change_password: bool = True,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """创建用户（仅管理员调用；默认 must_change_password=1）。

        Args:
            username: 登录名（小写唯一）。
            password: 初始密码（须满足密码策略）。
            role: 5 角色之一。
            email: 可选邮箱（非空时唯一）。
            actor_id: 操作者（审计 user_created 用）。
            ip_address / user_agent: 审计上下文。
            must_change_password: 创建后是否强制首次改密（默认 True；
                dev-login 传 False 以便 dev 无感联调）。
            user_id: 指定用户 id（默认 None → UUID4；dev-login 传
                ``f"dev-{role}"`` 以保证 id 稳定、可幂等复用）。

        Returns:
            UserSummary dict（**不含 password_hash**）。

        Raises:
            HTTPException 422: 用户名非法 / 密码策略不满足 / 角色非法。
            HTTPException 409: 用户名或邮箱已存在。
        """
        uname = (username or "").strip().lower()
        if not _USERNAME_RE.match(uname):
            raise HTTPException(
                status_code=422,
                detail="用户名仅支持小写字母、数字、_ - .（1-64 位）",
            )
        self._validate_password(password)
        role = (role or "").strip().lower()
        if role not in ROLE_VALUES:
            raise HTTPException(status_code=422, detail="无效角色")

        email_norm = (email or "").strip().lower() or None
        now = _now_iso()
        password_hash = self._hash_password(password)
        user_id = user_id or str(uuid.uuid4())

        conn = get_connection()
        try:
            try:
                if email_norm:
                    cur = conn.execute(
                        "INSERT INTO users "
                        "(id, username, email, password_hash, role, disabled, "
                        " must_change_password, password_changed_at, password_history, "
                        " failed_attempts, locked_until, last_login_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?, ?, '[]', 0, NULL, NULL, ?, ?)",
                        (
                            user_id,
                            uname,
                            email_norm,
                            password_hash,
                            role,
                            1 if must_change_password else 0,
                            None,
                            now,
                            now,
                        ),
                    )
                else:
                    cur = conn.execute(
                        "INSERT INTO users "
                        "(id, username, email, password_hash, role, disabled, "
                        " must_change_password, password_changed_at, password_history, "
                        " failed_attempts, locked_until, last_login_at, created_at, updated_at) "
                        "VALUES (?, ?, NULL, ?, ?, 0, ?, NULL, '[]', 0, NULL, NULL, ?, ?)",
                        (
                            user_id,
                            uname,
                            password_hash,
                            role,
                            1 if must_change_password else 0,
                            now,
                            now,
                        ),
                    )
                conn.commit()
            except sqlite3.IntegrityError as e:
                msg = str(e).lower()
                if "username" in msg:
                    raise HTTPException(
                        status_code=409, detail="用户名已存在"
                    ) from None
                if "email" in msg:
                    raise HTTPException(
                        status_code=409, detail="邮箱已被使用"
                    ) from None
                raise
        finally:
            conn.close()

        AuthAuditService.record(
            "user_created",
            user_id=user_id,
            username=uname,
            ip_address=ip_address,
            user_agent=user_agent,
            detail=f"actor={actor_id or 'unknown'} role={role}",
        )
        created = self.get_user(user_id)
        return self._to_summary(created or {})

    def update_user(
        self,
        user_id: str,
        role: str | None = None,
        disabled: int | None = None,
        password: str | None = None,
        actor_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """更新用户（改角色 / 禁用 / 改密，至少一项生效）。

        Args:
            user_id: 目标用户 id。
            role: 新角色（5 角色之一）。
            disabled: 0=启用 1=禁用。
            password: 新密码（满足策略；改密后撤销该用户全部 refresh）。
            actor_id / ip_address / user_agent: 审计上下文。

        Returns:
            UserSummary dict（**不含 password_hash**）。

        Raises:
            HTTPException 404: 用户不存在。
            HTTPException 422: 角色非法 / 密码策略不满足。
            HTTPException 409: 最后一个 admin 禁止禁用/降级（防呆）。
        """
        user = self.get_user(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")

        self._guard_last_admin(user_id, role, disabled)

        now = _now_iso()
        # 审计事件暂存，连接关闭后再落库（防 SQLITE_BUSY：主写连接持锁期间
        # 二次连接写 auth_audit_log 会锁冲突 → 审计丢事件）
        pending_audits: list[tuple[str, str]] = []
        conn = get_connection()
        try:
            # 1) 角色变更（现有 token 保留到过期——主理人拍板 #9，不踢下线）
            if role is not None:
                role = (role or "").strip().lower()
                if role not in ROLE_VALUES:
                    raise HTTPException(status_code=422, detail="无效角色")
                if role != user["role"]:
                    conn.execute(
                        "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                        (role, now, user_id),
                    )
                    pending_audits.append(
                        (
                            "role_changed",
                            f"{user['role']}->{role} actor={actor_id or 'unknown'}",
                        )
                    )

            # 2) 禁用 / 启用（禁用后 login/refresh/me 拒绝；已签发 access
            #    最长存活 TTL——即时踢下线属 P2，主理人拍板 #9）
            if disabled is not None:
                disabled_val = 1 if disabled else 0
                if disabled_val != int(user.get("disabled") or 0):
                    conn.execute(
                        "UPDATE users SET disabled = ?, updated_at = ? WHERE id = ?",
                        (disabled_val, now, user_id),
                    )
                    pending_audits.append(
                        (
                            "user_disabled" if disabled_val else "user_enabled",
                            f"disabled={disabled_val} actor={actor_id or 'unknown'}",
                        )
                    )

            # 3) 改密（撤销该用户全部 refresh——安全兜底 AC8-5）
            if password is not None:
                self._validate_password(password)
                new_hash = self._hash_password(password)
                history = _append_password_history(
                    user.get("password_history"), new_hash
                )
                conn.execute(
                    "UPDATE users SET password_hash = ?, password_changed_at = ?, "
                    "must_change_password = 0, password_history = ?, updated_at = ? "
                    "WHERE id = ?",
                    (new_hash, now, history, now, user_id),
                )
                conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (now, user_id),
                )
                pending_audits.append(
                    (
                        "password_changed",
                        f"admin_reset actor={actor_id or 'unknown'}",
                    )
                )

            conn.commit()
        finally:
            conn.close()

        # 审计在写连接关闭后统一落库（防锁冲突丢事件）
        for event_type, detail in pending_audits:
            AuthAuditService.record(
                event_type,
                user_id=user_id,
                username=user["username"],
                ip_address=ip_address,
                user_agent=user_agent,
                detail=detail,
            )

        updated = self.get_user(user_id)
        return self._to_summary(updated or {})

    # ── 密码策略 ────────────────────────────────────────────

    def _validate_password(self, password: str) -> None:
        """校验密码策略：≥8 位 + 至少一个数字 + 至少一个字母（拍板 #2）。

        Raises:
            HTTPException 422: 不满足策略（统一文案，不泄漏细节）。
        """
        if not password or len(password) < settings.password_min_length:
            raise HTTPException(
                status_code=422,
                detail=f"密码长度至少 {settings.password_min_length} 位",
            )
        if not _DIGIT_RE.search(password) or not _LETTER_RE.search(password):
            raise HTTPException(
                status_code=422,
                detail="密码需同时包含数字和字母",
            )

    def _hash_password(self, password: str) -> str:
        """bcrypt hash（成本因子 12 + 72 字节截断，共享知识 #4）。"""
        raw = password.encode("utf-8")[:72]
        return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("utf-8")

    def _verify_password(self, password: str, password_hash: str) -> bool:
        """bcrypt 校验（hash 与 verify 两侧一致做 72 字节截断）。"""
        try:
            raw = password.encode("utf-8")[:72]
            stored = password_hash.encode("utf-8")
            return bcrypt.checkpw(raw, stored)
        except (ValueError, TypeError):
            # 非法 hash（旧数据 / 篡改）→ 一律视为不匹配（fail-closed）
            return False

    def is_password_expired(self, user: dict[str, Any]) -> bool:
        """判断用户密码是否已过期（password_changed_at + PASSWORD_EXPIRY_DAYS）。

        Args:
            user: users 表行 dict。

        Returns:
            True=已过期；从未设置过密码 / 解析失败 → False（不过期，保守）。
        """
        changed = user.get("password_changed_at")
        if not changed:
            return False
        try:
            changed_dt = datetime.fromisoformat(str(changed))
            if changed_dt.tzinfo is None:
                changed_dt = changed_dt.replace(tzinfo=timezone.utc)
            expires_dt = changed_dt + timedelta(days=settings.password_expiry_days)
            return datetime.now(timezone.utc) >= expires_dt
        except (ValueError, TypeError):
            return False

    # ── 防呆 ────────────────────────────────────────────────

    def _guard_last_admin(
        self,
        user_id: str,
        new_role: str | None = None,
        disabled: int | None = None,
    ) -> None:
        """最后一个 admin 防呆：禁止禁用/降级系统最后一个 admin（409）。

        规则（共享知识 #7 + PRD AC6-5）：
        - 目标用户当前不是「启用态 admin」→ 直接放行；
        - 操作不会让其失去 admin 资格（仍是 admin 且不禁用）→ 放行；
        - 系统 admin（disabled=0）计数 ≤ 1 且目标会失去 admin 资格 → 409。
        """
        user = self.get_user(user_id)
        if user is None:
            return
        currently_admin = (
            user["role"] == "admin" and int(user.get("disabled") or 0) == 0
        )
        if not currently_admin:
            return

        loses_admin = False
        if new_role is not None and (new_role or "").strip().lower() != "admin":
            loses_admin = True
        if disabled is not None and int(disabled) != 0:
            loses_admin = True
        if not loses_admin:
            return

        conn = get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND disabled = 0"
            ).fetchone()["c"]
        finally:
            conn.close()
        if int(count) <= 1:
            raise HTTPException(
                status_code=409,
                detail="不能禁用或降级最后一个管理员",
            )

    # ── 内部工具 ────────────────────────────────────────────

    @staticmethod
    def _to_summary(user: dict[str, Any]) -> dict[str, Any]:
        """UserSummary dict（**不含 password_hash**）。"""
        return {
            "id": user["id"],
            "username": user["username"],
            "email": user.get("email"),
            "role": user["role"],
            "disabled": int(user.get("disabled") or 0),
            "must_change_password": int(user.get("must_change_password") or 0),
            "last_login_at": user.get("last_login_at"),
            "created_at": user.get("created_at"),
        }


def _append_password_history(existing_json: str | None, new_hash: str) -> str:
    """向 password_history JSON 追加新 hash（保留最近 N 条，P2 去重预留）。"""
    try:
        history = json.loads(existing_json) if existing_json else []
    except (TypeError, ValueError):
        history = []
    if not isinstance(history, list):
        history = []
    history.append(new_hash)
    history = history[-_PASSWORD_HISTORY_LIMIT:]
    return json.dumps(history)
