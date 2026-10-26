"""Run-time guards added after the 30-minute GitHub timeout (docs/PERF_NOTES.md):
time budget, SIGTERM/SIGINT handling, report-always, LLM timeout + circuit breaker + model fallback,
per-source cap, batched stats writes and network timeouts."""

from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime, timedelta

import pytest
from helpers import CountingApply, make_ctx

from jobagent.budget import RunBudget, RunInterrupted, handle_signals, hard_deadline
from jobagent.contacts.finder import MAX_PAGES, ContactFinder
from jobagent.discovery.base import SourceAdapter
from jobagent.llm.client import LLMTimeout, OpenAICompatClient
from jobagent.llm.fake import FakeLLM
from jobagent.models import MatchResult, RawJob
from jobagent.pipeline import state as S
from jobagent.pipeline.runner import run_pipeline
from jobagent.telegram.client import TelegramClient
from jobagent.utils.http import HttpClient

GOOD = "Junior role, 0-1 years. Python, LangChain, RAG, LLM, FastAPI, React. Remote in India."


class ListAdapter(SourceAdapter):
    name = "list"

    def __init__(self, jobs, on_fetch=None):
        super().__init__()
        self.jobs, self.on_fetch = jobs, on_fetch

    def fetch(self):
        if self.on_fetch:
            self.on_fetch()
        return list(self.jobs)


def raw(i: int, source: str = "srcA", days_old: int = 0, title: str = "AI Developer") -> RawJob:
    return RawJob(company=f"Co{source}{i}", title=title, url=f"https://jobs.example/{source}/{i}", location="Remote - India",
                  remote=True, description=GOOD, source=source, posted_at=datetime.now(UTC) - timedelta(days=days_old))


class ExceedWhen(RunBudget):
    """Budget that runs out when a condition becomes true (deterministic stand-in for wall-clock time)."""

    def __init__(self, cond):
        super().__init__(1200)
        self.cond = cond

    def exceeded(self) -> bool:
        return bool(self.cond())


# ================================================================ time budget
def test_budget_stops_new_work_mid_ai_matching_and_still_reports(settings, repo, profile, tg):
    ctx = make_ctx(settings, repo, profile, tg)
    llm = ctx.llm
    ctx.budget = ExceedWhen(lambda: llm.calls >= 2)  # "time runs out" after two LLM calls
    s = run_pipeline(ctx)
    assert llm.calls == 2 and s.partial
    assert repo.list_jobs([S.DISCOVERED]), "unmatched jobs stay queued for the next run"
    report = next(m for m in tg.sent if m.startswith("Daily Job Report"))
    assert "⏱ partial run (time budget reached)" in report and "AI matching stopped early" in report
    assert repo.list_applications(50) == []  # contact/prepare/act were not started


def test_budget_exhausted_before_start_skips_every_stage_but_reports(settings, repo, profile, tg):
    ctx = make_ctx(settings, repo, profile, tg)
    ctx.budget = ExceedWhen(lambda: True)
    s = run_pipeline(ctx)
    assert s.partial and s.scanned == 0 and repo.list_jobs() == []
    assert any("⏱ partial run" in m for m in tg.sent)


def test_real_clock_budget(settings, repo, profile, tg):
    clock = {"t": 0.0}
    b = RunBudget(10, clock=lambda: clock["t"])
    assert not b.exceeded() and b.remaining() == 10
    clock["t"] = 10.5
    assert b.exceeded() and b.elapsed() == 10.5


# ================================================================ SIGTERM / SIGINT
def test_sigterm_sends_interrupted_message_and_partial_report(settings, repo, profile, tg):
    before = signal.getsignal(signal.SIGTERM)
    ctx = make_ctx(settings, repo, profile, tg)
    ctx.adapters = [ListAdapter([raw(1)], on_fetch=lambda: signal.raise_signal(signal.SIGTERM))]
    with handle_signals():
        with pytest.raises(RunInterrupted) as ei:
            run_pipeline(ctx)
    assert ei.value.signal_name == "SIGTERM" and ei.value.reported
    assert any(m.startswith("🛑 Run interrupted (SIGTERM)") for m in tg.sent)
    report = next(m for m in tg.sent if m.startswith("Daily Job Report"))
    assert "run interrupted (SIGTERM)" in report
    assert signal.getsignal(signal.SIGTERM) is before  # handlers restored afterwards


