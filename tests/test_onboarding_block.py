"""One-message /setup (template reply), `/setup k=v` one-liners, partial -> only-missing fallback, and /set."""

from __future__ import annotations

import pytest
from test_onboarding import msg

from jobagent import onboarding as ob
from jobagent.telegram.commands import handle_command, process_updates

FULL_BLOCK = """phone: +91 98765 43210
linkedin: https://www.linkedin.com/in/vishal-sharma
github: https://github.com/VishalSharma7987
portfolio: (optional)
location: Pune"""


@pytest.fixture
def s(settings):
    """No env fallbacks, so 'missing' is decided by the message alone."""
    return settings.model_copy(update={"github_url": None, "candidate_phone": None, "linkedin_url": None,
                                       "portfolio_url": None})


# ================================================================ parse_block
def test_parses_a_full_multiline_block():
    p = ob.parse_block(FULL_BLOCK)
    assert p.values == {"phone": "+919876543210", "linkedin": "https://www.linkedin.com/in/vishal-sharma",
                        "github": "https://github.com/VishalSharma7987", "location": "Pune"}
    assert p.errors == {} and "portfolio" in p.seen  # the '(optional)' placeholder = not provided


def test_parses_single_line_key_value_form_with_spaces_in_values():
    p = ob.parse_block("phone=+919876543210 linkedin=linkedin.com/in/x github=github.com/y location=New Delhi")
    assert p.values == {"phone": "+919876543210", "linkedin": "https://www.linkedin.com/in/x",
                        "github": "https://github.com/y", "location": "New Delhi"}


def test_aliases_case_and_separators():
    p = ob.parse_block("Mobile = 9876543210\nCITY: Hyderabad\nWebsite: vishal.dev")
    assert p.values == {"phone": "+919876543210", "location": "Hyderabad", "portfolio": "https://vishal.dev"}


def test_untouched_template_placeholders_are_ignored_not_errors():
    p = ob.parse_block("phone: +91...\nlinkedin: https://...\ngithub: https://...\nportfolio: (optional)\nlocation: City")
    assert p.values == {} and p.errors == {} and set(p.seen) == set(ob.FIELDS)


def test_invalid_values_are_reported_per_field():
    p = ob.parse_block("phone: 12345\nlinkedin: https://evil.com/in/x\ngithub: https://github.com/a/b\nlocation: 123")
    assert set(p.errors) == {"phone", "linkedin", "github", "location"} and p.values == {}


def test_skip_only_for_optional_fields():
    p = ob.parse_block("linkedin: skip\ngithub: none\nportfolio: -\nphone: skip\nlocation: skip")
    assert p.values == {"linkedin": "", "github": "", "portfolio": ""} and set(p.errors) == {"phone", "location"}


def test_keep_uses_current_value():
    p = ob.parse_block("phone: keep\nlocation: keep", {"phone": "+911111111111"})
    assert p.values == {"phone": "+911111111111"} and set(p.errors) == {"location"}  # nothing to keep for location


def test_no_recognised_keys():
    assert ob.parse_block("hello there, here is my number 9876543210").seen == []


def test_template_prefills_known_values_and_placeholders_otherwise():
    t = ob.template({"github": "https://github.com/me"})
    for line in ("phone: +91...", "linkedin: https://...", "github: https://github.com/me", "portfolio: (optional)", "location: City"):
        assert line in t


# ================================================================ through Telegram
def test_setup_replies_with_template_then_whole_block_saves_everything(repo, s, tg, store):
    process_updates([msg(1, "/setup")], repo, tg, s, store)
    assert "ONE message" in tg.sent[-1] and "phone: +91..." in tg.sent[-1] and "location: City" in tg.sent[-1]
    assert repo.get_profile_row()["onboarding_state"]["step"] == "block"
    process_updates([msg(2, FULL_BLOCK)], repo, tg, s, store)
    row = repo.get_profile_row()
    assert (row["phone"], row["linkedin_url"], row["github_url"], row["location"]) == (
        "+919876543210", "https://www.linkedin.com/in/vishal-sharma", "https://github.com/VishalSharma7987", "Pune")
    assert row["portfolio_url"] is None and row["onboarding_state"] is None
    reply = tg.sent[-1]
    assert reply.startswith("✅ Details saved:") and "Phone: +919876543210" in reply and "Location: Pune" in reply


