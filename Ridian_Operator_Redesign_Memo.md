Ridian Redesign Memo
You're right. Let me say so clearly before anything else: the recent direction has been wrong, and the most recent thing I built — a workflow that generates a NotebookLM prompt for you to paste into NotebookLM — is the cleanest possible evidence of how wrong. I built it, you approved the spec, and it shipped, but it's exactly the anti-pattern you're now naming. Owning that up front so the rest of this isn't defensive.
1. Diagnosis — why the current app misses the point
The current Ridian is a prompt-and-form wrapper masquerading as an agent. Every workflow shares the same shape:
You pick a category from a list (Business / Social / Visual / Agentic / NotebookLM).
You fill a form.
You click Run.
A single LLM call produces Markdown.
You review 4-8 cards, copy-paste outputs into other tools, manually file them, manually send the email.
What's wrong with that, specifically:
You're the integration layer. Ridian creates the artifact; you carry it to its destination. NotebookLM, Sora, Gmail, LinkedIn, the Drive folder — all manual round-trips.
The "agent" only runs one model call per click. No tool selection. No verification. No iteration. No memory writes. A Bash one-liner that hits the chat completion API would deliver the same business value.
Approvals are everywhere they shouldn't be. Review & Publish gates drafts. The model writes a research summary you haven't read, then asks you to check a box before the next nothing happens. Approval-of-internal-artifacts is bureaucracy, not safety.
Categories are the wrong primitive. You don't think "I want to run a Social Media Production workflow." You think "make me a TikTok hook from this clip." Forcing the user to pre-classify intent is the form-app trap.
Memory is read-only to agents. Contacts, facts, decisions — Ryan types them, agents never write them. So memory is a notebook, not a learning system.
The artifact is Markdown. Markdown is the work-in-progress format. It is not "I made a podcast for you" — it's "here's a script you could turn into a podcast."
The most recent two workflows (Agentic Advances, NotebookLM) just doubled down. The NotebookLM Builder literally exists to produce prompts to paste into another tool. That is the canonical wrong answer.
Net: I built you another piece of software you have to operate. You wanted software that operates for you.
2. New product definition (one sentence)
Ridian is a private business operator that turns natural business intent into completed, organized, ready-to-use work — files in Drive, drafts in Gmail, audio you can press play on — not prompts you carry elsewhere.
The word "operator" is doing real work here. It's not a generator. It's not an assistant. It's an operator: it understands intent, picks tools, takes action, verifies, and reports.
The other word is completed. The deliverable is a thing you can use, not a thing you can use to make a thing.
3. What should be removed, hidden, or demoted
Aggressive cuts. None of these are "delete in a year" — these are "do not let the user see them in v2."
Remove outright:
The NotebookLM Builder workflow. It is the literal opposite of what you want. Reverse it.
The "Visual Production" section's image-generation prompts and Sora-Ready prompts (same anti-pattern: it generates prompts for other tools instead of generating the image/video where possible).
The Wizard modal as a primary entry. Its job is to compensate for the form-app model — kill the form-app model and the wizard becomes pointless.
The 5-tab Review & Publish checklist as a default-shown panel. Make it opt-in for risky operations only.
Hide (still exist in the codebase, not exposed in the main UI):
The sidebar mode picker (Business / Social / Agentic / NotebookLM). Workflows become templates the planner can choose, not categories the user picks.
The current input forms for Business, Social, Agentic. They become "structured intent capture" the planner uses when it asks for clarification, not the primary entry point.
The per-card "Listen / Pause / Stop" TTS controls. If audio is the deliverable, ship an audio file. If text is the deliverable, don't bolt fake audio onto it.
Demote:
The current Dashboard. It's a launcher. The new home screen is the command box.
"Recent runs" sidebar → "Recent operations" panel. Same data, different framing: each entry is an operation that produced artifacts, not a run that produced cards.
The Quick Launch tiles. A few may survive as example commands shown in the command box ("Try: Email me an AGI brief by Friday"), but they're not the front door.
4. What should stay
Lots of bones are good. The product was wrong; the engineering wasn't.
OpenAI Agents SDK. It already supports tool registries, Runner.run, traces, hosted tools (WebSearchTool), function tools. It is exactly the right abstraction for the redesign.
FastAPI backend. Async, lightweight, easy to add streaming endpoints (SSE / WebSockets) for the live execution timeline.
Electron renderer. Desktop is correct for a private operator that touches local files, Drive, Gmail, calendars. No reason to move to web.
Local JSON state. It's fine. Atomic writes, git-ignored, simple. Don't replace with sqlite/postgres yet.
Google Drive integration with drive.file scope. Already works, already routes runs into a sensible hierarchy. Keep and expand.
Email sending after approval. Keep the SMTP path. Add Gmail Drafts (no approval needed — drafts aren't sent) as a parallel option.
Memory layer (contacts, brand, facts, follow-ups, decisions). Keep the schema. Change the access pattern: agents must be able to read memory automatically and propose writes the user can confirm in batch.
Outputs folder with per-run subfolders. Keep. Expand to first-class binary artifacts (mp3, mp4, pdf, docx, pptx).
Path validation, allowlists, atomic writes, secret hiding. All security work stays — the new product is more powerful so it needs the same discipline.
Dark mode + theme toggle, settings modal, Google connect flow. All good as-is.
Image input as a generalized "attach this to the operation" affordance. Keep.
5. New core interaction model
Replace the form-app loop with the Claude Code loop, in business terms:


INTENT          User types a sentence into the command box.
                ("Research the newest in AGI and give me a NotebookLM-style audiobook.")

CONTEXT         Planner reads:
                - memory (contacts, brand voice, facts, recent decisions)
                - recent operations (what was just done, so it can build on it)
                - attached files (if any)
                - operator profile

PLAN            Planner picks tools, declares intermediate artifacts,
                and shows the plan inline in the timeline.
                ("I'll search 8 sources, draft a script in a NotebookLM-style
                 two-host conversation, synthesize ~15 min of audio with OpenAI
                 TTS, save to outputs/, and upload to Drive. ~6 minutes.")

EXECUTE         Tools run, one after another, with live status streamed to the
                timeline. Each tool emits structured events the UI renders.
                Real binary artifacts appear in the artifacts panel as they land.

VERIFY          The planner checks its own work between steps:
                - did web search return ≥ 5 distinct primary sources?
                - is the script's runtime estimate in range?
                - did the mp3 actually write to disk?
                - is the total duration within ±20% of target?
                On failure, it re-plans or asks for clarification.

DELIVER         When done, the operation summary shows:
                - the deliverable (play the mp3 inline, open the file, open Drive)
                - the operation log (every tool call, every artifact path)
                - the next-action suggestion (one, not five)

MEMORY UPDATE   The planner proposes memory writes ("Saw a new term — 'inference
                compute scaling'. Worth adding as a fact?") in a single batch
                at the end. User confirms / dismisses each.
That's the loop. It's not five model calls dressed up as one — it's a planner that emits a sequence of tool invocations, each of which actually does business work.

6. Approval philosophy
One bright line: does this action change the world outside this machine?
No approval needed (silent, logged):
Web research / reading public pages
Writing files inside outputs/ (any format — md, mp3, mp4, pdf, docx, pptx, png)
Reading memory
Generating drafts (text, audio, image — locally saved)
Creating internal summaries
Uploading artifacts to Ryan's own Drive (it's his Drive, files he just made — file-tier scope, internal organization)
Creating Gmail drafts (Gmail Drafts are not sent — they sit waiting for him)
Logging operations to the action log
Proposing memory updates
Single batch approval at end of operation (one screen, one click):
Memory writes (which facts/follow-ups/decisions to commit)
Suggested next operations (which to schedule)
Always requires explicit approval:
Sending email (vs. drafting it)
Posting publicly anywhere (LinkedIn, etc.)
Creating Calendar events that invite other people
Editing Google Contacts (mutates a shared graph)
Sharing files externally from Drive (different from organizing within his own Drive)
Deleting anything
Spending money (API budget caps don't count — those are settings)
Adding/removing OAuth connections
The default mode is act quietly, report cleanly. The exception is external blast radius.
This kills 90% of the current approval friction. Review & Publish becomes a single optional view for high-stakes operations, not a 7-checkbox gauntlet for every research summary.
7. The "NotebookLM-style audiobook" example, worked end-to-end
Command:
"Research the newest in AGI and give me a NotebookLM-style audiobook."
How the redesigned Ridian handles it (no prompts-for-other-tools anywhere in this flow):
Plan (shown in timeline before execution):


Goal: 15-min two-host conversational audio about recent AGI developments.
Tools: web_search, fetch_url, write_script, synthesize_audio, write_file, drive_upload
Estimated runtime: ~5-7 min, cost ≈ $0.40 (TTS + reasoning)
External effects: none until you ask me to email/share it.
Execution timeline (live, streamed):


✓ 0:02  web_search — "AGI advances 2026" + 6 related queries → 23 hits
✓ 0:15  fetch_url ×8 (parallel) — extracted main text from top sources
        skipped 3 paywalled, kept 8 primary
✓ 0:18  rank_and_dedupe — 8 sources, 5 themes identified
✓ 0:22  write_script — 14:30 estimated runtime, two voices (Host A / Host B),
        4 themed segments, ~2200 words
✓ 0:28  synthesize_audio — OpenAI TTS (tts-1-hd), Host A=onyx, Host B=nova,
        segmented per speaker, concatenated, normalized
        → outputs/<run>/agi_audiobook.mp3  (14:42, 13.4 MB)
✓ 0:31  write_file — sources_packet.md (with URLs, excerpts, retrieval dates)
                   — script.md (the spoken script, for the record)
✓ 0:33  drive_upload — Ridian Technologies / Audio Briefings / 2026-05-28_AGI
                       3 files uploaded, no approval needed
Deliverable card:
▶ inline player for agi_audiobook.mp3 (press play and listen, right there)
"Open in Drive" link
"Open sources packet" + "Open script"
One next-action chip: "Email this to me?" (would require approval — single click → done)
Memory proposals (batch, end of operation):
"Add fact: 'OpenAI shipped X on 2026-05-22' (source: blog.openai.com)" — ☐ confirm
"Add follow-up: 'Listen to AGI audiobook before Thursday's Open Gulf episode'" — ☐ confirm
What can be automated right now:
Web search via OpenAI hosted WebSearchTool (already integrated).
Fetch + extract via a new fetch_url tool (httpx + trafilatura or readability-lxml).
Script writing via the planner agent itself.
Audio synthesis via OpenAI TTS API (tts-1 / tts-1-hd) — produces real MP3 from text using the same OPENAI_API_KEY already in settings. Two voices via two passes + ffmpeg concat. This is the keystone that makes the audiobook real rather than a script.
Drive upload (already works).
Gmail drafts via Gmail API drafts.create (additive to existing Google OAuth — needs gmail.compose scope; user opts in once).
What requires external APIs (defer to a later milestone):
NotebookLM itself has no public API for source ingestion or Audio Overview generation. We don't try to talk to NotebookLM at all. We produce a NotebookLM-equivalent audiobook ourselves with TTS — that's the whole point. The user explicitly said: if direct NotebookLM generation is impossible, Ridian must produce the closest direct execution path, not pretend a prompt is the finished work. TTS is that path.
What must remain manual for now:
Publishing the audiobook anywhere public (Spotify, Apple Podcasts) — that's a posting decision, not a generation decision.
Multi-speaker SSML / professional voice direction / sound design — TTS is good, not studio-grade. Fine for personal use, not for client deliverables yet.
Crucial: there is no point in this flow where Ridian outputs "here is a NotebookLM prompt for you to paste." The deliverable is an MP3 file Ryan can play. If TTS quality isn't good enough on a given day, the deliverable degrades to "a polished script + a one-click 'open NotebookLM with my script in clipboard'" — but the default is execution, not prompt-handoff.
8. Agentic requirements (in practical terms)
For Ridian, "agentic" means seven concrete capabilities:
Observes state. Reads memory, recent operations, attached files, settings, integration status before planning.
Chooses tools. A general-purpose planner agent with a real tool registry — not a category picker that maps to a single hardcoded prompt.
Executes. Tools do work that produces effects (files written, audio generated, drafts created, Drive folders organized), not work that produces prompts.
Verifies. After each step, the planner checks: did the artifact actually land? Is the count/duration/length in range? Did the tool error? Verification is cheap and high-leverage.
Retries / re-plans. If a web search returns 2 results instead of 8, search again with different terms. If TTS fails on one segment, retry that segment. Failure isn't terminal — it's a signal to re-plan.
Remembers. Every operation appends to a structured log (intent, plan, tool calls, artifacts, outcomes). The planner reads this log on subsequent operations to avoid repeating work and to propose follow-ups.
Escalates only when needed. Asks for approval only on external-effect actions. Asks for clarification only when intent is genuinely ambiguous. Silent otherwise.
The thing that's NOT in this list: "produces a prompt for the user to copy." That's the opposite of agentic.
9. Proposed architecture
Keep:
OpenAI Agents SDK. It already gives you Agent, Runner, tools (@function_tool + hosted tools), tracing. The redesign needs: one planner agent + a growing tool registry. That maps perfectly onto the SDK. No new orchestration framework needed.
FastAPI. Add SSE (Server-Sent Events) for streaming the execution timeline to the renderer. FastAPI supports StreamingResponse natively — no WebSockets needed.
Electron. Stays.
Local JSON state. Stays. Add one new file: operations.json (the operation log, one entry per executed operation).
Google Drive integration. Stays, used more aggressively (every operation auto-organizes its artifacts).
Add:
Tool registry pattern. Each tool is a @function_tool Python function with a clear signature, docstring, and verification. Tools available at v1:
web_search (hosted)
fetch_url (httpx + readability extractor)
write_file (validated path, allowlisted file types)
synthesize_speech (OpenAI TTS → mp3)
generate_image (OpenAI gpt-image-1 or dall-e-3 → png, optional, gated by settings)
drive_upload
gmail_draft (Gmail API drafts.create)
memory_read / memory_propose_write
read_recent_operation (so a follow-up command can reference "the brief I just made")
Planner agent. One general-purpose Agent with the full tool registry, instructed to plan, execute, verify, re-plan, and report. No category picker upstream.
Operation log + streaming timeline endpoint. POST /operations/run accepts an intent, returns SSE stream of events. GET /operations/recent returns the log.
Audio asset pipeline. OpenAI TTS + ffmpeg concatenation. ffmpeg is already on most dev machines; if missing, fall back to single-voice TTS and surface the missing-ffmpeg notice in settings.
Inline <audio> player in the artifacts panel (just an HTML5 <audio> tag pointed at the local file).
Do NOT add:
LangGraph / CrewAI / AutoGen. The Agents SDK + a tool registry is enough. State machines are overkill when the planner can just call tools in sequence. Adding orchestration frameworks now would be premature and would slow iteration.
A queue / Celery / Redis. Operations run synchronously in the FastAPI process. If long-running becomes a problem, add asyncio.create_task + an in-process job table before reaching for external infrastructure.
A vector DB for memory. The memory tables are small and finite. A planner agent reads them as JSON context. Vector search is a solution to a problem we don't have.
A browser-automation tool. Tempting (Comet, Playwright). But fragile and high-maintenance for personal use. Skip for v1; revisit if a real use case appears.
A new database. JSON files have been fine; they'll stay fine through milestones 1-3 at least.
10. New UI concept
The home screen is not a dashboard. It is a command surface.


┌─ Ridian ─────────────────────────────────────────────────────────────────────┐
│                                                                              │
│   Good evening, Ryan.    Tuesday, May 28              [Settings] [Drive: ✓]  │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐    │
│  │  What should Ridian do?                                              │    │
│  │  ┌────────────────────────────────────────────────────────────────┐  │    │
│  │  │ Research the newest in AGI and give me a NotebookLM-style      │  │    │
│  │  │ audiobook.                                                      │  │    │
│  │  └────────────────────────────────────────────────────────────────┘  │    │
│  │                                                       [⏎  Run] [📎] │    │
│  │                                                                      │    │
│  │  Try:  "Brief me on Gulf Shores chambers"                            │    │
│  │        "Draft a follow-up to Sarah for tomorrow"                     │    │
│  │        "Make a 5-min Open Gulf TikTok hook from yesterday's clip"    │    │
│  └──────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ─── Active operation ───────────────────────────────────────────────────    │
│  Audiobook: Recent AGI advances                       Running · 0:34 elapsed │
│                                                                              │
│   ✓ Web search → 23 hits across 6 queries                                    │
│   ✓ Fetch + extract → 8 primary sources                                      │
│   ✓ Rank + dedupe → 5 themes                                                 │
│   ⟳ Writing two-host script · ~14 min target                                 │
│   ○ Synthesize audio (queued)                                                │
│   ○ Upload to Drive (queued)                                                 │
│                                                                              │
│  ─── Artifacts (live) ───────────────────────────────────────────────────    │
│  ☑ sources_packet.md   (8 sources)                                           │
│  ⟳ script.md           (writing…)                                            │
│  ○ agi_audiobook.mp3                                                         │
│                                                                              │
│  ─── Sidebar: Context ─────────  ─── Sidebar: Recent ops ──────────────      │
│  Memory:                          Tue 4:12pm  AGI audiobook (running)        │
│   · 7 contacts                    Tue 11:03  Open Gulf TikTok hook           │
│   · 3 brand voices                Mon 9:22   Chamber outreach drafts ✉       │
│   · 12 facts                      Sun 6:15   Weekend reading brief           │
│   · 4 open follow-ups                                                        │
│   · 6 decisions logged           [View all operations]                       │
│                                                                              │
│  Active integrations:                                                        │
│   · Google Drive ✓                                                           │
│   · Gmail SMTP ✓                                                             │
│   · OpenAI ✓ (gpt-4o-mini)                                                   │
└──────────────────────────────────────────────────────────────────────────────┘
Key shifts from current UI:
One command box. No mode picker, no wizard, no category cards. Just a sentence.
Live execution timeline (replaces the spinner). Each tool call is a row, with status, and clickable to expand the call details. Like Claude Code's tool list.
Artifacts panel (replaces the 5-card output view). Real files, live-updated, with inline players where applicable.
Recent operations (replaces Recent Runs). Same data, framed as completed jobs not "runs."
Context sidebar stays — memory counts, integration health. Click any line to drill in.
Settings + Drive status pill stay top-right.
No "Review & Publish" tab by default. When an operation needs approval (sending email, posting publicly), the approval surfaces inline in the timeline at the moment the approval is needed — not as a pre-emptive checklist.
The workflow-form views still exist for now (Business Workflow / Social) but are accessible only from a "Templates" affordance for cases where Ryan genuinely wants the structured form. They're not on the home screen.

11. First implementation milestone
Operator v1 — One real command, end to end.
The single command:
"Research the newest in AGI and give me a NotebookLM-style audiobook."
That command must produce, with no manual steps from Ryan beyond typing it and pressing Run:
8+ cited web sources
A sources_packet.md
A two-host conversational script.md of ~12-18 minutes
A real audiobook.mp3 produced via OpenAI TTS, with two voices
All three files saved to outputs/<timestamp>_agi-audiobook/
All three files uploaded to Drive / Ridian Technologies / Audio Briefings / <date>_AGI
An inline <audio> player on the result screen
A clean operation log entry with every tool call
A single suggested follow-up ("Email this to me?") that requires one click of approval
Why this command:
It is exactly the failure case you cited in this memo, solved correctly.
It exercises every new architectural piece (planner, tool registry, audio synthesis, streaming timeline, Drive upload, operation log).
The deliverable is unambiguous: an MP3 either exists and plays, or it doesn't. No "is the prompt good?" subjectivity.
It's narrow enough to ship in one development pass.
The instant the demo works, Ridian feels like a different product.
What's deliberately NOT in v1:
No Gmail drafts yet (defer to v2; SMTP send-after-approval is enough for now).
No Calendar integration (defer).
No Google Contacts (defer).
No image / video generation (defer; we don't have a great use case for it yet).
No general-purpose conversational fallback ("just chat with me"). v1 is operations only. Conversational chat can come later.
No multi-step / multi-turn operations. v1 is one command → one operation → done. Multi-turn comes after we see how single-turn behaves.
If v1 works, Ridian has fundamentally changed shape inside a week of work, not a quarter.
12. Concrete build plan for Operator v1
Backend — new files:
apps/api/app/services/operator_service.py — runs an operation: builds planner agent, streams events, persists log.
apps/api/app/services/tts_service.py — wraps OpenAI TTS API; handles segmented two-voice synthesis + ffmpeg concat.
apps/api/app/services/fetch_service.py — httpx + readability-lxml (or trafilatura) URL fetcher with content extraction.
apps/api/app/services/operation_log_service.py — append-only operation log (apps/api/state/operations.json), atomic writes.
apps/api/app/agents/planner_agent.py — single general-purpose planner agent with full tool registry.
apps/api/app/agents/tools/ — one file per tool: web_search.py (re-exposes hosted tool with usage instructions), fetch_url.py, write_file.py, synthesize_speech.py, drive_upload.py, memory_read.py, memory_propose_write.py.
apps/api/app/prompts/planner_prompt.txt — the planner system prompt (operator persona, verification rules, when to ask for approval).
Backend — modified files:
apps/api/app/main.py — add POST /operations/run (SSE streaming), GET /operations/recent, GET /operations/{id}. Add an audio MIME mapping for .mp3 in static serving if not already.
apps/api/app/services/export_service.py — extend ALLOWED_OPEN_FILENAMES to allow .mp3 files matching *.mp3 pattern (or add a general "audio output" allowlist).
apps/api/app/services/google_drive_service.py — add .mp3 to MIME map, extend UPLOAD_ALLOWED_FILENAMES for audio files, add new Drive route segment for audio (Audio Briefings).
apps/api/app/services/memory_service.py — add propose_write API that returns proposed writes without committing, plus commit_proposals that takes a confirmation set.
apps/api/app/services/project_service.py — extend _detect_workflow to recognize operations (different folder shape), add operation workflow type.
Backend — settings:
Add tts_voice_a / tts_voice_b to settings (default onyx / nova).
Add ffmpeg_path (auto-detected, override-able).
Add audio_quality (tts-1 / tts-1-hd).
Frontend — new components:
New home view replacing #view-welcome: command box + live timeline + artifacts panel + recent ops.
OperationTimeline component (renders SSE events as a stepped list).
ArtifactsPanel component (live list of artifacts with inline <audio> players for .mp3, <img> for images, "Open" for everything else).
RecentOperations sidebar entry.
Frontend — modified:
desktop/renderer/app.js — add SSE handling for the operations endpoint, render timeline events live.
desktop/renderer/styles.css — timeline + artifacts panel styles.
desktop/renderer/index.html — hide workflow mode buttons + Quick Launch from home (move behind a "Templates" disclosure), replace the dashboard with the command surface.
State files:
New: apps/api/state/operations.json — operation log.
Existing memory state untouched.
Endpoints:
POST /operations/run (SSE stream)
GET /operations/recent
GET /operations/{id}
POST /operations/{id}/approve-followup (for the single batched approval at end)
POST /memory/propose-write (planner-proposed batch)
Existing endpoints all stay (backward compat for the template-based workflow views).
Tests:
Tool registry smoke tests (each tool callable in isolation).
Planner agent integration test on a known small prompt (e.g. "summarize what's at https://example.com" — verify it calls fetch_url, then writes a summary).
TTS service test (mock the OpenAI client, verify segmenting + ffmpeg invocation).
Operation log persistence + recovery test.
End-to-end test for the AGI audiobook command using mocked tools (real OpenAI calls behind a RIDIAN_LIVE_TEST=1 flag).
What to hide or delete in this pass:
Hide #view-input-notebooklm and the NotebookLM workflow from the sidebar mode group (deprecate the endpoint with a 410 Gone + a planner-suggested alternative).
Hide #view-input-agentic, #view-input-business, #view-input-social from the home view. Keep their code; they're reachable only from a "Templates" affordance and become callable by the planner internally.
Remove the Quick Launch tile row from the home view.
Remove the Today's Focus card from the home view. The home view is the command box; suggestions can surface in the timeline post-operation.
Keep Memory modal, Settings modal, Wizard modal, but make Wizard reachable only from inside a future "Templates" view, not the sidebar.
13. Risks (and how to avoid them)
Risk: It becomes another prompt wrapper, just one level deeper.
Avoidance: Every tool in the registry must produce a side effect (file written, draft created, upload completed) or it doesn't ship. No tool whose output is "here's a prompt." Enforced by code review of the tool registry, not by hope.
Risk: The planner hallucinates capabilities it doesn't have.
Avoidance: Planner prompt explicitly lists the tool registry and forbids inventing tools. Add a "capability discovery" step early in the planner prompt where it grounds its plan in actual tool names. Verification step after planning checks every named tool exists.
Risk: TTS quality is poor enough that Ryan never listens to the audiobooks.
Avoidance: Test with tts-1-hd on the AGI demo before declaring v1 done. If it's not listenable, ship v1 with the script + sources only, and label TTS as "preview" rather than skip the milestone. The architecture still wins even without great audio.
Risk: Latency too long.
Avoidance: Stream every step. Aggressively parallelize tool calls (web fetches especially). Set a soft target of ≤8 min for the AGI command. If we miss it badly, audio synthesis is almost certainly the bottleneck — switch to tts-1 (lower quality, much faster) by default and offer tts-1-hd as an opt-in setting.
Risk: Approval logic too noisy → user disables it; or too loose → embarrassing send.
Avoidance: Start with the strict bright line in §6. Log every approval prompt. If user dismisses the same kind of prompt 3 times, surface a settings toggle to permanently allow that category. No "remember my choice" auto-magic — explicit toggle only.
Risk: The planner takes too many steps for simple commands ("what's the weather" turns into a 4-tool plan).
Avoidance: Planner prompt instructs: minimum-viable plan; one-tool answers are normal; only chain tools when the deliverable demands it. Test on trivial commands as a smoke check.
Risk: We rebuild form-app habits because hiding workflows leaves a gap.
Avoidance: Don't bring the forms back via the front door. If a particular template gets used a lot, the planner can ask one structured clarification question inline ("Which channel — Open Gulf TikTok, YouTube, or LinkedIn?") instead of presenting a form. Inline questions, not form pages.
Risk: Memory writes spam the user with "want me to save this?" prompts.
Avoidance: Batch all memory proposals to a single end-of-operation panel. Default-suggested writes are pre-checked; "low confidence" suggestions are unchecked. One submit button.
Risk: We ship Operator v1 and then revert to building more form-based workflows because they're easier.
Avoidance: After v1, the next milestone's success criterion is another command type works end-to-end via the planner — not a new template. If we find ourselves writing per-category code, that's a signal we're regressing.

14. Decision recommendation
Continue with the current architecture, redesign the product on top of it. Not a restart, not a fork.
The reasoning:
The technical foundation is sound. OpenAI Agents SDK, FastAPI, Electron, local JSON, Drive integration, settings/secrets handling, atomic writes, path validation — all of this is exactly what an operator app needs. Restarting would waste 4-6 weeks rebuilding plumbing that already works.
The product needs aggressive subtraction, not deletion. The form-based workflows aren't wrong code — they're just promoted to the wrong place in the UI. Hide them. Let the planner call them internally as templates if useful. Don't make Ryan see them.
One milestone proves the redesign. Operator v1 (the AGI audiobook command) is a one-week build that demonstrates the new model. If after that week the new home screen + command box + execution timeline + real audio artifact feels right, we commit. If it doesn't, we've burned a week and learned exactly why — much cheaper than a fork.
A fork or restart would risk re-shipping the same anti-pattern in cleaner code. The current app's problem isn't messy code — it's that we built the wrong product carefully. New repo doesn't fix that. New product definition + new interaction model does, and it can be grafted onto the current code without throwing away the work that's good.
Concretely:
Keep main branch as-is until Operator v1 is on feature/operator-v1.
Build v1 against the existing codebase (no rewrites). New files added; existing files mostly untouched except for main.py, the home view in index.html, and app.js.
Demo the AGI audiobook command at the end of one development pass. If it produces a playable MP3 from one sentence with no manual steps, the redesign is validated. Merge.
After merge, deprecate the old workflow forms from the home view in a follow-up pass. They keep working from a "Templates" disclosure until at least 3 more command types ship via the planner — then revisit whether to delete them entirely.
Optional rename: drop "Agency," keep "Ridian." Ridian — your private business operator. The "Command Center" tagline survives; the "Agency" framing was always a misdirect toward the agency-of-LLMs metaphor instead of the actually-useful agency-on-your-behalf metaphor.
One closing line
Stop building things that produce prompts. Start building things that produce results. If the next deliverable can't be played, opened, sent, or filed without you carrying it somewhere, it's not done.