def test_second_signal_while_finishing_is_ignored(settings):
    with handle_signals():
        with pytest.raises(RunInterrupted):
            signal.raise_signal(signal.SIGTERM)
        signal.raise_signal(signal.SIGTERM)  # must not raise again (the report is being sent)


def test_signal_cannot_be_swallowed_by_per_item_error_handlers():
    assert issubclass(RunInterrupted, BaseException) and not issubclass(RunInterrupted, Exception)


def _block(seconds: float) -> None:
    for _ in range(int(seconds / 0.02)):  # short sleeps: an interrupt is delivered between them on every platform
        time.sleep(0.02)


def test_hard_deadline_interrupts_a_call_that_blocks_past_the_budget_and_still_reports(settings, repo, profile, tg):
    ctx = make_ctx(settings, repo, profile, tg)
    ctx.adapters = [ListAdapter([raw(1)], on_fetch=lambda: _block(10))]  # a source that never returns
    budget = RunBudget(0.1)
    ctx.budget = budget
    t0 = time.monotonic()
    with handle_signals(), hard_deadline(budget, grace=0.2):
        with pytest.raises(RunInterrupted) as ei:
            run_pipeline(ctx)
    assert time.monotonic() - t0 < 5, "the backstop must fire long before the blocked call would return"
    assert ei.value.reported
    assert any(m.startswith("Daily Job Report") for m in tg.sent)


def test_hard_deadline_does_not_interrupt_the_final_report():
    budget = RunBudget(0.05)
    budget.finishing = True  # finalize() has started
    with handle_signals(), hard_deadline(budget, grace=0.05):
        _block(0.4)  # would raise RunInterrupted if the backstop fired


def test_hard_deadline_is_cancelled_when_the_run_ends_in_time():
    budget = RunBudget(0.05)
    with handle_signals():
        with hard_deadline(budget, grace=0.1):
            pass
        _block(0.4)  # timer was cancelled on exit: no late signal


# ================================================================ report always
def test_crash_still_sends_report_with_exception_type(settings, repo, profile, tg, monkeypatch):
    import jobagent.pipeline.graph as G

    def boom(job):
        raise ValueError("bad regex")

    monkeypatch.setattr(G, "cheap_filter", boom)
    with pytest.raises(ValueError):
        run_pipeline(make_ctx(settings, repo, profile, tg))
    report = next(m for m in tg.sent if m.startswith("Daily Job Report"))
    assert "❌ run crashed: ValueError: bad regex" in report and "Jobs scanned: 20" in report


def test_report_falls_back_to_error_summary_if_db_is_down(settings, repo, profile, tg, monkeypatch):
    ctx = make_ctx(settings, repo, profile, tg)

    def down(*a, **k):
        raise ConnectionError("db down")

    for name in ("get_stats", "list_applications", "add_event", "bump_stats", "add_skip_reasons"):
        monkeypatch.setattr(repo, name, down)
    with pytest.raises(ConnectionError):  # the crash itself propagates (exit code 1)...
        run_pipeline(ctx)
    report = next(m for m in tg.sent if m.startswith("Daily Job Report"))  # ...but finalize never raises
    assert "❌ run crashed: ConnectionError: db down" in report


# ================================================================ LLM speed + timeout fallback
class _Resp:
    def __init__(self, content):
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


class SlowOpenAI:
    """Mimics OpenRouter keep-alive: the call just hangs (an httpx read timeout would never fire)."""

    def __init__(self, delay):
        self.delay, self.calls = delay, 0
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kw):
        self.calls += 1
        time.sleep(self.delay)
        return _Resp('{"decision": "QUALIFIED"}')


