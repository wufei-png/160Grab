"""Local authorization, bound to identity and policy, separate from configuration.

Current site adapters cannot establish a stable account identity; production
therefore uses a fresh run nonce. A verified future adapter may supply account_ref.
"""

import secrets

from grab.transactions.store import POLICY_VERSION, AttemptStore


class ConsentManager:
    def __init__(self, store: AttemptStore, *, prompt=None, interactive=False):
        self.store, self.prompt, self.interactive = store, prompt, interactive
        self.run_nonce = secrets.token_hex(16)
        self.denied = False

    def new_identity_session(self):
        # Re-login/target preparation cannot silently carry run-only authorization.
        self.run_nonce = secrets.token_hex(16)
        self.denied = False

    def ensure(self, target, member_id, *, account_ref=None):
        if self.denied or self.store.pending():
            return False
        binding = self.store.reference(
            account_ref or self.run_nonce,
            member_id,
            target.unit_id,
            target.dept_id,
            target.doctor_id,
            POLICY_VERSION,
        )
        state = self.store.read()
        manual_ref = self.store.reference(
            "manual-choice",
            member_id,
            target.unit_id,
            target.dept_id,
            target.doctor_id,
            POLICY_VERSION,
        )
        if any(
            c["binding_ref"] == manual_ref and c["policy_version"] == POLICY_VERSION
            for c in state["consents"]
        ):
            return False
        if any(
            c["binding_ref"] == binding and c["policy_version"] == POLICY_VERSION
            for c in state["consents"]
        ):
            return True
        if not self.interactive or not self.prompt:
            return False
        # Direct user interaction only: never use logger/reporter/notifications.
        message = (
            f"医生目标：{target.unit_id}/{target.dept_id}/{target.doctor_id}；就诊人：{member_id}。\n"
            "自动模式会点击最终预约提交；未知结果必须先在原站核对预约记录。\n"
            "禁止 Python/userscript、多个浏览器或多机器混跑同一目标。\n"
            "此确认不替代站点协议、验证码或支付确认。\n"
            + (
                "授权保存于本机。\n"
                if account_ref
                else "账号不能可靠区分：授权仅本次运行有效，重启/重新登录需确认。\n"
            )
            + "输入 AUTHORIZE 明确授权，否则使用人工模式："
        )
        try:
            accepted = self.prompt(message) == "AUTHORIZE"
        except EOFError:
            accepted = False
        if not accepted:
            self.denied = True
            state = self.store.read()
            state["consents"].append(
                {"binding_ref": manual_ref, "policy_version": POLICY_VERSION}
            )
            self.store.write(state)
            return False
        # Re-read after human interaction; preserve any concurrently written blocker.
        state = self.store.read()
        if self.store.pending():
            return False
        state["consents"].append(
            {"binding_ref": binding, "policy_version": POLICY_VERSION}
        )
        self.store.write(state)
        return True
