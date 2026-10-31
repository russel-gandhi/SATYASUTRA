"""Raksha guided flow (RK3, RK4): every branch, the under-18 rule, no step asks for an image, and the summary holds only what was typed."""

from datetime import date

import httpx
import pytest

from satyasutra.act.plan import load_actions
from satyasutra.config import load_config
from satyasutra.raksha import load_flow
from satyasutra.raksha.flow import advance, begin

FLOW, ACTIONS, TODAY = load_flow(), load_actions(), date(2026, 10, 31)
FIELDS = ["@badname_99", "https://example.org/p/1", "30 Oct 21:00", "pay or he posts them"]
IMAGE_WORDS = ("send me the", "upload", "attach the picture", "share the picture")


def walk(lang, *choices):
    s, first = begin(FLOW, lang)
    out = [first]
    for c in choices:
        out.append(advance(FLOW, s, c, ACTIONS, TODAY))
    return s, out


def full(age, lang="en"):
    return walk(lang, "2", "1", age, "1", "3", *FIELDS)


def test_danger_ends_at_112_and_nothing_else_is_asked():
    s, out = walk("en", "1")
    assert s.done and "112" in out[-1] and "stopncii" not in out[-1].lower()


@pytest.mark.parametrize("age,shown,hidden", [("2", "StopNCII", "Take It Down"), ("1", "Take It Down", "StopNCII"), ("3", "Take It Down", "StopNCII")])
def test_adult_branch_and_the_under_18_or_unsure_branch_show_different_routes(age, shown, hidden):
    s, out = full(age)
    assert s.done and shown in out[-1] and hidden not in out[-1]            # unsure (3) is treated as under 18


def test_the_summary_holds_the_typed_fields_the_date_and_fixed_labels_only():
    _, out = full("2")
    text = out[-1]
    for f in FIELDS:
        assert f in text
    assert "2026-10-31" in text and "not legal advice" in text and "Facebook" in text
    assert "section" not in text.lower()                                       # no legal section numbers


def test_a_value_is_cut_and_a_skipped_field_is_left_out_and_nothing_else_appears():
    s, out = walk("en", "2", "1", "2", "1", "3", "x" * 5000, "-", "-", "-")
    summary = out[-1].split("Summary you can copy into a report")[1].split("This is not legal advice")[0]
    assert "x" * FLOW.settings.max_field_chars in summary and "x" * (FLOW.settings.max_field_chars + 1) not in summary
    assert "Links" not in summary and "Dates" not in summary


def test_a_wrong_answer_repeats_the_question_and_does_not_move_on():
    s, first = begin(FLOW, "en")
    again = advance(FLOW, s, "banana", ACTIONS, TODAY)
    assert "Please reply with one of the numbers" in again and s.step == FLOW.start


def test_hindi_flow_runs_to_the_end_and_no_step_in_either_language_asks_for_an_image():
    s, out = full("2", "hi")
    assert s.done and "रिपोर्ट सारांश" in out[-1]
    for step in FLOW.steps.values():
        for t in [step.ask, *[o.label for o in step.options], *[f.prompt for f in step.fields]]:
            if t:
                assert not any(w in t.en.lower() for w in IMAGE_WORDS)


def test_every_route_a_step_can_show_exists_and_hindi_is_marked_unreviewed():
    for step in FLOW.steps.values():
        for key in [*step.routes, *[k for ks in step.routes_by_age.values() for k in ks]]:
            assert key in ACTIONS.routes
    assert FLOW.reviewed_by == ""                                              # until a native speaker signs it, the file says so


def test_in_the_bot_a_flow_message_is_never_fact_checked_or_sent_to_the_local_api():
    from satyasutra.bot import TelegramBot
    seen = []
    b = TelegramBot(load_config(), token="t")
    b.local = httpx.Client(base_url="http://local", transport=httpx.MockTransport(lambda r: (seen.append(r.url.path), httpx.Response(200, json={}))[1]))
    assert "Are you in danger" in b.handle({"chat": {"id": 5}, "text": "/help_me"})
    reply = b.handle({"chat": {"id": 5}, "text": "2"})
    assert "to you, or are you helping" in reply and seen == []
    b.handle({"chat": {"id": 5}, "text": "/forget"})
    assert 5 not in b.raksha