def test_llm_hard_wall_clock_timeout(caplog):
    caplog.set_level(logging.INFO)
    fake = SlowOpenAI(delay=3)
    llm = OpenAICompatClient("https://openrouter.ai/api/v1", "k", "m", max_retries=2, backoff=0, client=fake, timeout=0.3)
    t0 = time.monotonic()
    with pytest.raises(LLMTimeout):
        llm.complete_json("s", "u", MatchResult, max_tokens=800)
    assert time.monotonic() - t0 < 1.5 and fake.calls == 2  # 2 attempts x 0.3 s, never the full 3 s
    assert "LLM MatchResult attempt 1/2 model=m" in caplog.text and "timeout" in caplog.text


def test_llm_passes_max_tokens_and_logs_duration(caplog):
    caplog.set_level(logging.INFO)
    seen = {}

    class Rec(SlowOpenAI):
        def create(self, **kw):
            seen.update(kw)
            return _Resp('{"decision": "REJECTED"}')

    llm = OpenAICompatClient("https://x/v1", "k", "m", client=Rec(0), timeout=5)
    assert llm.complete_json("s", "u", MatchResult, temperature=0.0, max_tokens=800).decision == "REJECTED"
    assert seen["max_tokens"] == 800 and seen["temperature"] == 0.0
    assert "LLM MatchResult attempt 1/2 model=m" in caplog.text and " ok" in caplog.text


def test_unavailable_model_falls_back_and_is_reported(settings, repo, profile, tg):
    class NotFound(Exception):
        status_code = 404

    class Switch(SlowOpenAI):
        def create(self, **kw):
            if kw["model"] == "dead/model:free":
                raise NotFound("No endpoints found for dead/model:free")
            return _Resp('{"decision": "REJECTED", "reasons": ["x"]}')

    llm = OpenAICompatClient("https://x/v1", "k", "dead/model:free", client=Switch(0), backoff=0, timeout=5,
                             fallback_models=["good/model:free"])
    assert llm.complete_json("s", "u", MatchResult).decision == "REJECTED"
    assert llm.model == "good/model:free" and llm.switched_from == "dead/model:free"
    ctx = make_ctx(settings, repo, profile, tg, llm=llm)
    ctx.adapters = []
    run_pipeline(ctx)
    report = next(m for m in tg.sent if m.startswith("Daily Job Report"))
    assert "dead/model:free unavailable - used good/model:free (update LLM_MODEL)" in report


class _HttpErr(Exception):
    def __init__(self, status_code, msg="err"):
        super().__init__(msg)
        self.status_code = status_code


class ScriptedModels(SlowOpenAI):
    """Per-model behaviour: an int raises that HTTP status, a string is returned as the model's JSON reply."""

    def __init__(self, behaviour):
        super().__init__(0)
        self.behaviour, self.order = behaviour, []

    def create(self, **kw):
        self.order.append(kw["model"])
        b = self.behaviour[kw["model"]]
        if isinstance(b, int):
            raise _HttpErr(b, f"{kw['model']} is only available on agentic harnesses" if b == 403 else "x")
        return _Resp(b)


OK_JSON = '{"decision": "REJECTED", "reasons": ["x"]}'


def test_fallback_order_403_then_404_then_429_then_success():
    fake = ScriptedModels({"a:free": 403, "b:free": 404, "c:free": 429, "d:free": OK_JSON})
    llm = OpenAICompatClient("https://x/v1", "k", "a:free", client=fake, backoff=0, timeout=5,
                             fallback_models=["b:free", "c:free", "d:free"])
    assert llm.complete_json("s", "u", MatchResult).decision == "REJECTED"
    assert fake.order == ["a:free", "b:free", "c:free", "d:free"]  # strictly in the configured order, one try each
    assert llm.model == "d:free" and llm.switched_from == "a:free"
    assert "a:free" not in llm.fallback_models and "b:free" not in llm.fallback_models  # 403/404 are dropped for the run
    assert "c:free" in llm.fallback_models  # 429 is transient: it stays in rotation


