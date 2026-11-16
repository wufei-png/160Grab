from functools import partial

import pytest

import grab.core.leader as leader_module
from grab.transactions.store import AttemptStore


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
    # Some strategy tests construct the default store implicitly. They must not
    # read or change a real user's submission journal, including during live runs.
    original_init = AttemptStore.__init__

    def isolated_store_init(self, root=None):
        original_init(
            self, tmp_path / "synthetic-transactions" if root is None else root
        )

    monkeypatch.setattr(AttemptStore, "__init__", isolated_store_init)
