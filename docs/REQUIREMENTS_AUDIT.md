# Requirements audit

Audit of the code against `docs/REQUIREMENTS.md` (the real requirements doc). Status: ✅ implemented · 🔧 gap found → fixed in this pass ·
➖ intentionally different (reason given; see `DECISIONS.md`). Every ✅/🔧 row names the file and the test that proves it.

## §5 End-to-end flow
| Requirement | Status | Where |
|---|---|---|
| Telegram command **or** schedule starts it | ✅ | `main.py` (commands first), `.github/workflows/agent.yml` (cron + `repository_dispatch`) · `test_pipeline_dry_run.py::test_main_processes_relay_payload_before_running` |
| Cloud control layer receives the task | ➖ | Cloudflare Worker is a relay only (`cloudflare/worker.js`); state lives in Supabase, not D1 (D-020) |
| Ephemeral GitHub Actions worker | ✅ | `agent.yml` |
| Search several sources, pool of 50–100 | ✅ | `discovery/*` (8 adapters, ~40 verified company boards in `config/companies.yaml`) · `test_normalize.py` |
| Normalize + dedupe | ✅ | `discovery/normalize.py`, `dedupe.py`, `utils/hashing.py` · `test_normalize.py`, `test_dedupe.py` |
| Evaluate experience/skills/role/location; only suitable jobs move on | ✅ | `matching/cheap_filter.py`, `ai_match.py` |
| **Check whether role/company already contacted or applied to** | 🔧 | was only `job_key` (same URL). Now also same company+role under another URL (`already_contacted`) and a 14-day cool-down per recruiting address: `pipeline/graph.py::_history` · `test_audit_features.py::test_same_company_and_role_is_never_applied_twice`, `…::test_same_recruiting_address_not_emailed_twice_within_14_days`; known jobs on re-runs count as `duplicate` |
| Public recruiting email, never guessed | ✅ | `contacts/finder.py` · `test_contact_finder.py` (name-pattern addresses rejected, provenance asserted) |
| **Select the appropriate resume/profile version** | 🔧 | `profile.py::select_resume` (`resume_ai.pdf` / `resume_fullstack.pdf` next to `resume.pdf`, else default); stored in `applications.resume_version`; used by SMTP + Playwright · `test_audit_features.py::test_resume_variant_selection`, `…::test_email_goes_out_with_the_selected_resume` |
| Personalised message from verified facts only | ✅ | `email/generator.py` guard + template · `test_email_guard.py` |
| Gmail sends when a public address exists | ✅ | `email/gmail_smtp.py` |
| Playwright where allowed | ✅ | `apply/` |
| CAPTCHA/OTP/legal/ambiguous → pause + Telegram | ✅ | `apply/detectors.py`, `field_mapper.py`, `pipeline/graph.py::_wait_user` |
| Save status, URL, company, role, contact, timestamp, result | ✅ | `applications`, `contacts`, `events` tables |
| Daily summary + exceptions on Telegram | ✅ | `telegram/reports.py`, `graph.py::_fail(notify=True)` |

## §8 Smart job matching
| Factor | Status | Where |
|---|---|---|
| Title relevance, experience vs actual, AI/ML/RAG/agents, full-stack, technologies | ✅ | `cheap_filter.py` + `MatchResult` (`must_have_skills`, `matched_skills`, `required_years_*`) |
| **Education requirements, salary, employment type** | 🔧 | new `MatchResult.education_required / salary / employment_type` (`models.py`, prompt in `llm/prompts.py`); internships/contract/part-time score lower |
| Location / remote | ✅ | `cheap_filter.location_ok`, `MatchResult.location_ok` |
| **Company legitimacy / source quality** | 🔧 | `scoring.py`: official company boards (Greenhouse/Lever/Ashby/career pages) score above aggregators · `test_audit_features.py::test_score_prefers_official_boards_and_full_time` |
| Application route availability | ✅ | `graph.py::contact_node` (email → ATS form → `manual_apply`, counted as "no suitable application route") |
| Previous application/contact history | 🔧 | see §5 |
| Explainable, stored reasons (e.g. "RAG + LangChain + 0–2 years match") | ✅ | `jobs.match_reasons`, `match_json`, score breakdown (`scoring.py`) · `test_pipeline_dry_run.py::test_reasons_scores_and_match_cache_are_stored` |