def test_one_line_setup_with_arguments(repo, s, tg, store):
    process_updates([msg(1, "/setup phone=+919876543210 linkedin=skip github=github.com/vishal location=Pune")], repo, tg, s, store)
    row = repo.get_profile_row()
    assert row["phone"] == "+919876543210" and row["github_url"] == "https://github.com/vishal" and row["location"] == "Pune"
    assert row["linkedin_url"] is None and row["onboarding_state"] is None and "saved" in tg.sent[-1]


def test_block_pasted_right_after_the_command(repo, s, tg, store):
    process_updates([msg(1, "/setup\n" + FULL_BLOCK)], repo, tg, s, store)
    assert repo.get_profile_row()["phone"] == "+919876543210" and repo.get_profile_row()["onboarding_state"] is None


def test_partial_block_falls_back_to_asking_only_for_the_missing_fields(repo, s, tg, store):
    process_updates([msg(1, "/setup phone=+919876543210 github=github.com/vishal")], repo, tg, s, store)
    assert "Still missing: LinkedIn, Location" in tg.sent[-1] and "phone" not in tg.sent[-1].split("Still missing")[1].lower()
    assert repo.get_profile_row().get("phone") is None  # nothing saved until the flow completes
    state = repo.get_profile_row()["onboarding_state"]
    assert state["step"] == "linkedin" and state["queue"] == ["linkedin", "location"]
    process_updates([msg(2, "linkedin.com/in/vishal")], repo, tg, s, store)
    assert "location" in tg.sent[-1].lower() and "2/5" not in tg.sent[-1]  # plain prompt, only the missing ones
    process_updates([msg(3, "Pune")], repo, tg, s, store)
    row = repo.get_profile_row()
    assert (row["phone"], row["github_url"], row["linkedin_url"], row["location"]) == (
        "+919876543210", "https://github.com/vishal", "https://www.linkedin.com/in/vishal", "Pune")
    assert row["onboarding_state"] is None and "Details saved" in tg.sent[-1]


def test_partial_block_with_stored_values_updates_only_what_was_sent(repo, s, tg, store):
    process_updates([msg(1, "/setup"), msg(2, FULL_BLOCK)], repo, tg, s, store)
    process_updates([msg(3, "/setup location=Bangalore")], repo, tg, s, store)  # nothing missing: everything else is stored
    row = repo.get_profile_row()
    assert row["location"] == "Bangalore" and row["phone"] == "+919876543210" and row["github_url"].endswith("VishalSharma7987")


def test_env_values_count_as_present_so_they_are_not_asked_for(repo, settings, tg, store):
    # settings fixture has GITHUB_URL=https://github.com/example (env fallback) -> github is not "missing"
    process_updates([msg(1, "/setup phone=+919876543210 linkedin=skip location=Pune")], repo, tg, settings, store)
    assert repo.get_profile_row()["onboarding_state"] is None and "Details saved" in tg.sent[-1]


def test_invalid_lines_keep_the_valid_ones_and_ask_for_just_those(repo, s, tg, store):
    process_updates([msg(1, "/setup"), msg(2, "phone: nope\nlinkedin: https://www.linkedin.com/in/ok\ngithub: skip\nlocation: Pune")],
                    repo, tg, s, store)
    assert "I could not accept" in tg.sent[-1] and "phone" in tg.sent[-1] and "valid ones were kept" in tg.sent[-1]
    assert repo.get_profile_row().get("phone") is None
    process_updates([msg(3, "phone: +919876543210")], repo, tg, s, store)  # only the corrected line is needed
    row = repo.get_profile_row()
    assert row["phone"] == "+919876543210" and row["linkedin_url"].endswith("/in/ok") and row["location"] == "Pune"
    assert row["onboarding_state"] is None


