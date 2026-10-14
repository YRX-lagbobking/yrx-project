"""
安全模块自测：JWT 签发/解析 + RBAC 数据权限（FR-11 / AC-19、AC-20）

推荐运行方式（在项目根目录下执行）：
    python -m app.test.test_security
    python app/test/test_security.py

演示内容：
    1. create_access_token(user_id, role) → decode_access_token(token) 正例
    2. 篡改 Token / 垃圾字符串 / 空串 → 必须返回 None（反例，不能抛异常）
    3. can_access_user(...) 三种角色的正反例：
         admin  → 任何人都是 True
         elder  → 只能访问自己（老人 A 的 Token 查老人 B → False，对应 AC-19）
         family → 只能访问已绑定的老人（未绑定 → False，对应 AC-20）
    4. hash_password / verify_password 一致性（与 sql/init.sql 里的哈希规则一致）

依赖：app/core/security.py（由 core-dao 同学实现）。缺失/异常时打印降级说明并退出。
"""
import os
import sys

# ---------- 项目根目录加入 sys.path ----------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 采集到的断言失败信息（全部跑完再汇总，便于一次看到所有问题）
FAILURES: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    """统一的断言输出：PASS / FAIL"""
    if condition:
        print(f"   [PASS] {name} {extra}")
    else:
        print(f"   [FAIL] {name} {extra}")
        FAILURES.append(name)


def main() -> None:
    print("=" * 72)
    print("安全模块自测：JWT + RBAC 数据权限（FR-11 / AC-19、AC-20）")
    print("=" * 72)

    try:
        from app.core.security import (
            TokenData,
            can_access_user,
            create_access_token,
            decode_access_token,
            hash_password,
            verify_password,
        )
    except Exception as e:
        print(f"[跳过] app/core/security.py 不可用：{e}")
        print("       请先完成 core-dao 模块（契约第 4 节），再运行本脚本。")
        return

    # ---------------- 1. JWT 正例：签发 → 解析 ----------------
    print("\n【1】JWT 正例：create_access_token → decode_access_token")
    token = create_access_token("u001", "elder")
    print(f"     Token 前缀：{str(token)[:32]}…（长度 {len(str(token))}）")
    data = decode_access_token(token)
    check("Token 能正常解析", data is not None)
    if data is not None:
        print(f"     解析结果：user_id={data.user_id} role={data.role}")
        check("user_id 一致", data.user_id == "u001")
        check("role 一致", data.role == "elder")

    # ---------------- 2. JWT 反例：各种非法 Token 必须返回 None ----------------
    print("\n【2】JWT 反例：非法输入不能抛异常，必须返回 None")
    bad_tokens = {
        "篡改后的 Token": str(token)[:-3] + "abc",
        "随手写的垃圾串": "not-a-jwt-token",
        "空字符串": "",
        "只有两段的假 Token": "aaa.bbb",
    }
    for label, bad in bad_tokens.items():
        try:
            got = decode_access_token(bad)
            check(f"{label} → None", got is None, f"（实际 {got!r}）")
        except Exception as e:  # decode 内部必须自己吞掉异常
            check(f"{label} → 不抛异常", False, f"（却抛了 {type(e).__name__}: {e}）")

    # ---------------- 3. RBAC 数据权限 ----------------
    print("\n【3】can_access_user：三种角色的正反例")
    admin = TokenData(user_id="admin", role="admin")
    elder_a = TokenData(user_id="u001", role="elder")
    family_f1 = TokenData(user_id="f001", role="family")
    bound = ["u001"]          # f001 绑定的老人

    check("admin 可访问任意老人", can_access_user(admin, "u002", []) is True)
    check("elder 可访问本人", can_access_user(elder_a, "u001", []) is True)
    check("elder 访问他人被拒（AC-19）", can_access_user(elder_a, "u002", []) is False)
    check("family 可访问已绑定老人", can_access_user(family_f1, "u001", bound) is True)
    check("family 访问未绑定老人被拒（AC-20）", can_access_user(family_f1, "u002", bound) is False)
    check("family 未传绑定列表时只允许自己",
          can_access_user(family_f1, "u001", []) is False)

    # ---------------- 4. 密码哈希：与 sql/init.sql 的规则一致 ----------------
    print("\n【4】密码哈希：salt = 'health_butler'，sha256(salt + 明文)")
    expected_123456 = "2348b2c5fa68c689a3302e1c3edc241a937249507a0bd86720b78bee8512e9bd"
    hashed = hash_password("123456")
    print(f"     hash_password('123456') = {hashed}")
    check("哈希值与 sql/init.sql 中的初始化密码一致", hashed == expected_123456)
    check("verify_password 正确密码通过", verify_password("123456", hashed) is True)
    check("verify_password 错误密码拒绝", verify_password("654321", hashed) is False)

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 72)
    if FAILURES:
        print(f"自测结束：存在 {len(FAILURES)} 项失败 → {FAILURES}")
    else:
        print("自测结束：全部断言通过（JWT 正反例 + RBAC 三角色 + 密码哈希）")
    print("=" * 72)


if __name__ == "__main__":
    main()
