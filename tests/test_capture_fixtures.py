"""scripts/capture_fixtures.py --session, run against the fake API: never a real account."""

from __future__ import annotations

import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from tests.conftest import saved_session
from tests.fake import FIXTURES, FakeBiblo

SCRIPT = Path(__file__).parents[1] / "scripts" / "capture_fixtures.py"

# What the hand capture of 2026-11-23 kept (tests/fixtures/README.md).
ACCOUNT_FIXTURES = {
    "account",
    "account_fees",
    "account_loans",
    "account_loans_biblogo",
    "account_loans_mybiblo",
    "account_quotas",
    "account_reservations",
    "account_reservations_mybiblo",
    "favorites",
    "favorites_biblogo",
    "favorites_mybiblo",
    "messages",
    "messages_mybiblo",
}
# Made-up personal values, standing in for a real account's.
NAME, EMAIL, PHONE = "Jens Peter Hansen", "jens.hansen@mail.example", "29876543"
CLAIM, MESSAGE = "Bør017", "6f0c2a49-3a8e-4bd4-9a51-1c2d3e4f5a6b"


@pytest.fixture(scope="module")
def capture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_fixtures", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def session_file(tmp_path: Path, fake: FakeBiblo) -> Path:
    fake.token_number = 1
    path = tmp_path / "session.json"
    path.write_text(json.dumps(saved_session(access="access-1", refresh="refresh-1")))
    return path


@pytest.fixture
def run(capture: ModuleType, fake: FakeBiblo, session_file: Path, tmp_path: Path) -> Any:
    def invoke(*names: str, session: Path = session_file) -> int:
        with httpx.Client(transport=httpx.MockTransport(fake.handle)) as http:
            return capture.main(
                ["--session", str(session), *names], http=http, out=tmp_path / "out"
            )

    return invoke


def written(tmp_path: Path) -> dict[str, Any]:
    out = tmp_path / "out"
    return {p.stem: json.loads(p.read_text()) for p in out.glob("*.json")} if out.exists() else {}


def with_real_values(fake: FakeBiblo) -> None:
    """Give the fake's (already anonymised) account personal values to hide."""
    fake.account.update(
        name=NAME,
        email={"emailAddress": EMAIL, "receiveNotification": True},
        phoneNumber={"phoneNumber": PHONE, "receiveNotification": False},
    )
    for listed in fake.loans.values():
        for loan in listed:
            loan["id"] = "real-loan-" + loan["record"]["recordId"].split(":")[1]
    for i, reservation in enumerate(fake.reservations["mybiblo"]):
        reservation["id"] = f"55500000{i}"
    fake.reservations["mybiblo"][0]["claim"]["id"] = CLAIM
    fake.messages["mybiblo"][0]["id"] = MESSAGE


def test_capturing_the_recorded_account_reproduces_its_fixtures(
    run: Any, fake: FakeBiblo, tmp_path: Path
) -> None:
    """Placeholders map to themselves, so a re-capture changes nothing: same files, same
    numbering, same formatting."""
    assert run() == 0
    files = written(tmp_path)
    assert set(files) == ACCOUNT_FIXTURES
    for name in ACCOUNT_FIXTURES:
        assert (tmp_path / "out" / f"{name}.json").read_text() == (
            FIXTURES / f"{name}.json"
        ).read_text(), name


def test_capture_sends_only_gets_under_each_accepted_app_type(run: Any, fake: FakeBiblo) -> None:
    assert run() == 0
    assert {r.method for r in fake.requests} == {"GET"}
    sent = {(r.url.path.rsplit("/", 1)[-1], r.headers["x-app-type"]) for r in fake.requests}
    assert ("quotas", "mybiblo") not in sent  # the spec says it would be refused
    assert ("messages", "biblogo") not in sent
    assert {("loans", t) for t in ("biblo", "biblogo", "mybiblo")} <= sent


def test_personal_values_are_replaced_and_ids_link_up(
    run: Any, fake: FakeBiblo, tmp_path: Path
) -> None:
    with_real_values(fake)
    assert run() == 0
    files = written(tmp_path)
    text = "".join((tmp_path / "out" / f"{name}.json").read_text() for name in files)
    for real in (NAME, EMAIL, PHONE, CLAIM, MESSAGE, "real-loan-", "55500000"):
        assert real not in text

    account = files["account"]["body"]["data"]
    assert account["name"] == "Test Låner"
    assert account["email"]["emailAddress"] == "laaner1@example.com"
    assert account["phoneNumber"]["phoneNumber"] == "12345671"
    assert account["preferredPickupBranch"] == {"id": "DK-763004", "name": "Børkop Bibliotek"}

    # The same loan has the same placeholder under Biblo and Biblo GO.
    def loan_ids(name: str) -> dict[str, str]:
        return {loan["record"]["recordId"]: loan["id"] for loan in files[name]["body"]["data"]}

    biblo, biblogo = loan_ids("account_loans"), loan_ids("account_loans_biblogo")
    assert len(set(biblo.values())) == 3
    assert all(loan_id.startswith("TestLoan") for loan_id in biblo.values())
    assert biblogo == {record: biblo[record] for record in biblogo}

    [ready, *_] = files["account_reservations_mybiblo"]["body"]["data"]
    assert ready["id"] == "900000001"
    assert ready["claim"]["id"] == "Xxx001"
    assert ready["details"]["recordId"] == "870970-basis:142848996"  # public ids stay
    [message] = files["messages_mybiblo"]["body"]["data"]["messages"]
    assert message["id"] == "00000000-0000-4000-8000-000000000001"


def test_a_value_left_after_anonymising_stops_the_capture(
    run: Any, fake: FakeBiblo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with_real_values(fake)
    fake.messages["mybiblo"][0]["body"] = f"Hej {NAME}, din reservering er klar."
    assert run() == 1
    assert written(tmp_path) == {}
    out, err = capsys.readouterr()
    assert "messages_mybiblo.json: the account name from account.json data.name" in err
    assert NAME not in out + err


def test_only_the_named_fixtures_are_captured(run: Any, fake: FakeBiblo, tmp_path: Path) -> None:
    assert run("account_loans") == 0
    assert set(written(tmp_path)) == {
        "account_loans",
        "account_loans_biblogo",
        "account_loans_mybiblo",
    }
    assert {r.url.path for r in fake.requests} == {"/v1/agencies/763000/account/loans"}


def test_an_answer_the_same_as_biblos_replaces_an_old_variant(
    run: Any, fake: FakeBiblo, tmp_path: Path
) -> None:
    stale = tmp_path / "out" / "account_loans_biblogo.json"
    stale.parent.mkdir()
    stale.write_text("{}")
    fake.loans["biblogo"] = fake.loans["biblo"]
    assert run("account_loans") == 0
    assert not stale.exists()


def test_an_expiring_session_is_not_refreshed(
    run: Any, fake: FakeBiblo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refreshing is a POST: the script asks for a fresh session instead."""
    expired = tmp_path / "expired.json"
    expired.write_text(json.dumps(saved_session(expires_in=timedelta(seconds=-1))))
    assert run(session=expired) == 1
    assert fake.requests == []
    assert "biblo account" in capsys.readouterr().err


def test_no_session_and_unknown_names_are_refused(
    run: Any, fake: FakeBiblo, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(session=tmp_path / "missing.json") == 1
    assert "biblo auth login" in capsys.readouterr().err
    assert run("search") == 2  # an anonymous fixture, not an account one
    assert fake.requests == []
