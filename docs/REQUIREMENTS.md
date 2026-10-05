AI JOB SEARCH & APPLICATION AGENT
Complete Product Requirements, Architecture & Implementation Brief
Zero-Budget / Free-Tier / Cloud-Hosted Approach • Target: 10–20 Quality Applications per Day

## 1. Executive Summary
I want to build a personal AI-powered job search and application agent for my own job search. The system should search for relevant jobs, understand the job requirements, compare them with my verified profile and resume, filter out poor matches, find legitimate public recruitment contacts when available, personalize applications, send application emails through my Gmail, submit suitable online applications where technically possible, track every application, prevent duplicates, and report everything through Telegram.
The target is not mass spam. A successful daily target is approximately 10–20 high-quality applications. The system should search a larger pool (for example 50–100 jobs) and intelligently reduce it to the best relevant opportunities.
The most important constraint is budget: I currently have no money for paid APIs, paid hosting, a VPS, paid automation platforms, or always-on hardware. My laptop cannot remain powered on 24/7. Therefore, the architecture must prioritize free tiers, open-source software, serverless services, scheduled GitHub Actions, and hosted services that do not require me to maintain a server.

## 2. User Profile / Job Target
The agent must use a verified candidate profile and never invent qualifications.
- Candidate: Vishal Sharma
- Target roles: AI Developer, AI/ML Developer, AI Engineer, Full Stack AI Developer, Agentic AI Developer, Full Stack Developer and closely related junior roles.
- Experience target: approximately 0–1 year / junior / entry-level / early-career opportunities, while allowing the agent to consider roles whose stated requirements reasonably match the candidate's actual experience.
- Relevant skills: Python, JavaScript, React.js, Next.js, Node.js, Express, REST APIs, RAG, LLMs, AI Agents, LangChain, LlamaIndex, embeddings, vector databases/Pinecone, Voice AI/Twilio, n8n, WhatsApp APIs, Google Calendar API, MCP, Playwright, OpenAI/OpenRouter/Groq APIs, Supabase, Git/GitHub and related full-stack technologies.
- Relevant project areas: RAG applications, AI agents, voice AI, automation workflows, WhatsApp appointment automation, browser automation and full-stack AI applications.
- Preferred locations: remote where available, plus suitable opportunities in India such as Pune, Bangalore and Hyderabad.
- Daily application target: 10–20 quality applications, not hundreds of low-quality submissions.

## 3. Core Problem
- Manually searching dozens of job boards every day is repetitive and time-consuming.
- Many jobs are irrelevant because of experience, technology, seniority or role mismatch.
- Finding a legitimate HR/recruitment email is repetitive.
- Generic applications have low personalization and can look automated.
- It is easy to apply to the same company/role multiple times.
- Browser applications can contain different questions and workflows.
- Some steps require CAPTCHA, OTP, legal declarations or other human actions.
- I cannot run a personal server or laptop continuously.

## 4. Main Product Goal
Build a reliable personal job-application agent that behaves like a smart assistant rather than a blind automation script.
The agent should make decisions from structured evidence: job description, company information, application URL, candidate profile, verified project history, previous application history and available public recruitment contact information.

## 5. Desired End-to-End Flow
- Telegram command or scheduled trigger starts the workflow.
- Cloud/serverless control layer receives the task.
- GitHub Actions starts an ephemeral worker when heavier processing/browser automation is required.
- Worker searches multiple legitimate job sources and company career pages.
- System collects a larger candidate pool, such as 50–100 jobs.
- Jobs are normalized and deduplicated.
- AI/logic evaluates experience, skills, role relevance, location and other configured criteria.
- Only suitable jobs move forward.
- System checks whether the role/company has already been contacted or applied to.
- System finds a public recruitment/HR/careers email when available. It must never guess an email address.
- System selects the appropriate resume/profile version.
- AI generates a concise personalized application message using verified facts only.
- Gmail sends the email when a legitimate public recruitment address is available.
- Where an online application is available and automation is permitted, Playwright can fill the form.
- If CAPTCHA, OTP, legal declarations or ambiguous high-impact questions appear, the system pauses and requests human action through Telegram.
- Application status, URL, company, role, contact, timestamp and result are saved.
- Telegram receives a daily summary and exceptions.

