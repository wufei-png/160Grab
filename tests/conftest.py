from functools import partial

import pytest

import grab.core.leader as leader_module


@pytest.fixture(autouse=True)
def synthetic_local_leader_root(tmp_path, monkeypatch, request):
    # Tests never contend with, or write coordination state into, a user's run.
    if request.node.get_closest_marker("live"):
        return  # Live canary must share the actual product OS-user lock.
    monkeypatch.setattr(
        leader_module,
        "LocalLeader",
        partial(leader_module.LocalLeader, tmp_path / "synthetic-leader"),
    )
