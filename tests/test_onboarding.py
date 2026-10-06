"""Telegram onboarding: /setup state machine, resume upload, /profile, /myresume, runtime fallback order."""

from __future__ import annotations

import logging

import pytest
from conftest import CHAT, FakeTelegram
from fake_supabase import FakeClient

from jobagent import onboarding as ob
from jobagent.db.sqlite_repo import SqliteRepository
from jobagent.db.supabase_repo import SupabaseRepository
from jobagent.runtime import load_runtime_profile
from jobagent.storage import LocalResumeStore, SupabaseResumeStore
from jobagent.telegram.commands import handle_command, process_updates
from jobagent.telegram.uploads import handle_document, variant_from_caption

PDF = b"%PDF-1.4\n" + b"x" * 2048


def msg(uid: int, text: str | None = None, chat: str = CHAT, document: dict | None = None, caption: str | None = None) -> dict:
    m: dict = {"chat": {"id": int(chat)}}
    if text is not None:
        m["text"] = text
    if document:
        m["document"] = document
    if caption is not None:
        m["caption"] = caption
    return {"update_id": uid, "message": m}


def pdf_doc(file_id="f1", size=2057, name="CV.pdf", mime="application/pdf") -> dict:
    return {"file_id": file_id, "file_name": name, "mime_type": mime, "file_size": size}


# ================================================================ validators
@pytest.mark.parametrize("raw,expected", [
    ("+919876543210", "+919876543210"), ("+91 98765 43210", "+919876543210"), ("9876543210", "+919876543210"),
    ("09876543210", "+919876543210"), ("919876543210", "+919876543210"), ("+1 (415) 555-2671", "+14155552671"),
])
def test_phone_accepted_and_normalised(raw, expected):
    assert ob.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "12345", "+91", "5876543210", "+0123456789", "98765 4321x", "+9198765432101234567"])
def test_phone_rejected(raw):
    assert ob.normalize_phone(raw) is None


def test_url_validators():
    assert ob.normalize_linkedin("linkedin.com/in/vishal-sharma") == "https://www.linkedin.com/in/vishal-sharma"
    assert ob.normalize_linkedin("https://in.linkedin.com/in/abc/") == "https://www.linkedin.com/in/abc"
    assert ob.normalize_linkedin("https://linkedin.com/company/x") is None and ob.normalize_linkedin("https://evil.com/in/x") is None
    assert ob.normalize_github("github.com/VishalSharma7987") == "https://github.com/VishalSharma7987"
    assert ob.normalize_github("https://github.com/a/b") is None and ob.normalize_github("https://gitlab.com/a") is None
    assert ob.normalize_portfolio("vishal.dev") == "https://vishal.dev" and ob.normalize_portfolio("not a url") is None
    assert ob.normalize_location("Pune") == "Pune" and ob.normalize_location("  New   Delhi, India ") == "New Delhi, India"
    assert ob.normalize_location("123") is None and ob.normalize_location("x") is None


# ================================================================ state machine
def run_flow(answers: list[str], current: dict | None = None):
    step = ob.start(current or {})
    state, replies = step.state, [step.reply]
    save = None
    for a in answers:
        res = ob.advance(state, a)
        state, save = res.state, res.save
        replies.append(res.reply)
    return state, replies, save


def test_full_flow_collects_everything_and_saves_on_confirm():
    state, replies, save = run_flow(["+91 98765 43210", "linkedin.com/in/vishal", "github.com/VishalSharma7987",
                                     "skip", "Pune", "yes"])
    assert state is None
    assert save == {"phone": "+919876543210", "linkedin_url": "https://www.linkedin.com/in/vishal",
                    "github_url": "https://github.com/VishalSharma7987", "portfolio_url": None, "location": "Pune"}
    assert "1/5" in replies[0] and "2/5" in replies[1] and "5/5" in replies[4] and "Confirm?" in replies[5]
    assert "Phone: +919876543210" in replies[5] and "Portfolio: (skipped)" in replies[5]