def test_block_mode_with_no_key_lines_resends_the_template(repo, s, tg, store):
    process_updates([msg(1, "/setup"), msg(2, "my number is 9876543210")], repo, tg, s, store)
    assert "couldn't find any" in tg.sent[-1] and "phone: +91..." in tg.sent[-1]
    assert repo.get_profile_row()["onboarding_state"]["step"] == "block"  # still waiting


def test_untouched_template_is_treated_as_nothing_provided_and_asks_for_all_required(repo, s, tg, store):
    process_updates([msg(1, "/setup"), msg(2, "phone: +91...\nlinkedin: https://...\ngithub: https://...\nportfolio: (optional)\nlocation: City")],
                    repo, tg, s, store)
    assert "Still missing: Phone, LinkedIn, GitHub, Location" in tg.sent[-1]


def test_cancel_in_block_mode(repo, s, tg, store):
    process_updates([msg(1, "/setup"), msg(2, "cancel")], repo, tg, s, store)
    assert repo.get_profile_row()["onboarding_state"] is None and "cancelled" in tg.sent[-1].lower()


def test_old_step_flow_still_available_as_setup_steps(repo, s, tg, store):
    process_updates([msg(1, "/setup steps")], repo, tg, s, store)
    assert "1/5" in tg.sent[-1] and repo.get_profile_row()["onboarding_state"]["step"] == "phone"


def test_switching_to_key_value_lines_during_the_step_flow(repo, s, tg, store):
    process_updates([msg(1, "/setup steps"), msg(2, FULL_BLOCK)], repo, tg, s, store)
    assert repo.get_profile_row()["phone"] == "+919876543210" and repo.get_profile_row()["onboarding_state"] is None


# ================================================================ /set
@pytest.mark.parametrize("text,column,value", [
    ("/set phone +91 98765 43210", "phone", "+919876543210"),
    ("/set phone 9876543210", "phone", "+919876543210"),
    ("/set linkedin linkedin.com/in/vishal", "linkedin_url", "https://www.linkedin.com/in/vishal"),
    ("/set github https://github.com/vishal", "github_url", "https://github.com/vishal"),
    ("/set portfolio vishal.dev", "portfolio_url", "https://vishal.dev"),
    ("/set location New Delhi", "location", "New Delhi"),
    ("/set city Pune", "location", "Pune"),
    ("/set mobile +14155552671", "phone", "+14155552671"),
])
def test_set_updates_one_field(repo, s, text, column, value):
    res = handle_command(text, repo, s).reply
    assert "✅" in res and repo.get_profile_row()[column] == value


def test_set_leaves_other_fields_alone_and_can_clear_optional_ones(repo, s):
    handle_command("/set phone +919876543210", repo, s)
    handle_command("/set github github.com/vishal", repo, s)
    handle_command("/set github skip", repo, s)
    row = repo.get_profile_row()
    assert row["phone"] == "+919876543210" and row["github_url"] is None
    assert "(cleared)" in handle_command("/set portfolio skip", repo, s).reply


@pytest.mark.parametrize("text,fragment", [
    ("/set", "Usage"), ("/set phone", "Usage"), ("/set colour red", "Usage"),
    ("/set phone 12345", "phone number"), ("/set linkedin https://evil.com/in/x", "LinkedIn"),
    ("/set location 42", "city"), ("/set phone skip", "phone number"),
])
def test_set_rejects_bad_input_without_saving(repo, s, text, fragment):
    assert fragment.lower() in handle_command(text, repo, s).reply.lower()
    row = repo.get_profile_row() or {}
    assert not any(row.get(c) for c in ("phone", "linkedin_url", "github_url", "location"))


def test_set_works_through_telegram_and_does_not_disturb_a_running_setup(repo, s, tg, store):
    process_updates([msg(1, "/setup"), msg(2, "/set phone +919876543210"), msg(3, "/profile")], repo, tg, s, store)
    assert "+919876543210" in tg.sent[-1] and "/setup is in progress" in tg.sent[-1]
    assert repo.get_profile_row()["onboarding_state"]["step"] == "block"