## 6. Proposed Architecture
Recommended architecture: Telegram + Cloudflare Worker/D1 + GitHub Actions + Gmail/Google Apps Script or Gmail API + Playwright + LangGraph/LangChain + a suitable free AI model/provider.
- Telegram Bot: user control interface, commands, alerts and reports.
- Cloudflare Worker: lightweight API/webhook/control layer; no VPS.
- Cloudflare D1: application/job/contact/task database.
- GitHub Actions: scheduled and on-demand ephemeral worker for heavier jobs, scraping where permitted, parsing and Playwright workflows. It avoids keeping a laptop online.
- Playwright: browser automation for permitted application flows. It must not bypass CAPTCHA, anti-bot controls or access restrictions.
- LangGraph/LangChain: orchestration and structured agent workflow.
- AI model layer: use a free/available model where possible. Keep the model provider replaceable so the architecture is not locked to one vendor.
- Gmail: send personalized application emails through the user's authorized account.
- Telegram: human-in-the-loop approvals, CAPTCHA/OTP notifications and daily reports.
- Open-source GitHub repositories: evaluate and reuse suitable components rather than depending blindly on one repository.

## 7. Architecture Diagram
Telegram → Cloudflare Worker → D1 Database → GitHub Actions Worker → Job Sources → Smart Filtering → Contact Discovery → Resume/Profile → Personalized Email / Playwright Application → D1 Tracking → Telegram Report
The laptop should be optional, not required. Local Ollama/Hermes can be used later when the laptop is available, but the core daily workflow must not depend on a 24/7 laptop.

## 8. Smart Job Matching
The system should not simply search for keywords. It should understand the role and compare the requirements with verified candidate data.
- Role/title relevance.
- Required experience versus actual experience.
- AI/ML/RAG/agent relevance.
- Full-stack relevance.
- Required technologies.
- Education requirements where stated.
- Location/remote preference.
- Salary information when available.
- Employment type.
- Company legitimacy and source quality.
- Application route availability.
- Previous application/contact history.
An internal matching score may be used to prioritize work, but it should be explainable. The system should store reasons such as 'RAG + LangChain + 0–2 years match' rather than producing an unexplained number.

## 9. Job Discovery Sources
Prioritize official company career pages and legitimate public job platforms. Potential sources can include Greenhouse, Lever, Ashby, Wellfound, Indeed, Internshala, Naukri, LinkedIn public job information and other sources that permit the required access.
The implementation must respect each site's terms, robots/access restrictions and anti-bot controls. Do not bypass CAPTCHA, authentication barriers or technical restrictions.

## 10. HR / Recruiter Contact Discovery
- Search official company pages and job listings for publicly listed recruitment contacts.
- Prefer careers@, jobs@, recruitment@ or other explicitly published recruitment addresses.
- Store the source URL where the contact was found.
- Store a confidence/source field.
- Never generate or guess a personal HR email from a name or email pattern.
- Do not collect private contact information that is not publicly intended for recruitment.

## 11. Personalized Email Generation
Each email should be generated from the actual job description and verified candidate profile. The agent must not claim experience that is not present in the candidate profile.
Example structure:
- Subject: Application – AI Developer | Vishal Sharma
- Short opening referencing the specific role.
- 2–3 lines connecting the role to relevant hands-on skills/projects.
- Resume attachment.
- Professional closing.
- Optional portfolio/project links only when configured by the user.
The system should avoid spammy language, excessive length, fake personalization and exaggerated claims.

## 12. Browser Application Automation
- Use Playwright for permitted public application forms.
- Map common fields such as name, email, phone, resume and experience.
- Use the candidate profile as the single source of truth.
- Handle common form variations with structured field detection.
- Take screenshots/logs when an application fails.
- Retry safe transient failures with limits.
- Stop when CAPTCHA, OTP, legal declaration, identity verification or ambiguous questions appear.
- Send a Telegram notification for human intervention.
- Never bypass anti-bot systems.

## 13. Human-in-the-Loop
Automation should stop safely instead of attempting to bypass sensitive or uncertain steps.
- CAPTCHA detected → pause and notify user.
- OTP required → pause and notify user.
- Legal/work-authorization declaration → ask user if not already explicitly configured.
- Salary commitment or other consequential declaration → ask user.
- Ambiguous application question → ask user.
- Unexpected website behavior → save state and report.

## 14. Application Memory / Database
The database should maintain a permanent history so the agent becomes more useful over time.
- jobs: company, title, URL, source, requirements, discovered_at, match_reason.
- contacts: company, email, source_url, confidence, verified_at.
- applications: job_id, company, role, status, email_sent_at, submitted_at, application_url, resume_version, notes.
- tasks: queued, running, waiting_user, completed, failed, retry_count.
- profile: verified skills, experience, projects, education, preferences.
- events/audit log: important agent actions, errors and decisions.

## 15. Suggested Application Statuses
DISCOVERED → QUALIFIED → CONTACT_FOUND → READY → EMAIL_SENT → APPLICATION_STARTED → SUBMITTED → WAITING_USER → FAILED → REJECTED → INTERVIEW
Statuses should be idempotent so a retry does not accidentally send the same email or submit the same application twice.

