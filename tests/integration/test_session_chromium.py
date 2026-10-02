import json
from datetime import UTC, datetime, timedelta

import pytest

from grab.browser.page_api import BrowserPageApi
from grab.errors import ReadRetryExhausted, UnknownSessionError
from grab.models.schemas import DoctorPageTarget, GrabConfig
from grab.services.schedule import ScheduleService
from tests.userscripts.test_doctor_page_poller import SCRIPT_PATH

URL = "https://www.91160.com/doctors/index/unit_id-u/dep_id-d/docid-doc.html"
HOOKS = "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__"


async def setup(page):
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            body='<a id="addMark" unit_id="u" dep_id="d" doctor_id="doc"></a>',
            content_type="text/html",
        ),
    )
    await page.goto(URL)
    await page.evaluate(
        'window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__=true;window._user_key="SYN_KEY";'
    )
    await page.add_script_tag(content=SCRIPT_PATH.read_text(encoding="utf-8"))
    instant = datetime(2026, 10, 1, tzinfo=UTC)
    await page.clock.install(time=instant)
    await page.clock.pause_at(instant + timedelta(seconds=1))
    await page.evaluate(f"""() => {{
      window.requests=0; window.finished=false;
      window.start=()=>{{const h={HOOKS};const id=h.prepareManualControllerStart('doctor');h.runDoctorPageController(id).then(()=>finished=true);}};
    }}""")


@pytest.mark.parametrize("status", [503, 429])
async def test_userscript_five_failures_pause_without_layered_requests(
    chromium_page, status
):
    page = chromium_page
    await setup(page)
    await page.evaluate(
        f"""() => {{window.fetch=async()=>{{requests++;return {{status:{status},text:async()=> 'SYN_SECRET',headers:{{get:()=> '45'}}}};}};start();}}"""
    )
    await page.wait_for_function(f"{HOOKS}.readState().readFailures === 1")
    # A 45-second Retry-After dominates jitter, poll and cooldown floors.
    await page.clock.run_for(44000)
    assert await page.evaluate("requests") == 1
    await page.clock.run_for(1000)
    await page.wait_for_function("requests === 2")
    for count in [3, 4, 5]:
        await page.clock.run_for(45000)
        await page.wait_for_function(f"requests === {count}")
    await page.wait_for_function("finished")
    state = await page.evaluate(f"{HOOKS}.readState()")
    assert state["running"] is False
    assert state["readFailures"] == 5
    assert "SYN_SECRET" not in json.dumps(state)
    # Start and reset do not grant a fresh consecutive-failure budget.
    await page.evaluate(f"{HOOKS}.resetRuntimeState();finished=false;start()")
    await page.wait_for_function("finished")
    assert await page.evaluate("requests") == 5


@pytest.mark.parametrize("during", ["delay", "request"])
async def test_userscript_stop_cancels_immediately_without_extra_request(
    chromium_page, during
):
    page = chromium_page
    await setup(page)
    await page.evaluate(
        """() => {window.fetch=async()=>{requests++;return new Promise(resolve=>{window.respond=resolve;});};start();}"""
    )
    await page.wait_for_function("requests === 1")
    if during == "delay":
        await page.evaluate(
            "respond({status:503,text:async()=>'',headers:{get:()=> '60'}})"
        )
        await page.wait_for_function(f"{HOOKS}.readState().readFailures === 1")
    await page.evaluate(f"{HOOKS}.stopRun()")
    await page.wait_for_function("finished")
    await page.clock.fast_forward(61000)
    assert await page.evaluate("requests") == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 1, "sch": {"new": "shape"}},
        {"error_code": 10021},
        {
            "result_code": 1,
            "data": {"schedules": []},
            "sch": {
                "changed": {
                    "schedule_id": "slot",
                    "y_state": True,
                    "to_date": "2030-01-01",
                }
            },
        },
    ],
)
async def test_unknown_or_expired_stops_without_navigation_or_booking(
    chromium_page, payload
):
    page = chromium_page
    await setup(page)
    await page.evaluate(
        "(payload)=>{window.fetch=async()=>{requests++;return {status:200,text:async()=>JSON.stringify(payload)};};start();}",
        payload,
    )
    await page.wait_for_function("finished")
    state = await page.evaluate(f"{HOOKS}.readState()")
    assert state["running"] is False
    assert state["sessionState"] == (
        "EXPIRED" if "error_code" in payload else "UNKNOWN"
    )
    assert page.url == URL
    assert await page.evaluate("requests") == 1
    assert state["pendingBooking"] is None
    if "error_code" in payload:
        # Only a valid response resets the manual recovery counter on Start.
        await page.evaluate("finished=false;start()")
        await page.wait_for_function("finished")
        assert await page.evaluate(f"{HOOKS}.readState().sessionRecoveryAttempts") == 2


