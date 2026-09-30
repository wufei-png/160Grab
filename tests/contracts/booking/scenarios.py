import json
from html.parser import HTMLParser
from pathlib import Path

FIXTURES = Path(__file__).with_name("fixtures")
REPO_ROOT = Path(__file__).resolve().parents[3]
USERSCRIPT = REPO_ROOT / "userscripts" / "91160-doctor-page-poller.user.js"


def load_scenarios():
    document = json.loads((FIXTURES / "scenarios.v1.json").read_text(encoding="utf-8"))
    if (document["schema_version"], document["contract_version"]) != (1, 1):
        raise ValueError("Unsupported booking scenario version")
    if document["provenance"] != "handwritten-synthetic":
        raise ValueError("Booking harness only accepts synthetic fixtures")
    return document["scenarios"]


def read_html(scenario):
    return (FIXTURES / scenario["html"]).read_text(encoding="utf-8")


class SyntheticDOM(HTMLParser):
    """Minimal fixture snapshot for Node; Chromium uses the actual HTML DOM."""

    def __init__(self, html):
        super().__init__()
        self.schedule_id = ""
        self.options = []
        self.inputs = {}
        self.submit_candidates = 0
        self._option = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            self.inputs[attrs["name"]] = attrs.get("value", "")
            if attrs["name"] == "schedule_id":
                self.schedule_id = attrs.get("value", "")
        if tag == "li":
            self._option = {"value": attrs["val"], "label": ""}
        if tag == "button" and attrs.get("type") == "submit":
            self.submit_candidates += 1

    def handle_data(self, data):
        if self._option is not None:
            self._option["label"] += data

    def handle_endtag(self, tag):
        if tag == "li" and self._option is not None:
            self._option["label"] = self._option["label"].strip()
            self.options.append(self._option)
            self._option = None


SCENARIOS = load_scenarios()
