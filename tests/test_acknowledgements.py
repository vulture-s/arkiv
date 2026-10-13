"""The attribution correction has to stay findable.

Five merged commits credit `Co-authored-by: Penny <penny@users.noreply.github.com>`.
That address was invented, and GitHub resolves it to a real, unrelated account — so
the repository's own history currently credits a stranger for another person's work.
Merged history cannot be rewritten (this project never force-pushes `main`), which
makes the correction a document rather than a commit, and a document is exactly the
kind of thing that gets tidied away later by someone who does not know why it exists.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ACK = _ROOT / "ACKNOWLEDGEMENTS.md"


def test_the_acknowledgement_exists():
    assert _ACK.exists()


def test_it_states_the_wrong_trailer_and_why_it_is_wrong():
    text = _ACK.read_text(encoding="utf-8")
    assert "penny@users.noreply.github.com" in text
    assert "invented" in text
    # The point that makes it urgent rather than cosmetic: it lands on a real person.
    assert "github.com/Penny" in text


def test_it_names_the_affected_prs():
    """Someone auditing the history needs to know which commits are affected."""
    text = _ACK.read_text(encoding="utf-8")
    for pr in ("#349", "#350", "#351", "#356", "#357"):
        assert pr in text, "affected PR {0} not listed".format(pr)


def test_it_does_not_publish_the_contributor_s_private_address():
    """This project asks before naming a contributor, and that question is still
    open. Correcting one over-share by committing a personal email would be a
    second one."""
    text = _ACK.read_text(encoding="utf-8")
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    allowed = {"penny@users.noreply.github.com"}  # quoted precisely to disown it
    assert set(emails) <= allowed, "unexpected address published: {0}".format(
        set(emails) - allowed)


def test_the_rule_is_written_where_the_next_person_will_look():
    """An acknowledgement explains one incident; CONTRIBUTING is what someone reads
    before crediting the next mailed-in patch."""
    contributing = (_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "users.noreply.github.com" in contributing
    assert "ACKNOWLEDGEMENTS.md" in contributing
