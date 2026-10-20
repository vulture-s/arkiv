"""`search.py --all-projects` must ask the same question GET /api/search/all asks.

Before this, the HTTP route refused cross-project search on a free install
while the CLI on the same machine answered it: one gated door, one open.
"""

import entitlements
import federation
import projects
import search


def _stub(monkeypatch, allowed):
    calls = []
    monkeypatch.setattr(projects, "known_project_dbs", lambda: [])
    monkeypatch.setattr(
        entitlements,
        "check_cross_project",
        lambda db_paths=None, **kw: entitlements.Verdict(
            allowed, "pro" if allowed else "cross_project", "REASON-TEXT"
        ),
    )

    def fake_search_all(query, **kw):
        calls.append(query)
        return {"items": [{"media_id": 1, "score": 1.0}], "errors": [],
                "projects_queried": 2, "projects_failed": 0}

    monkeypatch.setattr(federation, "search_all_projects", fake_search_all)
    return calls


def test_refused_before_any_fan_out(monkeypatch, capsys):
    calls = _stub(monkeypatch, allowed=False)
    for argv in (["--query", "q", "--all-projects"], ["--query", "q", "--projects", "a,b"],
                 ["--query", "q", "--tag", "t"]):
        assert search.main(argv) == search.ENTITLEMENT_EXIT
        assert "REASON-TEXT" in capsys.readouterr().err
    assert calls == []


def test_entitled_install_still_searches(monkeypatch):
    calls = _stub(monkeypatch, allowed=True)
    assert search.main(["--query", "q", "--all-projects", "--format", "json"]) == 0
    assert calls == ["q"]


def test_exit_code_does_not_collide():
    assert search.ENTITLEMENT_EXIT not in (0, 1, 2, 3, 4)