def test_invalid_answers_repeat_the_same_question_without_advancing():
    step = ob.start({})
    for bad, fragment in [("hello", "phone number")]:
        res = ob.advance(step.state, bad)
        assert res.state["step"] == "phone" and fragment in res.reply
    res = ob.advance(ob.advance(step.state, "+919876543210").state, "not a linkedin url")
    assert res.state["step"] == "linkedin" and "LinkedIn" in res.reply
    assert ob.advance(ob.advance(ob.advance(ob.advance(step.state, "+919876543210").state, "skip").state, "skip").state,
                      "skip").state["step"] == "location"


def test_phone_and_location_cannot_be_skipped():
    s = ob.start({}).state
    assert ob.advance(s, "skip").state["step"] == "phone"


def test_cancel_works_at_every_step_and_saves_nothing():
    answers = ["+919876543210", "skip", "skip", "skip", "Pune"]
    for n in range(0, 6):
        state, replies, save = run_flow(answers[:n] + ["cancel"])
        assert state is None and save is None and "cancelled" in replies[-1].lower()


def test_confirm_no_restarts_and_garbage_reasks():
    state, replies, save = run_flow(["+919876543210", "skip", "skip", "skip", "Pune", "maybe"])
    assert state["step"] == "confirm" and save is None and "yes or no" in replies[-1]
    res = ob.advance(state, "no")
    assert res.state["step"] == "phone" and res.save is None


def test_keep_uses_current_value_and_prefill_from_env():
    state, replies, save = run_flow(["keep", "keep", "keep", "skip", "Pune", "yes"],
                                    current={"phone": "+911111111111", "linkedin": "https://www.linkedin.com/in/x",
                                             "github": "https://github.com/fromenv"})
    assert "current: https://github.com/fromenv" in replies[2]  # github prompt shows the env prefill
    assert save["github_url"] == "https://github.com/fromenv" and save["phone"] == "+911111111111"
    # 'keep' with nothing to keep is validated as a normal (invalid) answer
    assert ob.advance(ob.start({}).state, "keep").state["step"] == "phone"


def test_corrupt_state_resets_safely():
    res = ob.advance({"step": "bogus"}, "x")
    assert res.state is None and "/setup" in res.reply


# ================================================================ through the Telegram pipeline (stateless steps)
def test_setup_via_updates_persists_state_between_runs(repo, settings, tg, store):
    process_updates([msg(1, "/setup steps")], repo, tg, settings, store)
    assert repo.get_profile_row()["onboarding_state"]["step"] == "phone" and "1/5" in tg.sent[-1]
    # every answer arrives in a *separate* worker run (new process = nothing in memory)
    for uid, text in enumerate(["+919876543210", "linkedin.com/in/vishal", "github.com/vishal", "skip", "Pune"], start=2):
        process_updates([msg(uid, text)], repo, tg, settings, store)
    assert repo.get_profile_row()["onboarding_state"]["step"] == "confirm" and "Confirm?" in tg.sent[-1]
    process_updates([msg(7, "yes")], repo, tg, settings, store)
    row = repo.get_profile_row()
    assert row["phone"] == "+919876543210" and row["linkedin_url"] == "https://www.linkedin.com/in/vishal"
    assert row["github_url"] == "https://github.com/vishal" and row["location"] == "Pune" and row["portfolio_url"] is None
    assert row["onboarding_state"] is None and "saved" in tg.sent[-1].lower()


def test_whole_conversation_in_one_run_is_processed_in_order(repo, settings, tg, store):
    # relay down / cron only: the user typed everything before the worker woke up. Order must be preserved.
    batch = [msg(i, t) for i, t in enumerate(["/setup steps", "+919876543210", "skip", "skip", "skip", "Pune", "yes"], start=1)]
    process_updates(list(reversed(batch)), repo, tg, settings, store)  # even delivered out of order
    assert repo.get_profile_row()["phone"] == "+919876543210" and repo.inbox_pending() == []


