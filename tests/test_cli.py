from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from biblo import Biblo
from biblo.cli import _state, main
from tests.fake import LIBRARY, PIN, USER, FakeBiblo

Run = Callable[..., tuple[int, str, str]]


@pytest.fixture(autouse=True)
def cli_env(fake: FakeBiblo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at a temp config dir and the fake API."""
    monkeypatch.setenv("BIBLO_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("BIBLO_LIBRARY", raising=False)
    monkeypatch.delenv("BIBLO_USER", raising=False)
    monkeypatch.delenv("BIBLO_PIN", raising=False)
    monkeypatch.setenv("COLUMNS", "200")

    class FakeBackedBiblo(Biblo):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(fake.handle))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(_state, "Biblo", FakeBackedBiblo)
    return tmp_path


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> Run:
    """Run the real entry point, so its error handling and exit codes are tested too."""

    def invoke(*args: str) -> tuple[int, str, str]:
        with pytest.raises(SystemExit) as exit_info:
            main(list(args))
        captured = capsys.readouterr()
        code = exit_info.value.code
        status = code if isinstance(code, int) else (0 if code is None else 1)
        return status, captured.out, captured.err

    return invoke


def test_search_table_and_json(run: Run) -> None:
    code, out, _ = run("search", "harry potter", "-l", LIBRARY, "-n", "3")
    assert code == 0
    assert "Harry Potter" in out
    assert "work-of:" in out

    code, out, _ = run("search", "harry potter", "-l", LIBRARY, "-n", "2", "--json")
    works = json.loads(out)
    assert len(works) == 2
    assert works[0]["title"]


def test_library_by_name(run: Run) -> None:
    code, out, err = run("search", "potter", "-l", "vejle", "-n", "1")
    assert code == 0, err


def test_ambiguous_library_name_is_a_usage_error(run: Run) -> None:
    code, _, err = run("search", "potter", "-l", "bibliotek")
    assert code == 2
    assert "several libraries" in err


def test_without_a_library_says_how_to_pick_one(run: Run) -> None:
    code, _, err = run("search", "potter")
    assert code == 2
    assert "--library" in err


def test_account_commands_need_login_with_exit_code_4(run: Run) -> None:
    code, _, err = run("loans")
    assert code == 4
    assert "biblo auth login" in err


def test_login_then_loans_then_logout(
    run: Run, cli_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BIBLO_PIN", PIN)
    code, _, err = run("auth", "login", "-l", LIBRARY, "--user", USER)
    assert code == 0, err
    assert "Logged in to" in err
    session_file = cli_env / "session.json"
    assert json.loads(session_file.read_text())["library_id"] == LIBRARY
    assert PIN not in session_file.read_text()

    code, out, _ = run("loans")
    assert code == 0
    assert "Harry Potter" in out

    code, out, _ = run("auth", "status", "--json")
    assert json.loads(out)["logged_in"] is True

    code, _, _ = run("auth", "logout")
    assert code == 0
    assert json.loads(session_file.read_text())["tokens"] is None


def test_wrong_pincode(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIBLO_PIN", "0000")
    code, _, err = run("auth", "login", "-l", LIBRARY, "--user", USER)
    assert code == 1
    assert "Login refused" in err


def test_api_command_fills_in_the_library(run: Run) -> None:
    code, out, _ = run("api", "v1/agencies/{agencyId}/search/works", "-p", "q=x", "-l", LIBRARY)
    assert code == 0
    assert json.loads(out)["hitcount"] == 12


@pytest.fixture
def logged_in(run: Run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIBLO_PIN", PIN)
    code, _, err = run("auth", "login", "-l", LIBRARY, "--user", USER)
    assert code == 0, err


def test_set_pickup_branch_by_part_of_its_name(run: Run, fake: FakeBiblo, logged_in: None) -> None:
    code, _, err = run("account", "set", "--pickup-branch", "børkop")
    assert code == 0, err
    assert "Børkop Bibliotek" in err
    [patch] = [r for r in fake.api_requests("/account") if r.method == "PATCH"]
    assert json.loads(patch.content) == {"preferredPickupBranchId": "DK-763004"}

    code, out, _ = run("account")
    assert "Pickup branch: Børkop Bibliotek" in out


def test_set_pickup_branch_by_id_prints_the_account_as_json(
    run: Run, fake: FakeBiblo, logged_in: None
) -> None:
    code, out, err = run("account", "set", "--pickup-branch", "dk-763007", "--json")
    assert code == 0, err
    [patch] = [r for r in fake.api_requests("/account") if r.method == "PATCH"]
    assert json.loads(patch.content) == {"preferredPickupBranchId": "DK-763007"}
    assert json.loads(out)["preferred_pickup_branch"] == {
        "id": "DK-763007",
        "name": "Vejle Bibliotek - Spinderiet",
    }


def test_unknown_pickup_branch_is_a_usage_error(run: Run, logged_in: None) -> None:
    code, _, err = run("account", "set", "--pickup-branch", "atlantis")
    assert code == 2
    assert "biblo branches" in err


def test_ambiguous_pickup_branch_lists_the_candidates(run: Run, logged_in: None) -> None:
    code, _, err = run("account", "set", "--pickup-branch", "bus")
    assert code == 2
    assert "DK-763020" in err
    assert "FBS-630009" in err


def test_set_pickup_branch_needs_login(run: Run) -> None:
    code, _, err = run("account", "set", "--pickup-branch", "børkop")
    assert code == 4
    assert "biblo auth login" in err


def test_account_without_a_subcommand_still_shows_it(run: Run, logged_in: None) -> None:
    code, out, _ = run("account")
    assert code == 0
    assert "Test Testesen" in out
    assert "Digital quota" in out
    assert "Pickup branch" not in out

    code, out, _ = run("account", "--json")
    assert set(json.loads(out)) == {"account", "quotas", "fees"}


def reserved_with(fake: FakeBiblo) -> dict[str, Any]:
    [post] = [r for r in fake.api_requests("/account/reservations") if r.method == "POST"]
    return json.loads(post.content)


def test_reserve_picks_up_at_the_preferred_branch(
    run: Run, fake: FakeBiblo, logged_in: None
) -> None:
    fake.account["preferredPickupBranch"] = {"id": "DK-763004", "name": "Børkop Bibliotek"}
    code, _, err = run("reserve", "870970-basis:141661116")
    assert code == 0, err
    assert reserved_with(fake)["pickupBranchId"] == "DK-763004"


def test_reserve_branch_option_wins_over_the_preferred_branch(
    run: Run, fake: FakeBiblo, logged_in: None
) -> None:
    fake.account["preferredPickupBranch"] = {"id": "DK-763004", "name": "Børkop Bibliotek"}
    code, _, err = run("reserve", "870970-basis:141661116", "--branch", "DK-763007")
    assert code == 0, err
    assert reserved_with(fake)["pickupBranchId"] == "DK-763007"


def test_reserve_without_any_branch_sends_none(run: Run, fake: FakeBiblo, logged_in: None) -> None:
    code, _, err = run("reserve", "870970-basis:141661116")
    assert code == 0, err
    assert "pickupBranchId" not in reserved_with(fake)


def test_rejected_reservation_without_a_branch_says_how_to_give_one(
    run: Run, fake: FakeBiblo, logged_in: None
) -> None:
    fake.overrides[("POST", f"/v1/agencies/{LIBRARY}/account/reservations")] = lambda _: (
        httpx.Response(
            400,
            json={
                "id": 4000,
                "type": "BAD_REQUEST",
                "message": "No material types found for records",
            },
        )
    )
    code, _, err = run("reserve", "870970-basis:141661116")
    assert code == 1
    assert "No material types found for records" in err
    assert "--branch" in err
    assert "biblo account set --pickup-branch" in err


def test_login_hints_at_setting_a_pickup_branch_only_when_there_is_none(
    run: Run, fake: FakeBiblo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BIBLO_PIN", PIN)
    code, _, err = run("auth", "login", "-l", LIBRARY, "--user", USER)
    assert code == 0, err
    assert "biblo account set --pickup-branch" in err

    fake.account["preferredPickupBranch"] = {"id": "DK-763004", "name": "Børkop Bibliotek"}
    code, _, err = run("auth", "login", "-l", LIBRARY, "--user", USER)
    assert code == 0, err
    assert "--pickup-branch" not in err


def test_version(run: Run) -> None:
    code, out, _ = run("--version")
    assert code == 0
    assert out.startswith("biblo ")


def test_bad_filter_is_a_usage_error(run: Run) -> None:
    code, _, err = run("search", "x", "-l", LIBRARY, "-f", "colour=red")
    assert code == 2
    assert "material_types_specific" in err