@pytest.mark.parametrize("status", [503, 429, 200])
async def test_python_transport_single_request_and_conservative_schema(
    chromium_page, status
):
    page = chromium_page
    await setup(page)
    requests = []

    async def response(route):
        requests.append(route.request.url)
        await route.fulfill(
            status=status,
            body='{"code":1,"new":"shape"}',
            headers={
                "Retry-After": "45",
                "Access-Control-Allow-Origin": "https://www.91160.com",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Expose-Headers": "Retry-After",
            },
        )

    await page.context.route("https://gate.91160.com/**", response)
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    reader = ScheduleService(BrowserPageApi(page), GrabConfig(), sleep=sleep)
    reader.set_target(
        DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url=URL)
    )
    with pytest.raises(UnknownSessionError if status == 200 else ReadRetryExhausted):
        await anext(reader.poll())
    assert len(requests) == (1 if status == 200 else 5)
    assert delays == ([] if status == 200 else [45] * 4)


async def test_valid_response_resets_userscript_budget_after_transient_failure(
    chromium_page,
):
    page = chromium_page
    await setup(page)
    await page.evaluate("""() => {
      window.fetch=async()=>{requests++;return requests===1 ? {status:503,text:async()=>'',headers:{get:()=> '10'}} : {status:200,text:async()=>'{"result_code":1,"data":{"schedules":[]}}'};};start();
    }""")
    await page.wait_for_function(f"{HOOKS}.readState().readFailures === 1")
    await page.clock.run_for(10000)
    await page.wait_for_function(f"{HOOKS}.readState().pollAttempt === 1")
    assert await page.evaluate(f"{HOOKS}.readState().readFailures") == 0
    assert await page.evaluate(f"{HOOKS}.readState().sessionState") == "VALID"
    await page.evaluate(f"{HOOKS}.stopRun()")
    await page.wait_for_function("finished")


@pytest.mark.parametrize("reset", [False, True])
async def test_stop_start_keeps_remaining_retry_after_cooldown(chromium_page, reset):
    page = chromium_page
    await setup(page)
    await page.evaluate("""() => {
      window.requestTimes=[];
      window.fetch=async()=>{requests++;requestTimes.push(performance.now());return {status:429,text:async()=>'',headers:{get:()=> '60'}};};start();
    }""")
    await page.wait_for_function(f"{HOOKS}.readState().readFailures === 1")
    await page.evaluate(f"{HOOKS}.stopRun()")
    await page.wait_for_function("finished")
    if reset:
        await page.evaluate(f"{HOOKS}.resetRuntimeState()")
    await page.evaluate("finished=false;start()")
    await page.wait_for_function(f"{HOOKS}.ownsBrowserLeader()")
    await page.clock.run_for(59000)
    assert await page.evaluate("requests") == 1
    await page.clock.run_for(1000)
    await page.wait_for_function(f"{HOOKS}.readState().readFailures === 2")
    times = await page.evaluate("requestTimes")
    assert times[1] - times[0] >= 60000
    await page.evaluate(f"{HOOKS}.stopRun()")
    await page.wait_for_function("finished")


async def test_long_retry_after_does_not_overflow_timer(chromium_page):
    page = chromium_page
    await setup(page)
    await page.evaluate("""() => {
      window.fetch=async()=>{requests++;return {status:429,text:async()=>'',headers:{get:()=> '3000000'}};};start();
    }""")
    await page.wait_for_function(f"{HOOKS}.readState().readFailures === 1")
    await page.clock.run_for(31000)
    assert await page.evaluate("requests") == 1
    await page.evaluate(f"{HOOKS}.stopRun()")
    await page.wait_for_function("finished")