def test_all_models_rate_limited_raises_quota_only_after_every_model_was_tried():
    from jobagent.llm.client import QuotaExceeded

    fake = ScriptedModels({"a:free": 429, "b:free": 429, "c:free": 429})
    llm = OpenAICompatClient("https://x/v1", "k", "a:free", client=fake, backoff=0, timeout=5,
                             fallback_models=["b:free", "c:free"])
    with pytest.raises(QuotaExceeded):
        llm.complete_json("s", "u", MatchResult)
    assert fake.order[:3] == ["a:free", "b:free", "c:free"]  # every model got a turn before giving up
    assert len(fake.order) <= 3 + 2  # then at most the normal retry of the last model, never a loop


def test_all_models_unavailable_raises_llm_error_not_quota():
    from jobagent.llm.client import LLMError, QuotaExceeded

    fake = ScriptedModels({"a:free": 403, "b:free": 404})
    llm = OpenAICompatClient("https://x/v1", "k", "a:free", client=fake, backoff=0, timeout=5, fallback_models=["b:free"])
    with pytest.raises(LLMError) as ei:
        llm.complete_json("s", "u", MatchResult)
    assert not isinstance(ei.value, QuotaExceeded) and fake.order == ["a:free", "b:free"]


def test_no_fallbacks_keeps_the_old_429_behaviour():
    from jobagent.llm.client import QuotaExceeded

    fake = ScriptedModels({"a:free": 429})
    llm = OpenAICompatClient("https://x/v1", "k", "a:free", client=fake, backoff=0, timeout=5)
    with pytest.raises(QuotaExceeded):
        llm.complete_json("s", "u", MatchResult)
    assert fake.order == ["a:free", "a:free"]  # 2 attempts on the only model


class TimeoutLLM:
    calls = 0

    def complete_json(self, *a, **k):
        TimeoutLLM.calls += 1
        raise LLMTimeout("LLM timed out on all 2 attempts (60s each)")


def test_llm_circuit_breaker_stops_after_three_failures_and_jobs_wait(settings, repo, profile, tg):
    TimeoutLLM.calls = 0
    s = run_pipeline(make_ctx(settings, repo, profile, tg, llm=TimeoutLLM()))
    assert TimeoutLLM.calls == 3  # not one call per candidate
    assert len(repo.list_jobs([S.DISCOVERED])) >= 5 and s.qualified == 0
    report = next(m for m in tg.sent if m.startswith("Daily Job Report"))
    assert "LLM unavailable (3 failures in a row" in report and "remaining jobs stay queued" in report


def test_drafts_use_the_template_when_the_llm_times_out(settings, repo, profile, tg):
    class MatchOnly(FakeLLM):
        def complete_json(self, system, user, schema, temperature=0.0, max_tokens=None):
            if schema is MatchResult:
                return super().complete_json(system, user, schema, temperature, max_tokens)
            raise LLMTimeout("email draft timed out")

    ctx = make_ctx(settings, repo, profile, tg, llm=MatchOnly(), apply_fn=CountingApply("DRY_RUN_STOPPED"))
    run_pipeline(ctx)
    drafts = [e for e in repo.list_events(500) if e["action"] == "draft_prepared"]
    assert drafts and all(e["detail"]["how"] == "template" for e in drafts)


# ================================================================ per-source cap + cheap filter first
def test_per_source_cap_keeps_newest_and_counts_the_rest(settings, repo, profile, tg):
    s3 = settings.model_copy(update={"max_jobs_per_source": 3})
    ctx = make_ctx(s3, repo, profile, tg)
    jobs = [raw(i, "srcA", days_old=i) for i in range(10)] + [raw(i, "srcB", days_old=i) for i in range(2)]
    ctx.adapters = [ListAdapter(jobs)]
    run_pipeline(ctx)
    stored = repo.list_jobs(limit=100)
    by_src = {}
    for r in stored:
        by_src.setdefault(r["source"], []).append(r["company"])
    assert sorted(by_src["srcA"]) == ["CosrcA0", "CosrcA1", "CosrcA2"] and len(by_src["srcB"]) == 2  # newest 3 kept
    assert repo.get_stats(ctx.day)["skip_reasons"]["source_cap"] == 7