## §10 HR / recruiter contact discovery
| Requirement | Status | Where |
|---|---|---|
| Search official company pages + job listings | ✅ | `contacts/finder.py` (job page, homepage, careers, contact; max 4 pages; robots.txt) |
| Prefer careers@/jobs@/recruitment@ | ✅ | `finder.py::ROLES` allow-list |
| Store source URL + confidence | ✅ | `contacts.source_url`, `contacts.confidence` (HIGH/MEDIUM used, LOW ignored) |
| **`verified_at`** | 🔧 | set when the address is seen on the page (`save_contact`, migration `002`) |
| Never generate/guess an address | ✅ | no code path builds addresses; `test_contact_finder.py::test_finder_source_has_no_address_construction` |
| No private contact data | ✅ | personal-name local parts and free-mail domains rejected |

## §11 Personalised email
| Requirement | Status | Where |
|---|---|---|
| Built from the job + verified profile only | ✅ | `generator.py` + `Profile.verified_terms()` (now with skill variants, e.g. React.js ⇒ React) |
| Subject `Application – {Role} | Vishal Sharma` | ✅ | `generator.subject_for` |
| Opening, 2–3 lines tying role to hands-on work, resume attached, closing | ✅ | prompt + template; with no named projects the template cites `project_areas` from the doc |
| Portfolio/project links only when configured | ✅ | env `PORTFOLIO_URL` / `CANDIDATE_GITHUB_URL` / `LINKEDIN_URL`; `test_no_links_when_not_configured` |
| No spam/length/exaggeration | ✅ | ≤ 180 words check, unverified-tech guard, regenerate once → template |

## §12 Browser application automation
| Requirement | Status | Where |
|---|---|---|
| Playwright, public forms | ✅ | `apply/playwright_runner.py`, `strategies/` (Greenhouse, Lever, Ashby; generic experimental/off) |
| Map name, email, phone, resume, **experience** | ✅ | `field_mapper.py`. Experience/"years of X" questions go through the profile-only LLM path (the doc gives a target range, not a number, so it is never hard-coded); < 0.8 confidence ⇒ ask you |
| Profile as single source of truth | ✅ | `field_mapper.decide` |
| Screenshots/logs on failure | ✅ | `artifacts/{job_key}/` (`failure.png/html`, `needs_user.png`, …) |
| Safe retries with limits | ✅ | timeouts retried ≤ 2; a clicked submit is never retried (D-009) |
| Stop on CAPTCHA, OTP, legal, **identity verification**, ambiguity | 🔧 | identity-verification detector added (`detectors.detect_identity`) · `test_audit_features.py::test_identity_verification_detected` |
| Telegram notification for human intervention | ✅ | `graph.py::_wait_user` (reason, company, role, url, screenshot, `/approve`, `/skip`) |
| Never bypass anti-bot | ✅ | `detect_blocked`, robots.txt check on the form URL |

## §13 Human-in-the-loop
| Trigger | Status | Where |
|---|---|---|
| CAPTCHA / OTP | ✅ | `detectors.py` · `test_apply_detectors_mapper.py`, `test_playwright_forms.py::test_captcha_stops_safely` |
| Legal / work-authorization unless configured | ✅ | `legal_prefs` in `profile.yaml` (`work_authorization_india: true` pre-approved) |
| Salary / consequential declaration | ✅ | never answered by the LLM, ask-user · `test_consequential_questions_wait_for_user` |
| Ambiguous question | ✅ | confidence < 0.8 or answer not in options ⇒ ask |
| **Unexpected website behaviour → save state and report** | 🔧 | any exception in the browser step (runner *and* graph) now saves artifacts, marks the task failed, and notifies Telegram with a screenshot instead of crashing the run · `test_audit_features.py::test_unexpected_browser_exception_is_reported_not_fatal` |

## §14 Application memory / database
| Table | Status | Where |
|---|---|---|
| jobs (company, title, URL, source, requirements, discovered_at, match_reason) | ✅ | `001_init.sql` |
| contacts (company, email, source_url, confidence, verified_at) | ✅/🔧 | `001`, `002` |
| applications (…resume_version, notes) | ✅ | `001` (`UNIQUE(job_id, route)`) |
| tasks (queued/running/waiting_user/completed/failed, retry_count) | ✅ | `001` (check constraint verified on the live DB) |
| **profile** (skills, experience, projects, education, preferences) | 🔧 | new table `profile` (migration `002`, applied to the live project); `main.run` stores the non-secret profile each run (no phone/email) · `test_audit_features.py::test_run_stores_verified_profile_without_pii` |
| events / audit log | ✅/🔧 | now also records `job_qualified`, `job_rejected`, `contact_found` (with source), `draft_prepared`, `email_sent`, `application_submitted`, `would_send_email`, `quota_exceeded`, `action_failed`, `run_finished` · `test_audit_trail_records_key_decisions` |
| Repository really works on Postgres | ✅ | `test_repository_contract.py` (sqlite + SupabaseRepository over an in-memory PostgREST fake); `test_supabase_integration.py` runs the same contract on the **real** project when `SUPABASE_URL`/`SUPABASE_KEY` are set |

