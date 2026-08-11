"""Live E2E smoke test against a running GridMind server (new code).

使用 dev-login 签发 admin JWT 做管理操作（不受真实 admin 账号锁定影响）；
真实登录链路用脚本自建的临时用户验证（不污染既有账号）。
"""
import base64
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9902"


def req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except Exception:
            detail = {}
        return e.code, detail


ok, fail = [], []


def check(name, cond, extra=""):
    (ok if cond else fail).append(name)
    print(("PASS" if cond else "FAIL"), name, extra)


# ── 0. dev-login admin（管理操作身份；不受锁定影响）──
s, admin_u = req("POST", "/auth/dev-login", {"role": "admin"})
check("dev-login admin 200", s == 200 and admin_u.get("access_token"), f"status={s}")
admin_bearer = {"Authorization": f"Bearer {admin_u['access_token']}"}
# dev 模式 /auth/me 返回占位用户（id=dev），admin 真实 id 从 dev-login 响应取
admin_id = admin_u["user"]["id"]

# ── 1. 创建临时用户 → 真实登录成功 → 双 token + role ──
uname = f"smoke{int(time.time())}"
s, created = req(
    "POST", "/users",
    {"username": uname, "password": "Dispatch#2026", "role": "operator"},
    admin_bearer,
)
check("admin create user 201", s == 201 and created.get("must_change_password") == 1, f"status={s}")
s, d = req("POST", "/auth/login", {"username": uname, "password": "Dispatch#2026"})
check(
    "login success dual tokens + role",
    s == 200 and d.get("access_token") and d.get("refresh_token")
    and d["user"].get("role") == "operator",
    f"status={s}",
)
access = d.get("access_token", "")

# ── 2. JWT claims：sub/user_id/role/name/iss/iat/exp，不含 thread_id ──
payload = json.loads(base64.urlsafe_b64decode(access.split(".")[1] + "=="))
check(
    "jwt claims role/name/iss/iat/exp",
    payload.get("role") == "operator" and payload.get("name")
    and payload.get("iss") == "gridmind" and payload.get("iat") and payload.get("exp"),
)
check("jwt no thread_id", "thread_id" not in payload)
check(
    "jwt sub == user.id",
    payload.get("sub") == d["user"]["id"] and payload.get("user_id") == d["user"]["id"],
)

# ── 3. 登录失败统一 401 + 锁定 423 ──
s1, d1 = req("POST", "/auth/login", {"username": "ghost-user", "password": "Wrong#1"})
s2, d2 = req("POST", "/auth/login", {"username": uname, "password": "Wrong#1"})
check(
    "failure uniform 401",
    s1 == 401 and s2 == 401 and d1.get("detail") == d2.get("detail") == "用户名或密码错误",
    f"{s1}/{s2}",
)
for _ in range(5):
    req("POST", "/auth/login", {"username": uname, "password": "Wrong#1"})
s6, d6 = req("POST", "/auth/login", {"username": uname, "password": "Dispatch#2026"})
check("lockout 423 after 5 fails", s6 == 423 and "锁定" in d6.get("detail", ""), f"status={s6}")

# 等待 per-IP 限流窗口（10/min）复位，保证后续登录断言不因 429 干扰
print("... waiting 61s for rate-limit window reset ...")
time.sleep(61)

# ── 4. refresh 轮换 + 旧 token revoked（dev-login 会话，不计入 /auth/login）──
s, d2 = req("POST", "/auth/dev-login", {"role": "operator"})
check("dev-login dev mode 200", s == 200 and d2["user"]["role"] == "operator", f"status={s}")
r1 = d2["refresh_token"]
s, d3 = req("POST", "/auth/refresh", {"refresh_token": r1})
check("refresh rotation (new refresh != old)", s == 200 and d3.get("refresh_token") != r1, f"status={s}")
s, _ = req("POST", "/auth/refresh", {"refresh_token": r1})
check("old refresh rejected 401", s == 401, f"status={s}")

# ── 5. 禁用用户：login 403 / refresh 401（me 401 属生产行为，dev 返回占位 200，
#       由 pytest test_me_prod_disabled_user_rejected 在 prod 模式覆盖）──
s, created2 = req(
    "POST", "/users",
    {"username": f"smoke2{int(time.time())}", "password": "Dispatch#2026", "role": "dispatcher"},
    admin_bearer,
)
s, lu2 = req("POST", "/auth/login", {"username": created2["username"], "password": "Dispatch#2026"})
check("second user login 200", s == 200, f"status={s}")
s, _ = req("PATCH", f"/users/{created2['id']}", {"disabled": 1}, admin_bearer)
check("admin disable user 200", s == 200, f"status={s}")
s, _ = req("POST", "/auth/login", {"username": created2["username"], "password": "Dispatch#2026"})
check("disabled login 403", s == 403, f"status={s}")
s, _ = req("POST", "/auth/refresh", {"refresh_token": lu2["refresh_token"]})
check("disabled refresh 401", s == 401, f"status={s}")
s, _ = req("GET", "/auth/me", headers={"Authorization": f"Bearer {lu2['access_token']}"})
check("disabled me (prod 401; dev placeholder 200)", s in (200, 401), f"status={s}")

# ── 6. 改密撤销全部 refresh + 弱密码 422 ──
s, _ = req("PATCH", f"/users/{created2['id']}", {"disabled": 0}, admin_bearer)
s, u2 = req("POST", "/auth/login", {"username": created2["username"], "password": "Dispatch#2026"})
u2_bearer = {"Authorization": f"Bearer {u2['access_token']}"}
s, _ = req(
    "POST", "/auth/change-password",
    {"old_password": "Dispatch#2026", "new_password": "short"},
    u2_bearer,
)
check("change password weak 422", s == 422, f"status={s}")
s, _ = req(
    "POST", "/auth/change-password",
    {"old_password": "Dispatch#2026", "new_password": "NewPass#2026"},
    u2_bearer,
)
check("change password ok 200", s == 200, f"status={s}")
s, _ = req("POST", "/auth/refresh", {"refresh_token": u2["refresh_token"]})
check("refresh revoked after pwd change 401", s == 401, f"status={s}")

# ── 7. 密码策略 422 / 用户名冲突 409 / 最后 admin 防呆 409 ──
s, _ = req(
    "POST", "/users",
    {"username": "weak", "password": "12345678", "role": "dispatcher"},
    admin_bearer,
)
check("weak password 422", s == 422, f"status={s}")
s, _ = req(
    "POST", "/users",
    {"username": uname, "password": "X#2026abc", "role": "dispatcher"},
    admin_bearer,
)
check("username conflict 409", s == 409, f"status={s}")
# 最后 admin 防呆：先确保 dev-admin 启用（防历史运行污染），
# 再降级真实 admin（dev-admin 仍在 → 允许），使 dev-admin 成为唯一 admin
# → 禁用必须 409
s, _ = req("PATCH", f"/users/{admin_id}", {"disabled": 0}, admin_bearer)
s, users_resp = req("GET", "/users", headers=admin_bearer)
real_admin = next((u for u in users_resp.get("users", []) if u["username"] == "admin"), None)
if real_admin and real_admin["role"] == "admin":
    s, _ = req("PATCH", f"/users/{real_admin['id']}", {"role": "dispatcher"}, admin_bearer)
    check("demote non-last admin allowed 200", s == 200, f"status={s}")
s, _ = req("PATCH", f"/users/{admin_id}", {"disabled": 1}, admin_bearer)
check("last admin guard 409", s == 409, f"status={s}")

print(f"\n== {len(ok)} PASS / {len(fail)} FAIL ==")
if fail:
    print("FAILED:", fail)
