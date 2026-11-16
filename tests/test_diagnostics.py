import json

import pytest

import grab.diagnostics as diagnostics
import main as cli
from grab.models.schemas import GrabConfig
from grab.transactions.store import AttemptStore
from grab.utils.profile_manager import create_profile


@pytest.fixture
def installed_browser(monkeypatch):
    async def installed(_channel):
        return True

    monkeypatch.setattr(diagnostics, "browser_available", installed)


async def test_checks_do_not_create_profiles_or_journal_or_reveal_values(
    tmp_path, installed_browser
):
    config = GrabConfig(
        member_id="SYN_PRIVATE_MEMBER",
        doctor_ids=["SYN_PRIVATE_DOCTOR"],
        browser={"profiles_root_dir": str(tmp_path / "profiles")},
        booking={"clinic_card": "SYN_PRIVATE_CARD"},
    )
    before = list(tmp_path.rglob("*"))
    report = await diagnostics.check_environment(
        config, journal_root=tmp_path / "journal"
    )
    assert list(tmp_path.rglob("*")) == before
    assert report["live_checked"] is False
    assert report["status"] == "warning"
    assert "SYN_PRIVATE" not in json.dumps(report)
    assert {c["code"] for c in report["checks"]} >= {
        "profile_setup_required",
        "journal_not_initialized",
        "readonly_canary_required",
    }


async def test_pending_is_reported_without_mutating_or_resolving_it(
    tmp_path, installed_browser
):
    store = AttemptStore(tmp_path / "journal")
    store.begin(store.reference("SYN_PRIVATE_BOOKING"))
    path = tmp_path / "journal" / "journal.json"
    before = path.read_bytes()
    report = await diagnostics.check_environment(
        GrabConfig(browser={"profiles_root_dir": str(tmp_path / "profiles")}),
        journal_root=store.root,
    )
    check = next(c for c in report["checks"] if c["id"] == "journal")
    assert check == {
        "id": "journal",
        "status": "blocked",
        "code": "pending_verification",
        "pending_count": 1,
    }
    assert report["status"] == "blocked"
    assert path.read_bytes() == before
    assert len(store.pending()) == 1


@pytest.mark.parametrize(
    "raw", ['{"SYN_PRIVATE_POISON":true}', "not-json-SYN_PRIVATE_POISON"]
)
def test_corrupt_journal_is_not_reset_or_printed(tmp_path, raw):
    (tmp_path / "journal.json").write_text(raw, encoding="utf-8")
    report = diagnostics.journal_check(tmp_path)
    assert report["code"] == "journal_unavailable"
    assert (tmp_path / "journal.json").read_text(encoding="utf-8") == raw
    assert "SYN_PRIVATE" not in json.dumps(report)


async def test_profile_channel_mismatch_and_missing_browser_are_actionable(
    tmp_path, installed_browser, monkeypatch
):
    create_profile(tmp_path / "profiles", "synthetic", channel="chromium")
    config = GrabConfig(
        browser={
            "profiles_root_dir": str(tmp_path / "profiles"),
            "profile_name": "synthetic",
            "channel": "chrome",
        }
    )

    async def unavailable(_channel):
        return False

    monkeypatch.setattr(diagnostics, "browser_available", unavailable)
    report = await diagnostics.check_environment(
        config, journal_root=tmp_path / "journal"
    )
    codes = {c["code"] for c in report["checks"] if c["status"] == "blocked"}
    assert codes == {"profile_channel_mismatch", "browser_missing"}


async def test_invalid_config_report_does_not_render_rejected_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("booking:\n  submit_mode: SYN_PRIVATE_POISON\n", encoding="utf-8")
    report = await diagnostics.diagnostic_report(path)
    assert report["status"] == "blocked"
    assert report["checks"][0]["code"] == "configuration_unavailable"
    assert "SYN_PRIVATE" not in json.dumps(report)


async def test_cli_check_runs_outside_leader_and_never_starts_product(
    monkeypatch, capsys
):
    async def forbidden(*_args):
        raise AssertionError("A diagnostic must not enter the product run")

    async def report(_path):
        return {"version": 1, "status": "blocked", "live_checked": False, "checks": []}

    monkeypatch.setattr(cli, "_exclusive_main", forbidden)
    monkeypatch.setattr(diagnostics, "diagnostic_report", report)
    with pytest.raises(SystemExit) as exc:
        await cli.main(["synthetic.yaml", "--check"])
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"


@pytest.mark.parametrize(
    "operation",
    [
        "--create-profile",
        "--pending-attempts",
        "--revoke-consent",
        "--cleanup-data",
        "--smoke-browser",
    ],
)
def test_check_cannot_be_combined_with_mutating_or_browser_operations(operation):
    with pytest.raises(SystemExit) as exc:
        cli.parse_args(["--check", operation])
    assert exc.value.code == 2
