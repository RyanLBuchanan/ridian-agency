# Legacy dependency and Operator parity record

## Purpose

Ridian Operator is the primary product. The fixed workflows below remain available only through **Legacy Tools** while their useful outcomes are proven through natural-language Operator operations. This record prevents surface simplification from accidentally deleting backend capabilities.

No vertical is ready for physical deletion until its full chain is traced and its required outcomes have automated Operator-parity coverage:

`frontend entry → JavaScript handler → API route → service → agent/prompt → artifact schema → loaders/exports → tests`

## Status vocabulary

- **Partial** — the Operator covers some outcomes, but not the complete legacy contract.
- **Candidate after parity** — may be archived only after the listed gaps and dependencies are resolved.
- **Preserve** — shared infrastructure, not legacy implementation to remove.

## Dependency matrix

| Vertical | Frontend entry | API route | Service | Agents / prompts | Artifacts and loaders | Principal test coverage | Operator equivalent | Parity / archival status |
|---|---|---|---|---|---|---|---|---|
| Business workflow | `#view-input-business`; Legacy Tools → Business package; legacy sidebar mode | `POST /workflows/run` | `workflow_service.run_workflow` | `research_agent`, `writer_agent`, `reviewer_agent`, `presentation_agent`, `email_agent`; matching five prompt files | `task.txt`, `research_summary.md`, `business_document.md`, `slide_outline.md`, `draft_email.md`; `project_service` detection/load; ZIP/DOCX/PPTX/open/upload actions | project, folder artifact, export, email-summary, UI packaging tests | Operator research, `write_file`, Google Slides, Gmail draft, Drive upload | **Partial; candidate after parity.** Operator can produce equivalent kinds of work, but no test proves one command preserves the complete package/export/load contract. |
| Five-agent pipeline | Invoked by Business workflow rather than selected individually | Via `POST /workflows/run` | `workflow_service.py`; shared `anthropic_runtime.py` | Five `PromptAgent` definitions and five prompts | Same Business artifact set; each stage feeds the next | Anthropic migration and legacy workflow/UI tests | General planner plus narrow internal model calls/tools | **Partial; not ready.** Preserve prompts as recipe candidates until quality and transformation parity are measured. |
| Social workflow | `#view-input-social`; Legacy Tools → Social package; legacy sidebar mode | `POST /workflows/social-media/run` | `social_media_workflow_service.run_social_media_workflow` | `social_media_prompt.txt` | Social content package, script, caption, checklist, visual-production assets and `task.txt`; project detection/load, ZIP/open/Drive | UI packaging, folder artifact, project and Drive upload tests | Operator `write_file`, Drive, brand memory; no dedicated equivalent contract | **Partial; not ready.** Channel-specific inputs and all artifact sections need Operator acceptance tests before archival. |
| Agentic Advances | `#view-input-agentic`; Legacy Tools → Agentic advances brief | `POST /workflows/agentic-advances/run` | `agentic_advances_workflow_service.run_agentic_advances_workflow` | `agentic_advances_prompt.txt` | Fixed brief artifact plus task metadata; project loader/export/upload paths | research progress/model/grounding and UI tests | `web_research`, sources packet, generic brief artifact | **Near-parity candidate, not yet proven.** Add an end-to-end Operator contract before archive. |
| NotebookLM workflow | `#view-input-notebooklm`; Legacy Tools → NotebookLM package | `POST /workflows/notebooklm/run` | `notebooklm_workflow_service.run_notebooklm_workflow` | `notebooklm_prompt.txt` | NotebookLM package/task artifacts; project loader/export paths | project, folder artifact, packaging and research-related tests | `build_research_packet`; optional `open_browser("NotebookLM")` | **Near-parity candidate, not yet proven.** Confirm artifact/load/export behavior and intended product outcome first. |
| Quick Launch | Removed from Operator home; former dashboard `[data-ql]` handlers remain defensively in JS | Indirectly called workflow/memory APIs | Dashboard and legacy workflow services | Depends on selected workflow | Depends on target workflow | Dashboard/UI tests | Natural-language composer; Memory and trust views | **Surface removed.** Handler code may be removed only with the dashboard dependency slice. |
| Prompt library | Hidden inside Business/Social legacy forms; initialized by `buildPromptLibrary` / `buildSocialPromptLibrary` | None directly | Client-side constants | Prompt strings in `desktop/renderer/app.js` | Seeds legacy form fields only | UI structure/packaging tests | Composer examples plus planner capability discovery | **Legacy-only; candidate after forms archive.** Do not delete independently of legacy forms. |
| Fixed workflow results | `#view-run`, output tabs, Review & Publish, workflow-specific cards | Artifact open/export/upload and approved SMTP routes | `project_service`, `export_service`, `google_drive_service`, `email_delivery_service` | None directly | All legacy artifact schemas; recent-project load and export allowlists | folder artifact, PDF/export, Drive gate, email, project and packaging tests | Generic Operator artifact panel and operation history | **Partial; not ready.** Shared open/export/upload services must survive even when fixed cards are removed. |

## Shared infrastructure that must not be archived with legacy UI

- `anthropic_runtime.py`, settings, runtime paths, state storage, artifact/export services.
- Google Drive/Workspace/Gmail, QuickBooks, calendar, inbox, document and browser services.
- Operator planner, tool registry, context, operation log, approvals, audit, memory and business-state services.
- Artifact filename allowlists and project loading support until legacy folders have a migration policy.

## Required archival gates

Before archiving any vertical:

1. Add an Operator acceptance test for its valuable user outcome.
2. Prove equivalent artifacts/actions can be opened, reloaded, exported, and uploaded where applicable.
3. Confirm no route, desktop handler, project detector, allowlist, packaged resource, or test still imports it.
4. Preserve useful prompt constraints as an internal recipe only when they improve measured output quality.
5. Deprecate the route for at least one release before physical deletion if shipped clients may call it.
6. Remove the complete dependency slice in one reviewed change; do not leave orphan prompts, cards, or loaders.