## 16. Daily Operating Target
- Search: approximately 50–100 opportunities.
- Filter: reduce to relevant roles.
- Final applications: approximately 10–20 per day.
- Quality over quantity.
- Daily duplicate prevention.
- Public recruitment email only when confidently sourced.
- Browser applications only where technically and contractually appropriate.
- Human intervention only for exceptional/manual steps.

## 17. Telegram Commands
- /jobs – search for today's jobs.
- /apply – process the current qualified queue.
- /status – show today's application status.
- /report – generate a daily report.
- /pause – stop new applications.
- /resume – resume automation.
- /retry – retry safe failed tasks.
- /approve – approve a waiting action.
- /skip – skip a specific job.
- /history – show previous applications.
- /settings – show configured preferences.

## 18. Example Daily Telegram Report
Daily Job Report

Jobs scanned: 87
Qualified: 26
Selected: 18

Email applications sent: 5
Browser applications submitted: 12
Waiting for manual action: 1
Failed/retry queue: 0

Skipped: 61
Main reasons: experience mismatch, senior role, duplicate, irrelevant technology, or no suitable application route.

The report should include company, role, application URL and status for the selected opportunities.

## 19. Security and Reliability Requirements
- Never commit Gmail tokens, Telegram tokens, API keys or secrets into GitHub.
- Use GitHub Actions Secrets/Variables and appropriate cloud secret storage.
- Use least-privilege OAuth scopes where possible.
- Keep candidate data private.
- Do not expose resume files publicly.
- Sanitize job descriptions before passing them into agent prompts to reduce prompt-injection risk.
- Treat job descriptions and web pages as untrusted input.
- Use timeouts, retries and rate limits.
- Keep an audit trail.
- Do not send repeated emails because of retries.
- Provide a kill switch through Telegram.

## 20. Open-Source GitHub Agent Repositories
GitHub contains many open-source projects for AI agents, browser automation, MCP, RAG and workflow orchestration. The implementation team/AI IDE should research current repositories and select components based on maintenance status, license, security, documentation and fit.
Do not assume a repository is safe, free forever, production-ready or suitable merely because it is popular. Review its license and dependencies before integrating it.
Useful categories to investigate: LangGraph agents, LangChain integrations, Playwright browser agents, MCP servers, job-search agents, browser-use style projects, RAG/profile retrieval projects and Telegram automation examples.

## 21. Why GitHub Actions
- No VPS is required.
- Jobs can run as temporary workers.
- Scheduled workflows can start automatically.
- Browser automation can run without my laptop being continuously online.
- Open-source code can be version-controlled.
- Secrets can be managed as GitHub Actions secrets.
- It works well as the execution layer while Cloudflare Worker/D1 acts as the lightweight control/database layer.
Important: GitHub Actions free usage has limits and policies that can change. The implementation must verify the current GitHub plan/runner limits before relying on a specific quota. The design must remain efficient and avoid unnecessary runs.

## 22. Zero-Budget Principle
I currently have no budget for this project. Therefore, the first implementation must use free tiers and open-source software wherever possible.
- No paid VPS.
- No paid always-on server.
- No paid OpenAI API dependency.
- No paid automation platform dependency.
- No paid database.
- No requirement to keep my laptop online 24/7.
- Use free-tier/serverless services with documented limits.
- Make paid services optional future upgrades, not core dependencies.
The system must clearly report when a free-tier quota or provider limitation is reached instead of silently failing.

## 23. AI Model Strategy
The AI layer must be provider-independent. The first version should use an available free model/provider suitable for classification, extraction and email drafting. More expensive models can be added later.
Tasks should be structured so simple deterministic code handles simple operations and AI is used only where reasoning or language understanding is useful. This reduces cost and improves reliability.

## 24. Future Phase: Email Auto-Reply
Do NOT make automatic HR replies part of the first MVP. Add it after job discovery, application, tracking and email sending are stable.
- Read incoming recruitment emails.
- Classify them as rejection, interview, assessment, request for information, recruiter outreach, etc.
- Notify the user through Telegram.
- Generate a draft reply.
- Ask for approval initially.
- Later allow trusted low-risk reply types to be automatically sent if explicitly configured.

## 25. Future Enhancements
- Interview scheduling detection.
- Calendar integration.
- Application follow-up reminders.
- Recruiter conversation tracking.
- Resume variants by role.
- Company-specific research before application.
- Job quality analytics.
- Interview preparation generated from the exact job description.
- Application success/failure analytics.
- Personal knowledge base and vector search over projects/resume.
- Optional local Ollama/Hermes worker when the laptop is available.
- Optional paid model fallback if budget becomes available.

