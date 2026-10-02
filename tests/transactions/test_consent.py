import pytest

from grab.models.schemas import DoctorPageTarget, GrabConfig
from grab.transactions.consent import ConsentManager
from grab.transactions.store import AttemptStore


def target(doctor="SYN_DOCTOR"):
    return DoctorPageTarget(
        unit_id="unit", dept_id="dept", doctor_id=doctor, source_url=""
    )


def test_run_only_confirmation_and_binding_changes(tmp_path):
    prompts = []

    def accept(message):
        prompts.append(message)
        return "AUTHORIZE"

    store = AttemptStore(tmp_path)
    consent = ConsentManager(store, prompt=accept, interactive=True)
    assert consent.ensure(target(), "SYN_MEMBER")
    assert consent.ensure(target(), "SYN_MEMBER")
    assert len(prompts) == 1
    assert "自动" in prompts[0] and "未知" in prompts[0] and "禁止" in prompts[0]
    assert consent.ensure(target("other"), "SYN_MEMBER")
    assert consent.ensure(target(), "other")
    assert len(prompts) == 3
    assert not ConsentManager(store).ensure(target(), "SYN_MEMBER")
    consent.new_identity_session()
    assert consent.ensure(target(), "SYN_MEMBER")
    assert len(prompts) == 4
    assert "SYN_" not in (tmp_path / "journal.json").read_text(encoding="utf-8")


def test_stable_account_seam_persists_and_revokes_without_resolving(tmp_path):
    store = AttemptStore(tmp_path)
    consent = ConsentManager(store, prompt=lambda _: "AUTHORIZE", interactive=True)
    assert consent.ensure(target(), "member", account_ref="SYN_ACCOUNT")
    restored = ConsentManager(store)
    assert restored.ensure(target(), "member", account_ref="SYN_ACCOUNT")
    assert not restored.ensure(target(), "member", account_ref="other-account")
    attempt = store.begin(store.reference("booking"))
    store.revoke()
    assert store.pending()[0]["attempt_id"] == attempt
    store.resolve(attempt, booked=False)
    assert not restored.ensure(target(), "member", account_ref="SYN_ACCOUNT")


@pytest.mark.parametrize("interactive,answer", [(False, "AUTHORIZE"), (True, "no")])
def test_unauthorized_and_rejected_do_not_save_grant(tmp_path, interactive, answer):
    store = AttemptStore(tmp_path)
    manager = ConsentManager(store, prompt=lambda _: answer, interactive=interactive)
    assert not manager.ensure(target(), "member")
    if interactive:
        assert len(store.read()["consents"]) == 1
        restored = ConsentManager(
            store,
            prompt=lambda _: pytest.fail("Rejected binding stays manual"),
            interactive=True,
        )
        assert not restored.ensure(target(), "member")
    else:
        assert store.read()["consents"] == []


def test_legacy_config_consent_does_not_authorize(tmp_path):
    config = GrabConfig.model_validate({"booking": {"consent": True}})
    assert config.booking.submit_mode == "auto"
    assert not ConsentManager(AttemptStore(tmp_path)).ensure(target(), "member")


def test_prior_policy_grant_requires_new_confirmation(tmp_path):
    store = AttemptStore(tmp_path)
    consent = ConsentManager(store, prompt=lambda _: "AUTHORIZE", interactive=True)
    old = store.read()
    old["consents"].append(
        {
            "binding_ref": store.reference(
                "account", "member", "unit", "dept", "SYN_DOCTOR", "submit-v0"
            ),
            "policy_version": "submit-v0",
        }
    )
    store.write(old)
    assert not ConsentManager(store).ensure(target(), "member", account_ref="account")
    assert consent.ensure(target(), "member", account_ref="account")
    assert len(store.read()["consents"]) == 2