def test_cheap_filter_runs_before_any_llm_call(settings, repo, profile, tg):
    ctx = make_ctx(settings, repo, profile, tg)
    ctx.adapters = [ListAdapter([raw(1, title="Senior Staff Engineer"), raw(2, title="Account Executive"),
                                 raw(3, title="AI Developer")])]
    run_pipeline(ctx)
    assert ctx.llm.calls == 1  # only the job that passed the cheap filter reached the LLM


def test_title_rejects_never_pay_for_html_sanitising(settings, repo, profile, tg, monkeypatch):
    import jobagent.pipeline.graph as G

    seen = []
    real = G.normalize
    monkeypatch.setattr(G, "normalize", lambda r: seen.append(r.title) or real(r))
    ctx = make_ctx(settings, repo, profile, tg)
    ctx.adapters = [ListAdapter([raw(1, title="Account Executive"), raw(2, title="AI Developer")])]
    run_pipeline(ctx)
    assert seen == ["AI Developer"]


class MatchCounter(FakeLLM):
    def __init__(self):
        super().__init__()
        self.match_calls = 0

    def complete_json(self, system, user, schema, temperature=0.0, max_tokens=None):
        if schema is MatchResult:
            self.match_calls += 1
        return super().complete_json(system, user, schema, temperature, max_tokens)


def test_second_run_hits_the_match_cache(settings, repo, profile, tg):
    ctx1 = make_ctx(settings, repo, profile, tg, llm=MatchCounter())
    run_pipeline(ctx1)
    matched = {r["id"] for r in repo.list_jobs(limit=500) if r["match_json"]}
    assert matched and ctx1.llm.match_calls == len(matched)
    # a job whose match is cached but whose status was never advanced (e.g. crash) costs no LLM call either
    some = next(iter(matched))
    repo.update_job(some, status=S.DISCOVERED)
    ctx2 = make_ctx(settings, repo, profile, tg, llm=MatchCounter())
    run_pipeline(ctx2)
    assert ctx2.llm.match_calls == 0 and ctx2.metrics["ai_match"].get("cached") == 1


# ================================================================ batched stats writes
def test_stats_are_written_once_per_stage_not_per_job(settings, repo, profile, tg, monkeypatch):
    calls = {"single": 0, "batch": 0}
    for name in ("bump_stat", "add_skip_reason"):
        real = getattr(repo, name)
        monkeypatch.setattr(repo, name, lambda *a, _r=real, **k: (calls.__setitem__("single", calls["single"] + 1), _r(*a, **k))[1])
    for name in ("bump_stats", "add_skip_reasons"):
        real = getattr(repo, name)
        monkeypatch.setattr(repo, name, lambda *a, _r=real, **k: (calls.__setitem__("batch", calls["batch"] + 1), _r(*a, **k))[1])
    ctx = make_ctx(settings, repo, profile, tg)
    ctx.adapters = [ListAdapter([raw(i, title="Account Executive") for i in range(300)] + [raw(999)])]
    run_pipeline(ctx)
    assert calls["single"] == 0 and calls["batch"] <= 2 * 9  # <= 2 writes per stage, independent of 301 jobs
    st = repo.get_stats(ctx.day)
    assert st["scanned"] == 301 and st["skip_reasons"]["title_seniority_or_unrelated"] == 300