## §15 Statuses & idempotency
✅ `pipeline/state.py` (guarded transitions), `UNIQUE(job_id, route)`, `SENDING` marker, `email_sent_at` checks · `test_idempotency.py` (two live runs ⇒ 0 duplicates; SMTP failure → `/retry` ⇒ exactly one send; crash between send and record ⇒ no resend; kill switch).

## §17 Telegram commands
| Command | Status | Note |
|---|---|---|
| /jobs /apply /status /report /pause /resume /retry /approve /skip /history | ✅ | `telegram/commands.py` · `test_telegram_commands.py` |
| **/settings – "configured preferences"** | 🔧 | now also shows target roles, preferred locations, experience target and daily target from the profile |
| /start /help /killswitch (from the brief) | ✅ | |
| Only the allowed chat is served; > 4000-char messages split | ✅ | |

## §19 Security & reliability
| Requirement | Status | Where |
|---|---|---|
| No tokens/secrets in git | ✅ | `.gitignore`, `.env.example`; `git ls-files` contains no `.env`, `.mcp.json`, resume, `artifacts/` |
| Actions Secrets/Variables | ✅ | `agent.yml` |
| Least-privilege auth | ✅ | `permissions: contents: read`; Gmail **app password** limited to SMTP send (no OAuth scopes needed); service key only in Secrets |
| Candidate data private | ✅/🔧 | prompts never contain phone/email; failure artifacts: filled-form screenshots excluded from uploads, retention 1 day, private repo recommended (D-021) |
| Resume not public | ✅ | secret `RESUME_PDF_B64`, written at runtime, git-ignored |
| Sanitise/untrusted input | ✅ | `llm/sanitize.py`, delimiters in `llm/prompts.py` · `test_llm_and_sanitize.py`, `test_pipeline_dry_run.py::test_prompt_injection_text_never_reaches_llm_or_email` |
| Timeouts, retries, rate limits | ✅ | `utils/http.py` (per-host throttle), `llm/client.py` backoff, Playwright timeouts |
| Audit trail | ✅/🔧 | see §14 |
| No repeated emails on retry | ✅ | §15 |
| Kill switch | ✅ | `/killswitch` (blocks every action; `test_killswitch_blocks_all_actions`) |

## §22 Zero-budget principle
| Requirement | Status | Where |
|---|---|---|
| No paid VPS / server / OpenAI / automation platform / database | ✅ | GitHub Actions + Supabase Free + OpenRouter `:free` + Gmail SMTP + Telegram |
| Laptop not required | ✅ | cron + `repository_dispatch` |
| Documented limits, paid items optional | ✅ | `docs/FREE_TIER_REPORT.md` (limits re-verified against the vendors' pages on 2026-10-05) |
| **Report quota/limit instead of failing silently** | ✅ | `QuotaExceeded` → `⚠️ Free-tier limit reached: <provider> — <details>` on Telegram + in the report · `test_quota_exceeded_is_reported_not_crashed` |

## §27 MVP definition of done
| Item | Status |
|---|---|
| Telegram command or schedule | ✅ |
| Runs with laptop off | ✅ (design; first remote run is a manual step) |
| Discovers a meaningful set of relevant jobs | ✅ (live APIs probed; a real-profile run against the live APIs reached the LLM stage — see README) |
| Filters on my real profile | ✅ (`profile.yaml` from §2) |
| Avoids duplicates | ✅/🔧 |
| Only public recruitment contacts | ✅ |
| Personalised Gmail with resume attached | ✅ (`test_live_send_attaches_resume`) |
| Tracks every application | ✅ |
| Browser applications via Playwright | ✅ (fixtures; live ATS forms are a manual check) |
| Pauses safely for CAPTCHA/OTP/legal/ambiguous | ✅ |
| Useful Telegram report in the §18 format | 🔧 `reports.py` now matches §18 line for line · `test_report_format.py::test_report_matches_section_18_exactly` |
| Within free-tier constraints | ✅ |

## Not implemented on purpose
- **§6/§7 Cloudflare D1 + Worker as control layer** → Supabase Postgres + a relay-only Worker (D-020).
- **§9 Wellfound / Indeed / Internshala / Naukri / LinkedIn scraping** → these sites restrict automated access (ToS/robots/anti-bot), which the doc itself forbids bypassing. Official ATS APIs, public aggregators and a JSON-LD career-page adapter are used instead (D-022).
- **§24 email auto-reply** → explicitly post-MVP.
