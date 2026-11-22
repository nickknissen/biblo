from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from biblo import Biblo, __version__
from biblo.cli import _state, main
from tests.fake import LIBRARY, PHYSICAL_RECORD, PIN, USER, FakeBiblo

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


def test_search_all_catalogues_finds_print(run: Run, fake: FakeBiblo) -> None:
    code, out, _ = run("search", "harry potter", "-l", LIBRARY, "--catalogue", "all", "--json")
    assert code == 0
    formats = {t["display"] for w in json.loads(out) for t in w["material_types"]}
    assert "bog" in formats
    assert fake.api_requests("/search/works")[-1].headers["x-app-type"] == "mybiblo"


def test_work_in_all_catalogues_lists_printed_editions(run: Run, fake: FakeBiblo) -> None:
    work_id = "work-of:870970-basis:22252852"
    code, out, _ = run("work", work_id, "-l", LIBRARY, "--catalogue", "all", "--json")
    assert code == 0
    formats = {v["material_type"]["display"] for v in json.loads(out)["work"]["variations"]}
    assert "bog" in formats
    assert {r.headers["x-app-type"] for r in fake.api_requests("/works/")} == {"mybiblo"}


def test_unknown_catalogue_is_a_usage_error(run: Run) -> None:
    code, _, err = run("search", "harry potter", "-l", LIBRARY, "--catalogue", "nope")
    assert code == 2
    assert "catalogue" in err


def test_library_by_name(run: Run) -> None:
    code, out, err = run("search", "potter", "-l", "vejle", "-n", "1")
    assert code == 0, err


def test_the_cli_names_itself_on_every_api_request(run: Run, fake: FakeBiblo) -> None:
    code, _, err = run("search", "potter", "-l", "vejle", "-n", "1")
    assert code == 0, err
    agents = [r.headers["user-agent"] for r in fake.api_requests()]
    assert len(agents) >= 2  # resolving the library by name, then the search
    assert all(a.startswith(f"biblo-cli/{__version__} biblo-python/") for a in agents)


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


def test_reservations_and_loans_show_physical_items(run: Run, logged_in: None) -> None:
    code, out, err = run("reservations")
    assert code == 0, err
    assert "Harry Potter" in out
    assert "En trykt bog" in out
    assert "number 3 in queue" in out

    code, out, err = run("reservations", "--json")
    assert code == 0, err
    listed = json.loads(out)
    assert [r["id"] for r in listed] == ["r-1", "p-1"]
    assert listed[1]["physical"]["pickup_branch"] == "DK-763004"

    code, out, err = run("loans")
    assert code == 0, err
    assert "Harry Potter" in out
    assert "En trykt bog" in out

    code, out, err = run("loans", "--json")
    assert code == 0, err
    assert [loan["id"] for loan in json.loads(out)] == ["123456", "p-loan-1"]


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
    run: Run, logged_in: None
) -> None:
    code, _, err = run("reserve", PHYSICAL_RECORD)
    assert code == 1
    assert "No material types found for records" in err
    assert "physical item" in err
    assert "--branch" in err
    assert "biblo account set --pickup-branch" in err
    assert "pickup_branch_id" not in err  # the library's wording, not the CLI's


def test_reserve_with_a_branch_reserves_a_physical_record(
    run: Run, fake: FakeBiblo, logged_in: None
) -> None:
    code, _, err = run("reserve", PHYSICAL_RECORD, "--branch", "DK-763004")
    assert code == 0, err
    [post] = [r for r in fake.api_requests("/account/reservations") if r.method == "POST"]
    assert post.headers["x-app-type"] == "mybiblo"


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