def test_stage_logs_have_elapsed_and_counts(settings, repo, profile, tg, caplog):
    caplog.set_level(logging.INFO)
    run_pipeline(make_ctx(settings, repo, profile, tg))
    text = caplog.text
    for line in ("stage discover: done", "discovered=20", "stage cheap_filter: done", "candidates=",
                 "stage ai_match: done", "llm_calls=", "stage contact: done", "stage prepare: done", "drafts="):
        assert line in text
    assert "AI match 1/" in text and "source fake: 20 jobs in" in text


# ================================================================ network limits
def test_http_timeouts_are_at_most_20s():
    t = HttpClient()._client.timeout
    assert t.connect <= 10 and t.read <= 20 and t.write <= 20 and t.pool <= 20
    assert HttpClient(timeout=120)._client.timeout.read == 20  # capped even if asked for more
    tt = TelegramClient("1:x", "1")._c.timeout
    assert tt.connect <= 10 and tt.read <= 20


def test_contact_finder_limits():
    f = ContactFinder()
    assert MAX_PAGES == 3 and f.http._client.timeout.read <= 15 and f.http.retries == 0


def test_contact_finder_visits_a_company_site_once_per_run():
    from conftest import make_job

    from jobagent.contacts.robots import RobotsChecker

    class Allow(RobotsChecker):
        def __init__(self):
            pass

        def allowed(self, url):
            return True

    fetched = []
    pages = {"https://acme.com": '<a href="/careers">Careers</a>', "https://acme.com/careers": "<p>jobs@acme.com</p>"}
    f = ContactFinder(fetcher=lambda u: fetched.append(u) or pages.get(u, "<html></html>"), robots=Allow())
    for i in range(5):
        c = f.find(make_job(url=f"https://acme.com/job/{i}", company="Acme", company_website="https://acme.com"))
        assert c and c.email == "jobs@acme.com" and c.confidence == "MEDIUM"
    assert fetched.count("https://acme.com") == 1 and fetched.count("https://acme.com/careers") == 1


def test_slow_source_is_cut_off_by_its_deadline():
    class Boards(SourceAdapter):
        name = "boards"

        def fetch(self):
            got = []
            for i in range(100):
                if self.expired():
                    break
                time.sleep(0.02)
                got.append(i)
            return got

    b = Boards()
    b.begin(0.1)
    assert 0 < len(b.fetch()) < 20
    b.begin(None)
    assert not b.expired()


def test_failing_source_is_skipped(settings, repo, profile, tg):
    class Broken(SourceAdapter):
        name = "broken"

        def fetch(self):
            raise ConnectionError("source down")

    ctx = make_ctx(settings, repo, profile, tg)
    ctx.adapters = [Broken(), ListAdapter([raw(1)])]
    s = run_pipeline(ctx)
    assert s.scanned == 1 and any(e["action"] == "source_failed" for e in repo.list_events(50))


# ================================================================ startup line + --debug-hang
def test_first_log_line_is_printed_before_any_work_and_hang_dumps_are_opt_in(monkeypatch, caplog, settings):
    import faulthandler

    import jobagent.main as M

    dumps = []
    monkeypatch.setattr(M, "setup_logging", lambda *_a, **_k: None)  # keep pytest's caplog handler on the root logger
    monkeypatch.setattr(M, "get_settings", lambda: settings)
    monkeypatch.setattr(M, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop after the first line")))
    monkeypatch.setattr(M, "_safe_send", lambda *_a, **_k: None)
    monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda *a, **k: dumps.append((a, k)))
    monkeypatch.delenv("DEBUG_HANG", raising=False)

    with caplog.at_level(logging.INFO, logger="jobagent"):
        assert M.main(["run", "--mode", "full"]) == 1
    first = next(r.getMessage() for r in caplog.records if r.name == "jobagent")
    assert first == f"run started, mode=full, budget={settings.run_time_budget_seconds}s"
    assert dumps == []  # off by default

    assert M.main(["run", "--mode", "full", "--debug-hang"]) == 1
    assert dumps and dumps[0][0][0] == M.HANG_DUMP_SECONDS == 120 and dumps[0][1]["repeat"] is True