def test_plain_text_without_setup_gets_a_hint_and_changes_nothing(repo, settings, tg, store):
    process_updates([msg(1, "hello bot")], repo, tg, settings, store)
    assert len(tg.sent) == 1 and "/help" in tg.sent[0] and repo.get_profile_row() is None


def test_setup_cancel_command_and_word(repo, settings, tg, store):
    process_updates([msg(1, "/setup"), msg(2, "cancel")], repo, tg, settings, store)
    assert repo.get_profile_row()["onboarding_state"] is None and "cancelled" in tg.sent[-1].lower()
    process_updates([msg(3, "/setup"), msg(4, "/cancel")], repo, tg, settings, store)
    assert repo.get_profile_row()["onboarding_state"] is None
    process_updates([msg(5, "/cancel")], repo, tg, settings, store)
    assert "Nothing to cancel" in tg.sent[-1]


def test_other_commands_still_work_during_setup(repo, settings, tg, store):
    process_updates([msg(1, "/setup steps"), msg(2, "/status"), msg(3, "+919876543210")], repo, tg, settings, store)
    assert repo.get_profile_row()["onboarding_state"]["step"] == "linkedin"


def test_strangers_cannot_setup_or_upload(repo, settings, tg, store):
    tg.files["f1"] = PDF
    process_updates([msg(1, "/setup", chat="999"), msg(2, document=pdf_doc(), chat="999"), msg(3, "+919876543210", chat="999")],
                    repo, tg, settings, store)
    assert tg.sent == [] and store.download("default.pdf") is None and repo.get_profile_row() is None


# ================================================================ resume upload
def test_pdf_upload_saves_default_resume(repo, settings, tg, store):
    tg.files["f1"] = PDF
    process_updates([msg(1, document=pdf_doc())], repo, tg, settings, store)
    assert store.download("default.pdf") == PDF
    row = repo.get_profile_row()
    assert row["resume_path"] == "default.pdf" and row["resume_updated_at"]
    assert row["resume_variants"]["default"]["size"] == len(PDF)
    assert tg.sent[-1] == "✅ Resume saved (2 KB). Send /setup to update your details."


def test_upload_overwrites_previous_default(repo, settings, tg, store):
    tg.files["a"], tg.files["b"] = PDF, b"%PDF-1.7 second version"
    process_updates([msg(1, document=pdf_doc("a")), msg(2, document=pdf_doc("b", size=22))], repo, tg, settings, store)
    assert store.download("default.pdf") == b"%PDF-1.7 second version"


@pytest.mark.parametrize("caption,variant", [("ai", "ai"), ("AI", "ai"), ("fullstack", "fullstack"), ("Full-Stack", "fullstack"),
                                             (None, "default"), ("", "default")])
def test_caption_selects_variant(repo, settings, tg, store, caption, variant):
    tg.files["f1"] = PDF
    process_updates([msg(1, document=pdf_doc(), caption=caption)], repo, tg, settings, store)
    assert store.download(f"{variant}.pdf") == PDF
    row = repo.get_profile_row()
    assert variant in row["resume_variants"]
    if variant != "default":
        assert row["resume_path"] is None  # variants never replace the main resume pointer
        assert variant in tg.sent[-1]


def test_any_other_caption_saves_the_main_resume(repo, tg, store):
    tg.files["f1"] = PDF
    for caption in ("/resume", "my cv", "Resume 2026", ""):
        store.delete("default.pdf")
        assert "✅ Resume saved (2 KB)" in handle_document(msg(1, document=pdf_doc(), caption=caption)["message"], repo, tg, store)
        assert store.download("default.pdf") == PDF
    assert variant_from_caption("my cv") == "default" and variant_from_caption("/resume") == "default"
    assert variant_from_caption("/ai") == "ai" and variant_from_caption("/fullstack") == "fullstack"


