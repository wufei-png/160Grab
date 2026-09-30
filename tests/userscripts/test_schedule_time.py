import json

import pytest

from tests.userscripts.test_doctor_page_poller import _run_hook


@pytest.mark.parametrize(
    "value,zone,expected",
    [
        ("2030-01-01T08:00", "Asia/Shanghai", "2030-01-01T00:00:00.000Z"),
        ("2030-01-01T08:00:00.250", "Asia/Shanghai", "2030-01-01T00:00:00.250Z"),
        ("2026-11-01T01:30:00-04:00", "America/New_York", "2026-11-01T05:30:00.000Z"),
        ("2026-11-01T01:30:00-05:00", "America/New_York", "2026-11-01T06:30:00.000Z"),
        ("2030-01-01T00:00:00Z", "Asia/Shanghai", "2030-01-01T00:00:00.000Z"),
    ],
)
def test_start_time_has_unambiguous_offset_iso(value, zone, expected):
    result = _run_hook(
        f"hooks.normalizeSettings({json.dumps({'runtime': {'startAt': value}, 'schedule': {'timezone': zone}})})"
    )
    assert result["runtime"]["startAt"] == expected
    assert result["schedule"]["lateStartGraceSeconds"] == 30


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-01T01:30",
        "2026-03-08T02:30",
        "bad",
        "2030-02-30T08:00",
        "2030-01-01T24:00",
        "2030-01-01",
    ],
)
def test_invalid_and_dst_local_times_rejected(value):
    result = _run_hook(
        f"(() => {{try {{hooks.normalizeStartAtValue({json.dumps(value)}, 'America/New_York');return false;}}catch (_) {{return true;}}}})()"
    )
    assert result is True


def test_query_date_and_panel_time_use_configured_zone():
    result = _run_hook(
        "[hooks.scheduleDate('Asia/Shanghai', Date.parse('2030-01-01T16:00Z')),hooks.scheduleDate('America/New_York',Date.parse('2030-01-01T16:00Z')), hooks.wallTimeValue(Date.parse('2030-01-01T00:00Z'), 'Asia/Shanghai')]"
    )
    assert result == ["2030-01-02", "2030-01-01", "2030-01-01T08:00:00"]


def test_migration_persists_iso_and_preserves_v4_explicit_fields():
    result = _run_hook(
        "hooks.readSettings()",
        """
      const old = {settingsVersion:4, runtime:{startAt:'2030-01-01T08:00'}, address:{province:'广东'},booking:{diseaseDescription:'门诊就诊，具体病情现场面诊沟通'}};
      sandbox.GM_getValue = () => old;
      sandbox.GM_setValue = (key,value) => {sandbox.saved=value;};
    """,
    )
    assert result["settingsVersion"] == 5
    assert result["runtime"]["startAt"].endswith("Z")
    assert result["address"]["province"] == "广东"
    assert result["booking"]["diseaseDescription"] == "门诊就诊，具体病情现场面诊沟通"


def test_invalid_stored_time_blocks_automatic_start():
    result = _run_hook(
        "hooks.readSettings()",
        "sandbox.GM_getValue = () => ({runtime:{startAt:'bad',autoStart:true}});",
    )
    assert result["runtime"]["startBlocked"] is True
    assert result["runtime"]["autoStart"] is False
    assert result["booking"]["submitMode"] == "manual_confirm"


def test_panel_roundtrip_preserves_explicit_dst_instant_and_fractions():
    result = _run_hook("""(() => {
      const previous = hooks.normalizeSettings({schedule:{timezone:'America/New_York'},runtime:{startAt:'2026-11-01T01:30:00.250-04:00'}});
      const container = {querySelector(selector) {
        const path = selector.match(/data-setting="([^"]+)"/)?.[1];
        if (path === 'runtime.startAt') return {value:hooks.startAtInputValue(previous.runtime.startAt,previous.schedule.timezone)};
        if (path === 'schedule.timezone') return {value:previous.schedule.timezone};
        if (path === 'schedule.lateStartGraceSeconds') return {value:'30'};
        if (path === 'booking.submitMode') return {value:'manual_confirm'};
        return null;
      }, querySelectorAll:()=>[]};
      return hooks.collectSettingsFromPanel(container,previous).runtime.startAt;
    })()""")
    assert result == "2026-11-01T05:30:00.250Z"
