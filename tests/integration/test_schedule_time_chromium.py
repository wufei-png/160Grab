from datetime import UTC, datetime

import pytest

from tests.integration.test_session_chromium import HOOKS, setup


@pytest.mark.parametrize(
    "late,interactive,accept,expected",
    [
        (0, False, False, True),
        (30, False, False, True),
        (30.001, False, True, False),
        (31, True, False, False),
        (31, True, True, True),
    ],
)
async def test_userscript_late_boundary_and_fresh_start_confirmation(
    chromium_page, late, interactive, accept, expected
):
    page = chromium_page
    await setup(page)
    result = await page.evaluate(f"""async () => {{
      const h={HOOKS};let prompts=0;window.confirm=()=>{{prompts++;return {str(accept).lower()};}};
      let id;
      if ({str(interactive).lower()}) id=h.prepareManualControllerStart('doctor');
      else {{h.writeState({{...h.readState(),running:true}});id=h.claimPageController('doctor');}}
      return await h.withBrowserLeader(async()=>{{
        const settings=h.normalizeSettings({{runtime:{{startAt:new Date(Date.now()-{late * 1000}).toISOString()}}}});
        const ready=await h.waitUntilStartAt(settings,id);
        window.result={{ready,prompts,running:h.readState().running}};return window.result;
      }});
    }}""")
    await page.wait_for_function("window.result !== undefined")
    result = await page.evaluate("window.result")
    assert result["ready"] is expected
    assert result["prompts"] == int(interactive and late > 30)
    if not expected:
        assert result["running"] is False


async def test_userscript_subsecond_wait_and_stop_cancel(chromium_page):
    page = chromium_page
    await setup(page)
    await page.evaluate(f"""() => {{
      const h={HOOKS};window.id=h.prepareManualControllerStart('doctor');
      window.confirm=()=>{{throw Error('unexpected prompt');}};
      window.done=false;
      h.withBrowserLeader(async()=>{{window.ready=await h.waitUntilStartAt(h.normalizeSettings({{runtime:{{startAt:new Date(Date.now()+250).toISOString()}}}}),id);done=true;}});
    }}""")
    await page.wait_for_function(
        f"{HOOKS}.readState().summary.message === 'Waiting for configured start time.'"
    )
    await page.clock.run_for(249)
    assert await page.evaluate("done") is False
    await page.clock.run_for(1)
    await page.wait_for_function("done")
    assert await page.evaluate("ready") is True
    await page.evaluate(
        f"""() => {{const h={HOOKS};done=false;id=h.prepareManualControllerStart('doctor');h.withBrowserLeader(async()=>{{ready=await h.waitUntilStartAt(h.normalizeSettings({{runtime:{{startAt:new Date(Date.now()+60000).toISOString()}}}}),id);done=true;}});}}"""
    )
    await page.wait_for_function(
        f"{HOOKS}.readState().summary.message === 'Waiting for configured start time.'"
    )
    await page.evaluate(f"{HOOKS}.stopRun()")
    await page.wait_for_function("done")
    assert await page.evaluate("ready") is False


async def test_userscript_controller_waits_then_requests_zone_date(chromium_page):
    page = chromium_page
    await setup(page)
    instant = datetime(2030, 1, 1, 16, tzinfo=UTC)
    await page.clock.set_system_time(instant)
    await page.evaluate(f"""() => {{
      const h={HOOKS};window.dates=[];window.fetch=async(url,options)=>{{requests++;dates.push(new URL(url).searchParams.get('date'));return {{status:200,text:async()=>JSON.stringify({{schedules:[]}}),headers:{{get:()=>null}}}};}};
      h.writeSettings({{runtime:{{startAt:new Date(Date.now()+250).toISOString()}}}});start();
    }}""")
    await page.wait_for_function(
        f"{HOOKS}.readState().summary.message === 'Waiting for configured start time.'"
    )
    assert await page.evaluate("requests") == 0
    await page.clock.run_for(250)
    await page.wait_for_function("requests === 1")
    assert await page.evaluate("dates") == ["2030-01-02"]
    await page.evaluate(f"{HOOKS}.stopRun()")
    await page.wait_for_function("finished")


async def test_userscript_invalid_config_never_polls(chromium_page):
    page = chromium_page
    await setup(page)
    await page.evaluate(
        f"""() => {{localStorage.setItem({HOOKS}.SETTINGS_KEY,JSON.stringify({{runtime:{{startAt:'bad'}}}}));window.fetch=async()=>{{requests++;throw Error('unexpected');}};start();}}"""
    )
    await page.wait_for_function("finished")
    assert await page.evaluate("requests") == 0


@pytest.mark.parametrize("jump_seconds", [-3600, 3600])
async def test_read_interval_uses_monotonic_clock_during_wall_clock_jump(
    chromium_page, jump_seconds
):
    page = chromium_page
    await setup(page)
    await page.evaluate(
        """() => {window.fetch=async()=>{requests++;return {status:429,text:async()=>'',headers:{get:()=> '60'}};};start();}"""
    )
    await page.wait_for_function(f"{HOOKS}.readState().readFailures === 1")
    current = await page.evaluate("Date.now()")
    await page.clock.set_system_time(
        datetime.fromtimestamp(current / 1000 + jump_seconds, UTC)
    )
    await page.clock.run_for(59000)
    assert await page.evaluate("requests") == 1
    await page.clock.run_for(1000)
    await page.wait_for_function("requests === 2", timeout=1000)
    await page.evaluate(f"{HOOKS}.stopRun()")
    await page.wait_for_function("finished")