## 26. Implementation Phases

#### Phase 1 – Foundation
- Create GitHub repository.
- Create Telegram bot.
- Create Cloudflare Worker and D1 schema.
- Create candidate profile/configuration.
- Create GitHub Actions workflow.
- Implement logging and secrets.

#### Phase 2 – Job Discovery
- Add job-source adapters.
- Normalize job records.
- Deduplicate.
- Store source URLs.
- Build basic filtering.

#### Phase 3 – Smart Matching
- Add structured AI extraction.
- Compare jobs with verified profile.
- Add explainable match reasons.
- Build qualified queue.

#### Phase 4 – Contact + Email
- Find public recruitment contacts.
- Validate source.
- Generate personalized emails.
- Connect Gmail.
- Attach resume.
- Prevent duplicate sends.

#### Phase 5 – Browser Applications
- Add Playwright.
- Implement common form mapping.
- Add safe retries.
- Add screenshots/logs.
- Add human-in-the-loop states.

#### Phase 6 – Reporting
- Daily Telegram report.
- Application history.
- Failure/retry reporting.
- Manual-action notifications.

#### Phase 7 – Auto-Reply
- Email monitoring.
- Classification.
- Draft replies.
- Approval workflow.
- Optional trusted auto-replies.

## 27. Definition of Done for MVP
- I can send a Telegram command or rely on a schedule.
- The system can run while my laptop is OFF.
- It can discover a meaningful set of relevant jobs.
- It filters jobs based on my real profile.
- It avoids duplicates.
- It finds only publicly available recruitment contacts.
- It can send personalized application emails through my Gmail.
- It can attach my resume.
- It can track every application.
- It can perform suitable browser applications through Playwright.
- It pauses safely for CAPTCHA/OTP/legal or ambiguous steps.
- It sends a useful Telegram report.
- It operates within free-tier constraints without requiring paid hosting.

## 28. Important Engineering Principles
- Reliability over flashy autonomy.
- Quality over application volume.
- Verified facts over AI assumptions.
- Human approval for uncertain or consequential actions.
- Provider-independent architecture.
- Free/open-source first.
- Idempotent workflows.
- Strong logging and observability.
- Secure secrets handling.
- Modular adapters so job sites can be added/removed independently.
- Never bypass CAPTCHA or security controls.
- Never fabricate candidate information.

## 29. Instructions to the AI IDE / Development Agent
Treat this document as the authoritative product requirement. Before coding, inspect the repository and existing environment. Produce an implementation plan that respects the zero-budget constraint and the architecture above.
- First identify which requirements can be implemented entirely with open-source/free services.
- Check current service limits and current GitHub Actions capabilities before depending on a quota.
- Do not introduce paid APIs without clearly marking them as optional.
- Do not require a VPS or 24/7 laptop.
- Prefer modular components and interfaces.
- Use environment variables/secrets for credentials.
- Create a database migration/schema.
- Create a clear README with setup instructions.
- Create .env.example without real secrets.
- Create GitHub Actions workflows with safe permissions.
- Implement dry-run mode before real email sending/application submission.
- Add logging and retry handling.
- Do not bypass CAPTCHA, OTP, authentication or anti-bot protections.
- Use human-in-the-loop for uncertain/high-impact actions.
- Do not fabricate candidate experience or contact information.
- After implementation, explain exactly what is free, what has limits, and what would require payment later.

## 30. Final Expected User Experience
The intended experience is simple: I should be able to open Telegram and see that the agent has found relevant opportunities, intelligently filtered them, applied or emailed recruiters where appropriate, recorded everything, and clearly told me what needs my attention.
I do not want a dumb script that searches for 'AI Developer' and applies everywhere. I want a controlled, explainable personal job-search assistant that understands my profile, understands job requirements, makes sensible filtering decisions, personalizes applications, remembers previous actions, and uses human intervention only when automation should not continue.
The first milestone is not autonomous HR email replies. The first milestone is a stable, zero-budget system that can reliably handle approximately 10–20 quality job applications per day while my laptop remains OFF. Auto-reply, interview intelligence and other advanced agent capabilities come after this foundation is stable.

## 31. One-Sentence Project Definition
Build a zero-budget, cloud-hosted, Telegram-controlled AI job application agent that uses GitHub Actions for ephemeral execution, serverless storage/control, open-source agent tooling, browser automation and Gmail to intelligently discover, qualify, personalize, submit and track approximately 10–20 relevant job applications per day without requiring a paid server or an always-on laptop.