def test_non_pdf_rejected(repo, tg, store):
    tg.files["f1"] = b"PK\x03\x04 zip"
    docx = pdf_doc(name="resume.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "not a PDF" in handle_document(msg(1, document=docx)["message"], repo, tg, store)
    # claims to be a PDF by name/mime but the bytes are not
    assert "not a valid PDF" in handle_document(msg(2, document=pdf_doc())["message"], repo, tg, store)
    assert store.download("default.pdf") is None and repo.get_profile_row() is None


def test_oversize_rejected_by_declared_size_and_by_real_size(repo, tg, store):
    big = b"%PDF" + b"0" * (5 * 1024 * 1024 + 1)
    tg.files["f1"] = big
    declared = handle_document(msg(1, document=pdf_doc(size=6 * 1024 * 1024))["message"], repo, tg, store)
    assert "limit is 5 MB" in declared
    tg.files["f1"] = big  # Telegram under-reported the size
    real = handle_document(msg(2, document=pdf_doc(size=100))["message"], repo, tg, store)
    assert "limit is 5 MB" in real and store.download("default.pdf") is None


def test_exactly_5mb_is_accepted(repo, tg, store):
    data = b"%PDF" + b"0" * (5 * 1024 * 1024 - 4)
    tg.files["f1"] = data
    assert "✅" in handle_document(msg(1, document=pdf_doc(size=len(data)))["message"], repo, tg, store)


def test_download_and_storage_failures_are_reported(repo, tg):
    assert "could not download" in handle_document(msg(1, document=pdf_doc("missing"))["message"], repo, tg, LocalResumeStore("data/x"))

    class Broken:
        def upload(self, *a):
            raise OSError("disk full")

    tg.files["f1"] = PDF
    assert "Saving the resume failed" in handle_document(msg(2, document=pdf_doc())["message"], repo, tg, Broken())  # type: ignore[arg-type]
    assert repo.get_profile_row() is None  # nothing recorded for a failed upload


def test_upload_never_logs_content_or_phone(repo, settings, tg, store, caplog):
    caplog.set_level(logging.DEBUG)
    tg.files["f1"] = PDF
    process_updates([msg(1, "/setup"), msg(2, "+919876543210"), msg(3, document=pdf_doc())], repo, tg, settings, store)
    text = caplog.text + " ".join(str(e) for e in repo.list_events(100))
    assert "9876543210" not in text and "xxxxxxxx" not in text and "%PDF" not in text


# ================================================================ /profile and /myresume
def test_profile_command_shows_saved_details_and_resume(repo, settings, tg, store):
    tg.files["f1"] = PDF
    process_updates([msg(1, "/setup steps"), msg(2, "+919876543210"), msg(3, "linkedin.com/in/vishal"), msg(4, "github.com/vishal"),
                     msg(5, "https://vishal.dev"), msg(6, "Pune"), msg(7, "yes"), msg(8, document=pdf_doc()),
                     msg(9, document=pdf_doc(), caption="ai"), msg(10, "/profile")], repo, tg, settings, store)
    out = tg.sent[-1]
    for expect in ("+919876543210", "https://www.linkedin.com/in/vishal", "https://github.com/vishal", "https://vishal.dev",
                   "Pune", "default: 2 KB", "ai: 2 KB"):
        assert expect in out


def test_profile_command_marks_env_fallbacks_and_missing(repo, settings):
    s = settings.model_copy(update={"candidate_phone": "+911234567890", "github_url": None})
    out = handle_command("/profile", repo, s).reply
    assert "Phone: +911234567890 (from env)" in out and "GitHub: not set" in out and "send me your resume" in out


def test_myresume_sends_the_stored_pdf_back(repo, settings, tg, store):
    tg.files["f1"] = PDF
    process_updates([msg(1, "/myresume")], repo, tg, settings, store)
    assert "No resume stored" in tg.sent[-1] and tg.documents == []
    process_updates([msg(2, document=pdf_doc()), msg(3, "/myresume")], repo, tg, settings, store)
    assert tg.documents == [("resume.pdf", len(PDF))]
    tg.files["f2"] = b"%PDF second"
    process_updates([msg(4, document=pdf_doc("f2", size=11), caption="ai"), msg(5, "/myresume ai"), msg(6, "/myresume nonsense")],
                    repo, tg, settings, store)
    assert tg.documents[-1] == ("resume_ai.pdf", 11) and "Usage" in tg.sent[-1]


def test_resume_command_keeps_its_automation_meaning(repo, settings):
    repo.set_state("paused", "1")
    assert "Resumed" in handle_command("/resume", repo, settings).reply and repo.get_state("paused") == "0"


# ================================================================ runtime fallback order: DB -> env -> warn
@pytest.fixture
def rt_settings(settings, tmp_path):
    return settings.model_copy(update={"resume_path": str(tmp_path / "work" / "resume.pdf"), "candidate_phone": None,
                                       "linkedin_url": None, "github_url": None, "portfolio_url": None, "fake_mode": False})


def test_personal_details_db_beats_env_and_env_fills_gaps(repo, rt_settings, store):
    s = rt_settings.model_copy(update={"candidate_phone": "+910000000000", "github_url": "https://github.com/envuser",
                                       "linkedin_url": "https://www.linkedin.com/in/env"})
    repo.update_profile_fields(phone="+919876543210", location="Pune")
    p = load_runtime_profile(s, repo, store).profile
    assert p.phone == "+919876543210"  # DB wins
    assert p.github == "https://github.com/envuser" and p.linkedin == "https://www.linkedin.com/in/env"  # env fallback
    assert p.current_location == "Pune" and p.portfolio == ""  # missing everywhere -> empty (forms ask the user)


def test_resume_comes_from_storage_first(repo, rt_settings, store):
    import base64

    s = rt_settings.model_copy(update={"resume_pdf_b64": base64.b64encode(b"%PDF env copy").decode()})
    store.upload("default.pdf", PDF)
    store.upload("ai.pdf", PDF + b"ai")
    repo.update_profile_fields(resume_path="default.pdf", resume_variants={"default": {}, "ai": {}})
    rt = load_runtime_profile(s, repo, store)
    from jobagent.profile import resume_path

    assert rt.resume_source == "storage" and resume_path(s).read_bytes() == PDF
    assert (resume_path(s).parent / "resume_ai.pdf").read_bytes() == PDF + b"ai" and set(rt.variants) == {"default", "ai"}


def test_resume_falls_back_to_env_when_storage_empty(repo, rt_settings, store):
    import base64

    s = rt_settings.model_copy(update={"resume_pdf_b64": base64.b64encode(b"%PDF env copy").decode()})
    rt = load_runtime_profile(s, repo, store)
    from jobagent.profile import resume_path

    assert rt.resume_source == "env" and resume_path(s).read_bytes() == b"%PDF env copy"


def test_storage_pointer_without_object_falls_through_to_env(repo, rt_settings, store):
    import base64

    s = rt_settings.model_copy(update={"resume_pdf_b64": base64.b64encode(b"%PDF env copy").decode()})
    repo.update_profile_fields(resume_path="default.pdf", resume_variants={"default": {}})  # DB says yes, bucket is empty
    assert load_runtime_profile(s, repo, store).resume_source == "env"


def test_invalid_env_resume_is_ignored(repo, rt_settings, store):
    s = rt_settings.model_copy(update={"resume_pdf_b64": "bm90IGEgcGRm"})  # "not a pdf"
    assert load_runtime_profile(s, repo, store).resume_source == "missing"


def test_missing_everywhere_is_reported_as_missing(repo, rt_settings, store):
    assert load_runtime_profile(rt_settings, repo, store).resume_source == "missing"


def test_existing_local_file_counts_for_local_dev(repo, rt_settings, store):
    from jobagent.profile import resume_path

    resume_path(rt_settings).parent.mkdir(parents=True, exist_ok=True)
    resume_path(rt_settings).write_bytes(PDF)
    assert load_runtime_profile(rt_settings, repo, store).resume_source == "local"


def test_main_warns_on_telegram_when_no_resume_and_skips_actions(rt_settings, repo, tg, store, monkeypatch):
    from helpers import FakeSMTP, make_ctx

    import jobagent.main as m

    seen = {}
    monkeypatch.setattr(m, "run_pipeline", lambda ctx: seen.setdefault("ctx", ctx))
    live = rt_settings.model_copy(update={"dry_run": False})
    m.run(live, "jobs", repo=repo, tg=tg, store=store)
    assert "⚠️ No resume configured. Send me your PDF." in tg.sent and seen["ctx"].profile.is_complete

    # ...and the act step really does nothing without a resume (discovery/matching/drafts already ran)
    from jobagent.pipeline.runner import run_pipeline as real_run
    from jobagent.profile import load_profile

    ctx = make_ctx(live, repo, load_profile(live), tg)
    assert not ctx.resume.exists()
    real_run(ctx)
    assert FakeSMTP.instances == [] and "no resume: email/apply steps skipped" in ctx.summary.notes
    assert repo.list_applications(50)  # drafts were still prepared


# ================================================================ storage backends + repo contract bits
def test_supabase_store_roundtrip_overwrite_and_missing():
    client = FakeClient()
    st = SupabaseResumeStore(client)
    assert st.download("default.pdf") is None
    st.upload("default.pdf", PDF)
    st.upload("default.pdf", b"%PDF v2")  # upsert
    assert st.download("default.pdf") == b"%PDF v2"
    st.delete("default.pdf")
    assert st.download("default.pdf") is None


def test_local_store_blocks_path_traversal(tmp_path):
    st = LocalResumeStore(tmp_path / "s")
    st.upload("../../evil.pdf", b"%PDF")
    assert (tmp_path / "s" / "evil.pdf").exists() and not (tmp_path / "evil.pdf").exists()


@pytest.fixture(params=["sqlite", "fake_supabase"])
def any_repo(request):
    return SqliteRepository(":memory:") if request.param == "sqlite" else SupabaseRepository("http://f", "k", client=FakeClient())


def test_profile_row_merge_semantics(any_repo):
    r = any_repo
    assert r.get_profile_row() is None
    r.update_profile_fields(phone="+919876543210")
    r.update_profile_fields(location="Pune", onboarding_state={"step": "phone", "answers": {}})
    r.save_profile({"skills": ["Python"]})  # the per-run facts sync must NOT wipe personal details
    row = r.get_profile_row()
    assert row["phone"] == "+919876543210" and row["location"] == "Pune"
    assert row["onboarding_state"]["step"] == "phone" and row["data"] == {"skills": ["Python"]}
    r.update_profile_fields(onboarding_state=None, resume_variants={"default": {"size": 1}})
    row = r.get_profile_row()
    assert row["onboarding_state"] is None and row["resume_variants"]["default"]["size"] == 1 and row["phone"]


def test_inbox_is_idempotent_ordered_and_pruned(any_repo):
    r = any_repo
    assert r.inbox_add(10, {"a": 1}) and r.inbox_add(5, {"b": 2}) and not r.inbox_add(10, {"a": "dup"})
    assert [x["update_id"] for x in r.inbox_pending()] == [5, 10] and r.inbox_pending()[1]["payload"] == {"a": 1}
    r.inbox_mark_done(5)
    assert [x["update_id"] for x in r.inbox_pending()] == [10]
    r.inbox_prune(14)  # recent rows survive
    assert not r.inbox_add(5, {"again": 1})


def test_sqlite_upgrade_adds_missing_profile_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("create table profile (key text primary key, data text not null, updated_at text)")  # pre-onboarding schema
    db.commit()
    db.close()
    r = SqliteRepository(path)
    r.update_profile_fields(phone="+919876543210")
    assert r.get_profile_row()["phone"] == "+919876543210"


def test_fake_telegram_is_default_for_these_tests(tg):
    assert isinstance(tg, FakeTelegram)
