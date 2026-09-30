import json

import pytest

from grab.booking.serialized import schedule_record_dates
from tests.userscripts.test_doctor_page_poller import _run_hook


def string(value):
    return f's:{len(value.encode("utf-8"))}:"{value}";'


def array(pairs):
    return (
        f"a:{len(pairs)}:{{"
        + "".join(string(key) + value for key, value in pairs)
        + "}"
    )


RECORD = array([("sch", array([("to_date", string("2030-01-02"))]))])
VALID = array([("synthetic-slot", RECORD)])
CASES = [
    (VALID, ["2030-01-02"]),
    (array([("synthetic-other", RECORD), ("synthetic-slot", "a:0:{}")]), []),
    (array([("synthetic-slot", RECORD), ("synthetic-slot", RECORD)]), []),
    (VALID[:-1], []),
    (VALID.replace('s:14:"synthetic-slot"', 's:1:"synthetic-slot"'), []),
    (
        array(
            [
                (
                    "synthetic-slot",
                    array(
                        [
                            ("label", string('synthetic 中文 { " }')),
                            ("to_date", string("2030-01-02")),
                        ]
                    ),
                )
            ]
        ),
        ["2030-01-02"],
    ),
    (array([("synthetic-slot", 'O:1:"X":0:{}')]), []),
    ("a:1:{" * 40, []),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_serialized_dates_require_one_complete_matching_record(raw, expected):
    assert schedule_record_dates(raw, "synthetic-slot") == expected
    assert (
        _run_hook("hooks.scheduleRecordDates(" + json.dumps(raw) + ',"synthetic-slot")')
        == expected
    )
