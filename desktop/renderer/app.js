/* Ridian Agency desktop renderer.
 *
 * Talks to the local FastAPI backend at http://127.0.0.1:8000 via fetch().
 * Layout: persistent left sidebar (mode + recent runs + outputs) +
 * workspace (header + body with welcome / input / run states).
 */

const BACKEND = (window.ridian && window.ridian.backendOrigin) || 'http://127.0.0.1:8000';

const EXAMPLE_TASK =
  "Research practical AI consulting opportunities for small businesses in Gulf Shores, Orange Beach, Foley, and Fairhope Alabama.";

const DEFAULT_EMAIL_SUBJECT = 'Ridian Agency Draft Email Output';

/* ============================================================ */
/*                       PROMPT LIBRARIES                        */
/* ============================================================ */

const PROMPT_CATEGORIES = [
  {
    id: 'market-research', label: 'Market Research',
    prompts: [
      'Research practical AI consulting opportunities for small businesses in Baldwin County, Alabama. Focus on industries, pain points, competitors, and first outreach opportunities.',
      'Research AI workflow automation opportunities for healthcare clinics, dental offices, and wellness providers in Lower Alabama.',
      'Research how local tourism and hospitality businesses could use AI for guest communication, scheduling, reviews, and operations.',
    ],
  },
  {
    id: 'client-outreach', label: 'Client Outreach',
    prompts: [
      'Create a concise outreach package for a local business owner explaining how AI workflow automation could save time, reduce missed follow-ups, and improve customer communication.',
      'Draft a professional introductory email to a chamber of commerce director proposing a short AI productivity presentation for local businesses.',
      'Create a follow-up email after an AI consulting conversation, summarizing the opportunity and suggesting next steps.',
    ],
  },
  {
    id: 'slide-decks', label: 'Slide Decks',
    prompts: [
      'Create a 7-slide outline for a presentation titled "Practical AI for Small Business Owners." Include pain points, examples, workflow ideas, and a simple call to action.',
      'Create a chamber of commerce lunch-and-learn slide outline about AI productivity tools for local businesses.',
      'Create a sales presentation outline for Ridian Technologies explaining market research agents, document generation, and email workflow support.',
    ],
  },
  {
    id: 'internal-productivity', label: 'Internal Productivity',
    prompts: [
      'Create a weekly business development plan for Ridian Technologies focused on outreach, follow-ups, demos, content creation, and partnership opportunities.',
      'Create a prioritized task plan for turning Ridian Agency into a polished local desktop product.',
      'Create a founder operating brief summarizing the next 5 actions, risks, and opportunities for Ridian Technologies this week.',
    ],
  },
  {
    id: 'industry-specific', label: 'Industry-Specific',
    prompts: [
      'Research AI automation opportunities for HVAC, plumbing, and electrical companies in Baldwin County.',
      'Research AI productivity opportunities for real estate agents and property managers in coastal Alabama.',
      'Research AI support workflows for educators, instructional designers, and training departments.',
    ],
  },
];

// Open Gulf content operating system — platform-first prompt library.
//
// Tabs are organized by PLATFORM (not by brand) so the operator chooses
// where the content lives first, then the prompt encodes the platform's
// role in the Open Gulf ecosystem:
//   TikTok        = The Spark         (curiosity, "wait, what?")
//   YouTube       = The Heart         (depth, teaching, trust)
//   Instagram     = The Face          (visual identity, warm Gulf Coast futurism)
//   LinkedIn      = The Brain         (professional authority, AI education credibility)
//   X / Twitter   = The Signal Stream (rapid ideas, sharp observations)
// Plus two cross-platform scenario tabs (Existing Footage, Weekly Planning).
//
// Each prompt pre-fills the social form. The channel value drives Drive
// folder routing in google_drive_service.map_channel_to_path. Platforms
// without dedicated routing rules (Instagram, X / Twitter) currently use
// channel='Custom' and land under Social Media / Custom — see the
// follow-up note in the deliverable summary.
const SOCIAL_PROMPT_CATEGORIES = [
  {
    id: 'tiktok', label: 'TikTok',
    prompts: [
      { text: 'Create a 20–30 second Open Gulf TikTok that sparks curiosity about one unconventional AI strategy.',
        fields: { channel: 'Open Gulf TikTok', starting_point: 'I have a topic', content_format: 'Short-form video',
          topic_notes: 'Open Gulf TikTok = THE SPARK. 20–30 seconds. One hook, one reframe, one example, one redirect to YouTube. Spark curiosity about one unconventional AI strategy — flip a common assumption.' } },
      { text: 'Create a TikTok hook and script showing why people should stop treating ChatGPT like Google.',
        fields: { channel: 'Open Gulf TikTok', starting_point: 'I have a topic', content_format: 'Short-form video',
          topic_notes: 'Open Gulf TikTok = THE SPARK. Hook + script: why people should stop treating ChatGPT like Google. Make the contrast vivid. End with a soft redirect to the YouTube deep-dive.' } },
      { text: 'Create a short TikTok that uses one clear AI insight, one example, and one redirect to YouTube.',
        fields: { channel: 'Open Gulf TikTok', starting_point: 'I have a topic', content_format: 'Short-form video',
          topic_notes: 'Open Gulf TikTok = THE SPARK. Structure: one clear AI insight + one concrete example + one redirect to YouTube. Get them curious enough to click through.' } },
    ],
  },
  {
    id: 'youtube', label: 'YouTube',
    prompts: [
      { text: 'Create a YouTube episode outline that expands a TikTok idea into a deeper Open Gulf teaching segment.',
        fields: { channel: 'Open Gulf YouTube', starting_point: 'I have a topic', content_format: 'Long-form YouTube video',
          topic_notes: 'Open Gulf YouTube = THE HEART. Expand a TikTok idea into a 6–10 minute teaching segment. Trust, depth, teaching, philosophy, storytelling, human connection. Warm, educator-friendly, reflective.' } },
      { text: 'Create a 6–10 minute YouTube script about using AI as a strategic thinking partner.',
        fields: { channel: 'Open Gulf YouTube', starting_point: 'I have a topic', content_format: 'Long-form YouTube video',
          topic_notes: 'Open Gulf YouTube = THE HEART. 6–10 minute script: using AI as a strategic thinking partner (not a search box). Cover the mindset shift, practical examples, one exercise the viewer can do today.' } },
      { text: 'Create a YouTube breakdown that teaches role, context, goal, output format, guardrails, and clarifying questions.',
        fields: { channel: 'Open Gulf YouTube', starting_point: 'I have a topic', content_format: 'Long-form YouTube video',
          topic_notes: 'Open Gulf YouTube = THE HEART. Teaching breakdown of the six-part prompt structure: role, context, goal, output format, guardrails, clarifying questions. Worked example for each part. Depth + warmth.' } },
    ],
  },
  {
    id: 'instagram', label: 'Instagram',
    prompts: [
      { text: 'Create an Instagram Reel concept using warm Gulf Coast futurism visuals to explain one AI idea.',
        fields: { channel: 'Open Gulf Instagram', starting_point: 'I have a topic', content_format: 'Short-form video',
          topic_notes: 'Open Gulf Instagram Reel concept. Platform: Instagram. Open Gulf Instagram = THE FACE — visual identity, aesthetic presence, emotional tone. Warm Gulf Coast futurism (teal + amber palette, sunsets, ocean waves, calm technology). Include shot list, on-screen captions, voiceover sketch, IG caption (~150–300 chars), 5–8 hashtags.' } },
      { text: 'Create an Instagram carousel that turns an Open Gulf AI insight into 5 simple visual slides.',
        fields: { channel: 'Open Gulf Instagram', starting_point: 'I have a topic', content_format: 'Caption/post only',
          topic_notes: 'Open Gulf Instagram carousel: turn one AI insight into 5 simple, visually clean slides. Platform: Instagram. Open Gulf Instagram = THE FACE. Provide slide-by-slide copy + visual direction (Gulf Coast palette: teal + amber). Include the post caption, hashtags, and a suggested first comment.' } },
      { text: 'Create a reflective Instagram caption about learning to use AI with less fear and more curiosity.',
        fields: { channel: 'Open Gulf Instagram', starting_point: 'I have a topic', content_format: 'Caption/post only',
          topic_notes: 'Open Gulf Instagram caption (single image post). Platform: Instagram. Open Gulf Instagram = THE FACE. Reflective: learning to use AI with less fear and more curiosity. Photo concept + caption (~150–300 chars suited for IG) + 3–6 hashtags + alt text. Warm, human-centered, calm.' } },
    ],
  },
  {
    id: 'linkedin', label: 'LinkedIn',
    prompts: [
      { text: 'Create a professional LinkedIn post about why educators and professionals should treat AI as a thinking partner, not a search box.',
        fields: { channel: 'Open Gulf LinkedIn', starting_point: 'I have a topic', content_format: 'Caption/post only',
          topic_notes: 'Open Gulf LinkedIn = THE BRAIN. Professional authority, AI education credibility. Post: why educators and professionals should treat AI as a thinking partner, not a search box. Concrete, useful, non-hype.' } },
      { text: 'Create a LinkedIn thought-leadership post connecting prompt strategy to the future of learning and work.',
        fields: { channel: 'Open Gulf LinkedIn', starting_point: 'I have a topic', content_format: 'Caption/post only',
          topic_notes: 'Open Gulf LinkedIn = THE BRAIN. Thought-leadership: connect prompt strategy to the future of learning and work. Position Ryan as a credible, calm voice on AI education. Avoid AI-bro tone.' } },
      { text: 'Create a concise LinkedIn post that introduces Open Gulf as a human-centered AI education media project.',
        fields: { channel: 'Open Gulf LinkedIn', starting_point: 'I have a topic', content_format: 'Caption/post only',
          topic_notes: 'Open Gulf LinkedIn = THE BRAIN. Concise introduction of Open Gulf as a human-centered AI education media project. 900–1500 chars. Mission (warm + reflective + practical), audience (educators, entrepreneurs, professionals), soft CTA.' } },
    ],
  },
  {
    id: 'x-twitter', label: 'X / Twitter',
    prompts: [
      { text: 'Create 10 short Open Gulf posts from one AI insight.',
        fields: { channel: 'Open Gulf X / Twitter', starting_point: 'I have a topic', content_format: 'Caption/post only',
          topic_notes: 'Open Gulf X / Twitter = THE SIGNAL STREAM. Platform: X. 10 short posts (each ≤ 280 chars) all derived from ONE AI insight. Each post stands alone. Number them. Open Gulf voice — rapid ideas, sharp observations, philosophical questions, thinking out loud.' } },
      { text: 'Create a sharp X thread about why better AI conversations start with better context.',
        fields: { channel: 'Open Gulf X / Twitter', starting_point: 'I have a topic', content_format: 'Caption/post only',
          topic_notes: 'Open Gulf X / Twitter = THE SIGNAL STREAM. Platform: X (thread). 6–10 tweets: why better AI conversations start with better context. Open Gulf voice — thoughtful, sharp, not preachy. Strong first tweet that earns the click-through. End with a CTA.' } },
      { text: 'Create short idea fragments about AI, education, creativity, and the future of work.',
        fields: { channel: 'Open Gulf X / Twitter', starting_point: 'I have a topic', content_format: 'Caption/post only',
          topic_notes: 'Open Gulf X / Twitter = THE SIGNAL STREAM. Platform: X. 8–12 short idea fragments suitable for live testing. Topics: AI, education, creativity, future of work. Reflective, warm, philosophical. Each ≤ 280 chars. Number them.' } },
    ],
  },
  {
    id: 'existing-footage', label: 'Existing Footage',
    prompts: [
      { text: 'Turn this recorded clip into platform-specific outputs for TikTok, YouTube Shorts, Instagram, LinkedIn, and X / Twitter.',
        fields: { channel: 'Custom', starting_point: 'I have existing footage or a thumbnail', content_format: 'Repurposed clip',
          media_notes: '(Describe your recorded clip here — what is on screen, what you said, the mood, the length, any on-screen text.)',
          topic_notes: 'Repurpose ONE recorded clip into platform-specific outputs for all five Open Gulf platforms: TikTok (the spark), YouTube Shorts (the heart, compressed), Instagram (the face), LinkedIn (the brain), X / Twitter (the signal stream). Adapt — do not duplicate. Each platform gets its own hook, copy, caption, and posting checklist.' } },
      { text: 'Analyze this footage description and create the best Open Gulf short-form post package for the most suitable platform.',
        fields: { channel: 'Custom', starting_point: 'I have existing footage or a thumbnail', content_format: 'Short-form video',
          media_notes: '(Describe the footage in detail — composition, subject, mood, length, any audio.)',
          topic_notes: 'Analyze the footage. Recommend ONE best-fit Open Gulf platform (TikTok / Instagram Reel / YouTube Short / LinkedIn video) and explain in one sentence why. Then produce a complete platform-shaped post package for that platform only.' } },
      { text: 'Repurpose this existing video into a hook, script, caption, hashtags, and posting checklist.',
        fields: { channel: 'Custom', starting_point: 'I have existing footage or a thumbnail', content_format: 'Short-form video',
          media_notes: '(Describe your existing video — visuals, what was said, mood, length.)',
          topic_notes: 'Repurpose the existing video into a complete posting package: hook, tightened script, caption, hashtags, posting checklist. Work with the material as given — do not invent new topics. Open Gulf voice.' } },
    ],
  },
  {
    id: 'weekly-planning', label: 'Weekly Planning',
    prompts: [
      { text: 'Create a 7-day Open Gulf content plan across TikTok, YouTube, Instagram, LinkedIn, and X / Twitter from one core weekly theme.',
        fields: { channel: 'Custom', starting_point: 'Generate ideas from scratch', content_format: 'Content calendar', output_depth: 'Weekly content plan',
          topic_notes: 'Open Gulf 7-day content plan across all five platforms (TikTok = spark, YouTube = heart, LinkedIn = brain, Instagram = face, X / Twitter = signal stream). Pick ONE core weekly theme and adapt it per platform — do not duplicate. For each day list: platform, post topic, hook, format, suggested visual, CTA, repurpose idea, best audience angle.' } },
      { text: 'Create a weekly content map where TikTok sparks curiosity, YouTube teaches deeply, LinkedIn builds authority, Instagram builds identity, and X / Twitter tests short ideas.',
        fields: { channel: 'Custom', starting_point: 'Generate ideas from scratch', content_format: 'Content calendar', output_depth: 'Weekly content plan',
          topic_notes: 'Open Gulf weekly content map. Each platform has a distinct role: TikTok sparks curiosity (20–30s, one reframe), YouTube teaches deeply (6–10 min teaching), LinkedIn builds authority (900–1500 char post), Instagram builds identity (visual / aesthetic), X / Twitter tests short ideas (sharp fragments). Plan 7 days with this division of labor.' } },
      { text: 'Create a weekly Open Gulf publishing plan for AI productivity, education, creativity, and human-centered technology.',
        fields: { channel: 'Custom', starting_point: 'Generate ideas from scratch', content_format: 'Content calendar', output_depth: 'Weekly content plan',
          topic_notes: 'Open Gulf weekly publishing plan. Themes to weave through the week: AI productivity, education, creativity, human-centered technology. All five platforms (TikTok, YouTube, Instagram, LinkedIn, X / Twitter). Pick a tentpole topic for the week and spin it across formats — TikTok hook, YouTube teaching, LinkedIn authority, Instagram visual, X fragments.' } },
    ],
  },
];

const SOCIAL_FIELD_MAP = {
  channel: 'socialChannel',
  starting_point: 'socialStartingPoint',
  content_format: 'socialContentFormat',
  goal: 'socialGoal',
  output_depth: 'socialOutputDepth',
  media_notes: 'socialMediaNotes',
  topic_notes: 'socialTopicNotes',
};

/* ============================================================ */
/*                       SETTINGS FIELDS                         */
/* ============================================================ */

const SETTINGS_FIELDS = [
  'operator_name', 'operator_email', 'default_to_email', 'company_name',
  'openai_model', 'smtp_host', 'smtp_port', 'smtp_username', 'smtp_from_email',
  'google_drive_root_folder_id',
  'appearance',
];
const SETTINGS_SECRET_FIELDS = ['openai_api_key', 'smtp_password'];

/* ============================================================ */
/*                    TABS PER WORKFLOW MODE                     */
/* ============================================================ */

const TABS_BUSINESS = [
  { label: 'Review & Publish',  panel: 'review-publish-card' },
  { label: 'Actions',           panel: 'actions-card' },
  { label: 'Files',             panel: 'artifact-folder-card' },
  { label: 'Research',          panel: 'research-card' },
  { label: 'Business Document', panel: 'business-document-card' },
  { label: 'Slide Outline',     panel: 'slide-outline-card' },
  { label: 'Draft Email',       panel: 'draft-email-card' },
];

const TABS_SOCIAL = [
  { label: 'Review & Publish',  panel: 'review-publish-card' },
  { label: 'Actions',           panel: 'actions-card' },
  { label: 'Files',             panel: 'artifact-folder-card' },
  { label: 'Content Package',   panel: 'social-content-package-card' },
  { label: 'Script',            panel: 'social-script-card' },
  { label: 'Caption',           panel: 'social-caption-card' },
  { label: 'Checklist',         panel: 'social-checklist-card' },
  { label: 'Visual Production', panel: 'social-visual-production-card' },
];

const TABS_AGENTIC = [
  { label: 'Review & Publish', panel: 'review-publish-card' },
  { label: 'Actions',          panel: 'actions-card' },
  { label: 'Files',            panel: 'artifact-folder-card' },
  { label: 'Brief',            panel: 'agentic-brief-card' },
];

const TABS_NOTEBOOKLM = [
  { label: 'Review & Publish', panel: 'review-publish-card' },
  { label: 'Actions',          panel: 'actions-card' },
  { label: 'Files',            panel: 'artifact-folder-card' },
  { label: 'Package',          panel: 'notebooklm-package-card' },
];

/* ============================================================ */
/*                REVIEW & PUBLISH CHECKLISTS                    */
/* ============================================================ */
/* Session-only state — each new workflow run resets the boxes by
 * re-rendering the list. No persistence across app restarts. */

const REVIEW_CHECKLIST_BUSINESS = [
  { id: 'rv-bus-research',  text: 'Review research summary',         actionPanel: 'research-card',          actionLabel: 'Open' },
  { id: 'rv-bus-document',  text: 'Review business document',        actionPanel: 'business-document-card', actionLabel: 'Open' },
  { id: 'rv-bus-slides',    text: 'Review slide outline',            actionPanel: 'slide-outline-card',     actionLabel: 'Open' },
  { id: 'rv-bus-email',     text: 'Review draft email',              actionPanel: 'draft-email-card',       actionLabel: 'Open' },
  { id: 'rv-bus-export',    text: 'Export or upload the package',    actionPanel: 'actions-card',           actionLabel: 'Open' },
  { id: 'rv-bus-send',      text: 'Send approved email if desired',  actionPanel: 'draft-email-card',       actionLabel: 'Open' },
];

const REVIEW_CHECKLIST_SOCIAL = [
  { id: 'rv-soc-content',   text: 'Review content package',                                actionPanel: 'social-content-package-card',        actionLabel: 'Open' },
  { id: 'rv-soc-script',    text: 'Review script',                                          actionPanel: 'social-script-card',                actionLabel: 'Open' },
  { id: 'rv-soc-caption',   text: 'Review caption',                                         actionPanel: 'social-caption-card',               actionLabel: 'Open' },
  { id: 'rv-soc-checklist', text: 'Review posting checklist',                               actionPanel: 'social-checklist-card',             actionLabel: 'Open' },
  { id: 'rv-soc-visual',    text: 'Review visual production plan',                          actionPanel: 'social-visual-production-card',     actionLabel: 'Open' },
  { id: 'rv-soc-upload',    text: 'Upload package to Google Drive if desired',              actionPanel: 'actions-card',                      actionLabel: 'Open' },
  { id: 'rv-soc-publish',   text: 'Manually publish or schedule on the chosen platform',    actionPanel: null,                                actionLabel: null },
];

const REVIEW_CHECKLIST_AGENTIC = [
  { id: 'rv-agt-summary',  text: 'Review executive summary',                       actionPanel: 'agentic-brief-card',  actionLabel: 'Open' },
  { id: 'rv-agt-sources',  text: 'Review sources and confidence flags',            actionPanel: 'agentic-brief-card',  actionLabel: 'Open' },
  { id: 'rv-agt-ridian',   text: 'Review Ridian opportunities and next actions',   actionPanel: 'agentic-brief-card',  actionLabel: 'Open' },
  { id: 'rv-agt-upload',   text: 'Upload brief to Google Drive if desired',        actionPanel: 'actions-card',        actionLabel: 'Open' },
  { id: 'rv-agt-email',    text: 'Email brief to yourself if desired',             actionPanel: 'agentic-brief-card',  actionLabel: 'Open' },
];

const REVIEW_CHECKLIST_NOTEBOOKLM = [
  { id: 'rv-nlm-package',  text: 'Review the NotebookLM package',                  actionPanel: 'notebooklm-package-card', actionLabel: 'Open' },
  { id: 'rv-nlm-copy',     text: 'Copy the Audio Overview prompt into NotebookLM', actionPanel: 'notebooklm-package-card', actionLabel: 'Open' },
  { id: 'rv-nlm-upload',   text: 'Upload package to Google Drive if desired',      actionPanel: 'actions-card',            actionLabel: 'Open' },
  { id: 'rv-nlm-email',    text: 'Email package to yourself if desired',           actionPanel: 'notebooklm-package-card', actionLabel: 'Open' },
];

/* ============================================================ */
/*                          DOM REFS                             */
/* ============================================================ */

const els = {
  // sidebar
  sidebarNewWorkflowBtn: document.getElementById('sidebar-new-workflow-btn'),
  sidebarModeBusiness: document.getElementById('sidebar-mode-business'),
  sidebarModeSocial: document.getElementById('sidebar-mode-social'),
  sidebarModeAgentic: document.getElementById('sidebar-mode-agentic'),
  sidebarModeNotebooklm: document.getElementById('sidebar-mode-notebooklm'),
  sidebarOutputs: document.getElementById('sidebar-outputs'),
  sidebarOutputsList: document.getElementById('sidebar-outputs-list'),
  sidebarRunsList: document.getElementById('sidebar-runs-list'),
  sidebarRunsEmptyState: document.getElementById('sidebar-runs-empty-state'),
  sidebarRunsSearch: document.getElementById('sidebar-runs-search'),
  sidebarRunsShowMore: document.getElementById('sidebar-runs-show-more'),
  sidebarHiddenToggle: document.getElementById('sidebar-hidden-toggle'),
  sidebarHiddenList: document.getElementById('sidebar-hidden-list'),
  sidebarDashboardBtn: document.getElementById('sidebar-dashboard-btn'),
  sidebarSettingsBtn: document.getElementById('sidebar-settings-btn'),
  sidebarTipsBtn: document.getElementById('sidebar-tips-btn'),
  tipsModal: document.getElementById('tips-modal'),
  tipsCloseBtn: document.getElementById('tips-close-btn'),
  tipsDoneBtn: document.getElementById('tips-done-btn'),
  welcomeTip: document.getElementById('welcome-tip'),
  welcomeTipDismiss: document.getElementById('welcome-tip-dismiss'),

  // workspace header
  workspaceTitle: document.getElementById('workspace-title'),
  workspaceSubtitle: document.getElementById('workspace-subtitle'),
  workspaceBackBtn: document.getElementById('workspace-back-btn'),
  backendPill: document.getElementById('backend-pill'),
  backendLabel: document.getElementById('backend-pill-label'),
  googlePill: document.getElementById('google-pill'),
  googlePillLabel: document.getElementById('google-pill-label'),

  // banners
  backendDownBanner: document.getElementById('backend-down-banner'),
  openaiMissingBanner: document.getElementById('openai-missing-banner'),

  // views
  viewWelcome: document.getElementById('view-welcome'),
  viewInputBusiness: document.getElementById('view-input-business'),
  viewInputSocial: document.getElementById('view-input-social'),
  viewInputAgentic: document.getElementById('view-input-agentic'),
  viewInputNotebooklm: document.getElementById('view-input-notebooklm'),
  viewRun: document.getElementById('view-run'),

  // status / error
  status: document.getElementById('status-region'),
  statusSub: document.getElementById('status-sub'),
  elapsed: document.getElementById('elapsed'),
  errorRegion: document.getElementById('error-region'),
  errorMessage: document.getElementById('error-message'),

  // business form
  taskInput: document.getElementById('task-input'),
  runBtn: document.getElementById('run-btn'),
  clearBtn: document.getElementById('clear-btn'),
  exampleBtn: document.getElementById('example-btn'),

  // social form
  socialChannel: document.getElementById('social-channel'),
  socialStartingPoint: document.getElementById('social-starting-point'),
  socialContentFormat: document.getElementById('social-content-format'),
  socialGoal: document.getElementById('social-goal'),
  socialOutputDepth: document.getElementById('social-output-depth'),
  socialMediaNotes: document.getElementById('social-media-notes'),
  socialTopicNotes: document.getElementById('social-topic-notes'),
  socialRunBtn: document.getElementById('social-run-btn'),
  socialClearBtn: document.getElementById('social-clear-btn'),
  thumbnailFileInput: document.getElementById('thumbnail-file-input'),
  thumbnailSelectBtn: document.getElementById('thumbnail-select-btn'),
  thumbnailFilename: document.getElementById('thumbnail-filename'),
  thumbnailClearBtn: document.getElementById('thumbnail-clear-btn'),

  // agentic advances form
  agenticTimeWindow: document.getElementById('agentic-time-window'),
  agenticOutputDepth: document.getElementById('agentic-output-depth'),
  agenticTopicFocus: document.getElementById('agentic-topic-focus'),
  agenticRunBtn: document.getElementById('agentic-run-btn'),
  agenticClearBtn: document.getElementById('agentic-clear-btn'),
  agenticSendEmailBtn: document.getElementById('agentic-send-email-btn'),
  agenticSendEmailStatus: document.getElementById('agentic-send-email-status'),

  // notebooklm form
  notebooklmSubject: document.getElementById('notebooklm-subject'),
  notebooklmPurpose: document.getElementById('notebooklm-purpose'),
  notebooklmAudience: document.getElementById('notebooklm-audience'),
  notebooklmOutputType: document.getElementById('notebooklm-output-type'),
  notebooklmNotes: document.getElementById('notebooklm-notes'),
  notebooklmRunBtn: document.getElementById('notebooklm-run-btn'),
  notebooklmClearBtn: document.getElementById('notebooklm-clear-btn'),
  notebooklmSendEmailBtn: document.getElementById('notebooklm-send-email-btn'),
  notebooklmSendEmailStatus: document.getElementById('notebooklm-send-email-status'),

  // prompt library containers
  promptsTabsBusiness: document.querySelector('#view-input-business .prompts-tabs'),
  promptsPanelsBusiness: document.querySelector('#view-input-business .prompts-panels'),
  socialPromptsTabs: document.getElementById('social-prompts-tabs'),
  socialPromptsPanels: document.getElementById('social-prompts-panels'),

  // run view
  runSummary: document.getElementById('run-summary'),
  resultPanelArea: document.getElementById('result-panel-area'),
  resultsBusiness: document.getElementById('results-business'),
  resultsSocial: document.getElementById('results-social'),
  resultsAgentic: document.getElementById('results-agentic'),
  resultsNotebooklm: document.getElementById('results-notebooklm'),

  // actions card
  actionsStatus: document.getElementById('actions-status'),
  actionOpenFolder: document.getElementById('action-open-folder'),
  actionCopyFolder: document.getElementById('action-copy-folder'),
  actionExportZip: document.getElementById('action-export-zip'),
  actionUploadDrive: document.getElementById('action-upload-drive'),
  actionDriveLink: document.getElementById('action-drive-link'),
  driveSuccessCard: document.getElementById('drive-success-card'),
  driveSuccessCount: document.getElementById('drive-success-count'),
  driveSuccessPath: document.getElementById('drive-success-path'),
  driveSuccessCopy: document.getElementById('drive-success-copy'),
  driveSuccessCopyStatus: document.getElementById('drive-success-copy-status'),

  // email
  sendEmailBtn: document.getElementById('send-email-btn'),
  sendEmailStatus: document.getElementById('send-email-status'),

  // settings modal
  settingsModal: document.getElementById('settings-modal'),
  settingsForm: document.getElementById('settings-form'),
  settingsCloseBtn: document.getElementById('settings-close-btn'),
  settingsCancelBtn: document.getElementById('settings-cancel-btn'),
  settingsSaveBtn: document.getElementById('settings-save-btn'),
  settingsTestEmailBtn: document.getElementById('settings-test-email-btn'),
  settingsStatus: document.getElementById('settings-status'),
  settingsPasswordHint: document.getElementById('settings-password-hint'),
  settingsOpenaiKeyHint: document.getElementById('settings-openai-key-hint'),
  settingsOutputsPath: document.getElementById('settings-outputs-path'),
  googleConnectBtn: document.getElementById('google-connect-btn'),
  googleDisconnectBtn: document.getElementById('google-disconnect-btn'),
  googleStatusLabel: document.getElementById('google-status-label'),
};

/* ============================================================ */
/*                            STATE                              */
/* ============================================================ */

let currentMode = 'business';        // 'business' | 'social'
let currentView = 'welcome';         // 'welcome' | 'input' | 'run'
let currentResult = null;
let currentResultTabs = [];          // [{label, panel}, ...] for the active mode
let currentRunMeta = null;           // {channel, starting_point, ...} for social
let cachedSettings = null;
let recentRuns = [];                 // [{artifact_folder, name, workflow, channel, mtime_iso}]
let activeRunFolder = null;          // string — which sidebar run is highlighted
let hiddenRuns = [];                 // [{artifact_folder, name, workflow, channel, mtime_iso}]
let hiddenRunsExpanded = false;      // disclosure state for the hidden section
let recentRunsSearch = '';           // current filter text for Recent runs
let runsFilter = 'all';             // 'all' | 'pinned' | 'business' | 'social'
let runsExpanded = false;           // false = show RUNS_PAGE_SIZE; true = show all
const RUNS_PAGE_SIZE = 10;
let thumbnailData = null;           // { name, dataUri } or null
const THUMBNAIL_MAX_BYTES = 5 * 1024 * 1024; // 5 MB

/* ============================================================ */
/*                          HELPERS                              */
/* ============================================================ */

const show = (el) => el && el.classList.remove('hidden');
const hide = (el) => el && el.classList.add('hidden');

function escapeHtml(s) {
  return (s || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function debugLog(...args) {
  // eslint-disable-next-line no-console
  console.log('[ridian]', ...args);
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
    return ok;
  }
}

function fmtDateShort(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch (_) {
    return iso;
  }
}

function prettifyRunName(name) {
  // "20260524-141337_open-gulf-tiktok-7-day..." -> "open gulf tiktok 7 day..."
  const idx = name.indexOf('_');
  const tail = idx >= 0 ? name.slice(idx + 1) : name;
  return tail.replace(/-/g, ' ');
}

/* ============================================================ */
/*                  MINIMAL MARKDOWN -> HTML                     */
/* ============================================================ */

function inlineMd(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*(?!\*)/g, '$1<em>$2</em>');
  return s;
}

function renderMarkdown(md) {
  const lines = (md || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  const flushP = (buf) => {
    const text = buf.join(' ').trim();
    if (text) out.push(`<p>${inlineMd(text)}</p>`);
  };
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*$/.test(line)) { i++; continue; }
    if (/^\s*---+\s*$/.test(line)) { out.push('<hr />'); i++; continue; }
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) { out.push(`<h${h[1].length}>${inlineMd(h[2].trim())}</h${h[1].length}>`); i++; continue; }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, '')); i++;
      }
      out.push('<ul>' + items.map((it) => `<li>${inlineMd(it)}</li>`).join('') + '</ul>');
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, '')); i++;
      }
      out.push('<ol>' + items.map((it) => `<li>${inlineMd(it)}</li>`).join('') + '</ol>');
      continue;
    }
    const buf = [];
    while (i < lines.length && !/^\s*$/.test(lines[i]) && !/^(#{1,4})\s+/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+\.\s+/.test(lines[i]) && !/^\s*---+\s*$/.test(lines[i])) {
      buf.push(lines[i]); i++;
    }
    flushP(buf);
  }
  return out.join('\n');
}

/* ============================================================ */
/*                    WORKSPACE STATE MACHINE                    */
/* ============================================================ */

function setWorkspaceView(view) {
  // 'welcome' | 'input' | 'run'
  currentView = view;
  hide(els.viewWelcome);
  hide(els.viewInputBusiness);
  hide(els.viewInputSocial);
  hide(els.viewInputAgentic);
  hide(els.viewInputNotebooklm);
  hide(els.viewRun);
  hide(els.status);
  hide(els.errorRegion);
  if (view === 'welcome') {
    show(els.viewWelcome);
    updateWorkspaceHeader('Dashboard', 'Ridian Command Center');
    loadDashboard();
  } else if (view === 'input') {
    if (currentMode === 'social') {
      show(els.viewInputSocial);
      updateWorkspaceHeader('Social Media Production', 'Configure your brief, then run the social workflow.');
    } else if (currentMode === 'agentic') {
      show(els.viewInputAgentic);
      updateWorkspaceHeader('Agentic Advances Brief', 'Pick a time window and depth, then run the brief.');
    } else if (currentMode === 'notebooklm') {
      show(els.viewInputNotebooklm);
      updateWorkspaceHeader('NotebookLM Builder', 'Pick a subject, purpose, and audience, then build the package.');
    } else {
      show(els.viewInputBusiness);
      updateWorkspaceHeader('Business Workflow', 'Describe a task, then run the workflow.');
    }
  } else if (view === 'run') {
    show(els.viewRun);
    // header updated by updateRunSummary
  }
  hide(els.sidebarOutputs); // shown only inside 'run'
  if (view === 'run') show(els.sidebarOutputs);

  // Back arrow appears on every non-welcome view.
  if (els.workspaceBackBtn) {
    els.workspaceBackBtn.classList.toggle('hidden', view === 'welcome');
  }
  // Dashboard sidebar button highlights when on Dashboard (welcome view).
  if (els.sidebarDashboardBtn) {
    els.sidebarDashboardBtn.classList.toggle('is-active', view === 'welcome');
  }
  // Welcome tip is visible only on Welcome (and only if not dismissed).
  refreshWelcomeTip();
}

function updateWorkspaceHeader(title, subtitle) {
  if (els.workspaceTitle) els.workspaceTitle.textContent = title || '';
  if (els.workspaceSubtitle) els.workspaceSubtitle.textContent = subtitle || '';
}

function setMode(mode) {
  currentMode = mode;
  els.sidebarModeBusiness && els.sidebarModeBusiness.classList.toggle('is-active', mode === 'business');
  els.sidebarModeSocial && els.sidebarModeSocial.classList.toggle('is-active', mode === 'social');
  els.sidebarModeAgentic && els.sidebarModeAgentic.classList.toggle('is-active', mode === 'agentic');
  els.sidebarModeNotebooklm && els.sidebarModeNotebooklm.classList.toggle('is-active', mode === 'notebooklm');
}

// First-launch welcome tip. Hidden once dismissed; the localStorage flag
// keeps it gone across app restarts. Visible only inside the Welcome view.
const WELCOME_TIP_KEY = 'ridian.welcomeTipDismissed';

function refreshWelcomeTip() {
  if (!els.welcomeTip) return;
  let dismissed = false;
  try { dismissed = window.localStorage.getItem(WELCOME_TIP_KEY) === 'true'; } catch (_) {}
  if (dismissed || currentView !== 'welcome') {
    els.welcomeTip.classList.add('hidden');
  } else {
    els.welcomeTip.classList.remove('hidden');
  }
}

function dismissWelcomeTip() {
  try { window.localStorage.setItem(WELCOME_TIP_KEY, 'true'); } catch (_) {}
  if (els.welcomeTip) els.welcomeTip.classList.add('hidden');
}

// Returns true if the active input form has user-typed draft text we
// should warn about before navigating away. Dropdown state doesn't count
// (they're always set), only the freeform textareas.
function hasUnsavedFormText() {
  if (currentView !== 'input') return false;
  if (currentMode === 'business') {
    return !!(els.taskInput && els.taskInput.value.trim());
  }
  if (currentMode === 'social') {
    const a = els.socialTopicNotes && els.socialTopicNotes.value.trim();
    const b = els.socialMediaNotes && els.socialMediaNotes.value.trim();
    return !!(a || b);
  }
  return false;
}

function goBackToWelcome() {
  if (currentView === 'welcome') return;
  audioStop();
  if (hasUnsavedFormText()) {
    const ok = window.confirm(
      'Return to the welcome screen? Your current unsaved form text will remain available if you come back during this session.'
    );
    if (!ok) return;
  }
  // Don't touch form values, recent runs, or generated outputs — the user
  // can navigate forward again and pick up where they left off.
  setWorkspaceView('welcome');
}

/* ============================================================ */
/*                  SIDEBAR — RECENT RUNS                        */
/* ============================================================ */

async function loadRecentRunsFromBackend() {
  try {
    const [recentRes, hiddenRes] = await Promise.all([
      fetch(`${BACKEND}/projects/recent?limit=30`),
      fetch(`${BACKEND}/projects/hidden`),
    ]);
    if (recentRes.ok) {
      const data = await recentRes.json();
      recentRuns = data.projects || [];
    } else {
      recentRuns = [];
    }
    if (hiddenRes.ok) {
      const data = await hiddenRes.json();
      hiddenRuns = data.projects || [];
    } else {
      hiddenRuns = [];
    }
    renderRecentRuns();
    renderHiddenRuns();
  } catch (err) {
    debugLog('projects.recent.failed', { error: err && err.message });
    recentRuns = [];
    hiddenRuns = [];
    renderRecentRuns();
    renderHiddenRuns();
  }
}

function _runMatchesSearch(run, query) {
  if (!query) return true;
  const haystack = [
    run.name || '',
    run.channel || '',
    run.workflow || '',
  ].join(' ').toLowerCase();
  return haystack.includes(query);
}

function _setEmptyStateHtml(html) {
  if (!els.sidebarRunsEmptyState) return;
  if (!html) {
    els.sidebarRunsEmptyState.innerHTML = '';
    hide(els.sidebarRunsEmptyState);
    return;
  }
  els.sidebarRunsEmptyState.innerHTML = html;
  show(els.sidebarRunsEmptyState);
}

function _renderEmptyState({ kind, query }) {
  // kind: 'no-runs' | 'all-hidden' | 'no-match'
  let html = '';
  if (kind === 'no-runs') {
    html = `
      No runs yet.<br />
      Pick <strong>Business Workflow</strong> or <strong>Social Media Production</strong>
      above, write a task or click a suggested prompt, then <strong>Run</strong>.
    `;
  } else if (kind === 'all-hidden') {
    html = `
      All your runs are hidden.
      <button type="button" class="sidebar-empty-action" data-empty-action="expand-hidden">Show hidden runs</button>
      below, or start a <button type="button" class="sidebar-empty-action" data-empty-action="new-workflow">new workflow</button>.
    `;
  } else if (kind === 'no-match') {
    const safe = (query || '').replace(/[<>&"']/g, '');
    html = `
      No runs match <strong>"${escapeHtml(safe)}"</strong>.
      Try different keywords or
      <button type="button" class="sidebar-empty-action" data-empty-action="clear-search">clear the search</button>.
    `;
  }
  _setEmptyStateHtml(html);

  if (els.sidebarRunsEmptyState) {
    els.sidebarRunsEmptyState.querySelectorAll('[data-empty-action]').forEach((btn) => {
      const action = btn.getAttribute('data-empty-action');
      btn.addEventListener('click', () => {
        if (action === 'clear-search') {
          if (els.sidebarRunsSearch) els.sidebarRunsSearch.value = '';
          recentRunsSearch = '';
          renderRecentRuns();
        } else if (action === 'expand-hidden') {
          if (!hiddenRunsExpanded) toggleHiddenRunsList();
        } else if (action === 'new-workflow') {
          startNewWorkflow(currentMode);
        }
      });
    });
  }
}

function _applyRunsFilter(runs) {
  if (runsFilter === 'pinned') return runs.filter((r) => r.pinned);
  if (runsFilter === 'business') {
    // "Business" filter excludes social / agentic / notebooklm — pure business runs only.
    return runs.filter((r) =>
      r.workflow !== 'social' && r.workflow !== 'agentic' && r.workflow !== 'notebooklm'
    );
  }
  if (runsFilter === 'social') return runs.filter((r) => r.workflow === 'social');
  if (runsFilter === 'agentic') return runs.filter((r) => r.workflow === 'agentic');
  if (runsFilter === 'notebooklm') return runs.filter((r) => r.workflow === 'notebooklm');
  return runs; // 'all'
}

function _buildRunLi(run) {
  const li = document.createElement('li');
  li.className = 'sidebar-run-li';
  if (run.pinned) li.classList.add('is-pinned');

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'sidebar-list-item';
  if (run.artifact_folder === activeRunFolder) btn.classList.add('is-active');
  btn.setAttribute('data-folder', run.artifact_folder);
  let labelChannel;
  if (run.workflow === 'social') labelChannel = run.channel || 'Custom';
  else if (run.workflow === 'agentic') labelChannel = 'Agentic Brief';
  else if (run.workflow === 'notebooklm') labelChannel = 'NotebookLM';
  else labelChannel = 'Business';
  const title = prettifyRunName(run.name);
  btn.innerHTML = `
    <span class="sidebar-run-title">${escapeHtml(title)}</span>
    <span class="sidebar-run-meta">${escapeHtml(labelChannel)} · ${escapeHtml(fmtDateShort(run.mtime_iso))}</span>
  `;
  btn.addEventListener('click', () => openProjectFromSidebar(run));

  const actions = document.createElement('span');
  actions.className = 'sidebar-run-actions';

  const pinBtn = document.createElement('button');
  pinBtn.type = 'button';
  pinBtn.className = 'sidebar-pin-btn';
  pinBtn.setAttribute(
    'aria-label',
    run.pinned ? `Unpin ${title}` : `Pin ${title} to top`
  );
  pinBtn.setAttribute('title', run.pinned ? 'Unpin from top' : 'Pin to top');
  pinBtn.textContent = run.pinned ? '★' : '☆';
  pinBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (run.pinned) unpinRun(run);
    else pinRun(run);
  });

  const hideBtn = document.createElement('button');
  hideBtn.type = 'button';
  hideBtn.className = 'sidebar-hide-btn';
  hideBtn.setAttribute('aria-label', `Hide ${title} from sidebar`);
  hideBtn.setAttribute('title', 'Hide from sidebar');
  hideBtn.textContent = '×';
  hideBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    hideRunFromSidebar(run);
  });

  actions.appendChild(pinBtn);
  actions.appendChild(hideBtn);
  li.appendChild(btn);
  li.appendChild(actions);
  return li;
}

function _addGroupLabel(container, text) {
  const div = document.createElement('li');
  div.className = 'sidebar-pinned-divider';
  div.setAttribute('aria-hidden', 'true');
  div.textContent = text;
  container.appendChild(div);
}

function renderRecentRuns() {
  if (!els.sidebarRunsList) return;
  els.sidebarRunsList.innerHTML = '';

  // Update filter chip active state
  document.querySelectorAll('[data-runs-filter]').forEach((chip) => {
    chip.classList.toggle('is-active', chip.getAttribute('data-runs-filter') === runsFilter);
  });

  if (!recentRuns.length) {
    if (hiddenRuns.length > 0) {
      _renderEmptyState({ kind: 'all-hidden' });
    } else {
      _renderEmptyState({ kind: 'no-runs' });
    }
    if (els.sidebarRunsShowMore) hide(els.sidebarRunsShowMore);
    return;
  }

  const q = (recentRunsSearch || '').trim().toLowerCase();
  let filtered = recentRuns.filter((r) => _runMatchesSearch(r, q));
  filtered = _applyRunsFilter(filtered);

  if (!filtered.length) {
    _renderEmptyState({ kind: 'no-match', query: recentRunsSearch || runsFilter });
    if (els.sidebarRunsShowMore) hide(els.sidebarRunsShowMore);
    return;
  }
  _setEmptyStateHtml('');

  // Separate pinned from unpinned
  const pinned = filtered.filter((r) => r.pinned);
  const unpinned = filtered.filter((r) => !r.pinned);

  // Pinned always visible
  if (pinned.length && unpinned.length) _addGroupLabel(els.sidebarRunsList, 'Pinned');
  pinned.forEach((run) => els.sidebarRunsList.appendChild(_buildRunLi(run)));

  // Recent (unpinned) — paginate
  if (unpinned.length && pinned.length) _addGroupLabel(els.sidebarRunsList, 'Recent');
  const limit = runsExpanded ? unpinned.length : RUNS_PAGE_SIZE;
  const visible = unpinned.slice(0, limit);
  const hasMore = unpinned.length > limit;
  visible.forEach((run) => els.sidebarRunsList.appendChild(_buildRunLi(run)));

  // Show more / Show less
  if (els.sidebarRunsShowMore) {
    if (hasMore || runsExpanded) {
      show(els.sidebarRunsShowMore);
      if (runsExpanded) {
        els.sidebarRunsShowMore.textContent = 'Show less';
      } else {
        els.sidebarRunsShowMore.textContent = `Show ${unpinned.length - limit} more`;
      }
    } else {
      hide(els.sidebarRunsShowMore);
    }
  }
}

function renderHiddenRuns() {
  if (!els.sidebarHiddenToggle || !els.sidebarHiddenList) return;
  const count = hiddenRuns.length;

  if (count === 0) {
    hide(els.sidebarHiddenToggle);
    hide(els.sidebarHiddenList);
    hiddenRunsExpanded = false;
    els.sidebarHiddenToggle.setAttribute('aria-expanded', 'false');
    return;
  }

  show(els.sidebarHiddenToggle);
  els.sidebarHiddenToggle.textContent =
    (hiddenRunsExpanded ? 'Hide list (' : 'Show hidden runs (') + count + ')';
  els.sidebarHiddenToggle.setAttribute('aria-expanded', hiddenRunsExpanded ? 'true' : 'false');

  if (!hiddenRunsExpanded) {
    hide(els.sidebarHiddenList);
    return;
  }
  show(els.sidebarHiddenList);

  els.sidebarHiddenList.innerHTML = '';
  hiddenRuns.forEach((run) => {
    const li = document.createElement('li');
    li.className = 'sidebar-hidden-item';
    const labelChannel = run.workflow === 'social' ? (run.channel || 'Custom') : 'Business';
    const title = prettifyRunName(run.name);
    li.innerHTML = `
      <span class="sidebar-hidden-item-text">
        <span class="sidebar-hidden-item-title">${escapeHtml(title)}</span>
        <span class="sidebar-hidden-item-meta">${escapeHtml(labelChannel)} · ${escapeHtml(fmtDateShort(run.mtime_iso))}</span>
      </span>
    `;
    const restoreBtn = document.createElement('button');
    restoreBtn.type = 'button';
    restoreBtn.className = 'sidebar-restore-btn';
    restoreBtn.textContent = 'Restore';
    restoreBtn.setAttribute('aria-label', `Restore ${title} to the sidebar`);
    restoreBtn.addEventListener('click', () => restoreRunToSidebar(run));
    li.appendChild(restoreBtn);
    els.sidebarHiddenList.appendChild(li);
  });
}

function toggleHiddenRunsList() {
  hiddenRunsExpanded = !hiddenRunsExpanded;
  renderHiddenRuns();
}

async function hideRunFromSidebar(run) {
  if (!run || !run.artifact_folder) return;
  debugLog('projects.hide.start', { folder: run.artifact_folder });
  // Optimistic UI: move from recent to hidden immediately, then sync.
  recentRuns = recentRuns.filter((r) => r.artifact_folder !== run.artifact_folder);
  hiddenRuns = [run, ...hiddenRuns.filter((r) => r.artifact_folder !== run.artifact_folder)];
  if (activeRunFolder === run.artifact_folder) activeRunFolder = null;
  renderRecentRuns();
  renderHiddenRuns();

  try {
    const res = await fetch(`${BACKEND}/projects/hide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_folder: run.artifact_folder }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    debugLog('projects.hide.ok', { folder: run.artifact_folder });
  } catch (err) {
    debugLog('projects.hide.failed', { error: err && err.message });
    // Roll back optimistic move
    await loadRecentRunsFromBackend();
  }
}

function _resortRecentRuns() {
  // Mirror the backend ordering: pinned first, then newest mtime first.
  recentRuns.sort((a, b) => (b.mtime_iso || '').localeCompare(a.mtime_iso || ''));
  recentRuns.sort((a, b) => (a.pinned ? 0 : 1) - (b.pinned ? 0 : 1));
}

async function pinRun(run) {
  if (!run || !run.artifact_folder) return;
  debugLog('projects.pin.start', { folder: run.artifact_folder });
  // Optimistic: flip the flag locally + resort + render
  const target = recentRuns.find((r) => r.artifact_folder === run.artifact_folder);
  if (target) target.pinned = true;
  // If it was hidden somehow, drop from hidden too (mutual exclusion).
  hiddenRuns = hiddenRuns.filter((r) => r.artifact_folder !== run.artifact_folder);
  _resortRecentRuns();
  renderRecentRuns();
  renderHiddenRuns();
  try {
    const res = await fetch(`${BACKEND}/projects/pin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_folder: run.artifact_folder }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    debugLog('projects.pin.ok', { folder: run.artifact_folder });
  } catch (err) {
    debugLog('projects.pin.failed', { error: err && err.message });
    await loadRecentRunsFromBackend();
  }
}

async function unpinRun(run) {
  if (!run || !run.artifact_folder) return;
  debugLog('projects.unpin.start', { folder: run.artifact_folder });
  const target = recentRuns.find((r) => r.artifact_folder === run.artifact_folder);
  if (target) target.pinned = false;
  _resortRecentRuns();
  renderRecentRuns();
  try {
    const res = await fetch(`${BACKEND}/projects/unpin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_folder: run.artifact_folder }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    debugLog('projects.unpin.ok', { folder: run.artifact_folder });
  } catch (err) {
    debugLog('projects.unpin.failed', { error: err && err.message });
    await loadRecentRunsFromBackend();
  }
}

async function restoreRunToSidebar(run) {
  if (!run || !run.artifact_folder) return;
  debugLog('projects.unhide.start', { folder: run.artifact_folder });
  // Optimistic UI
  hiddenRuns = hiddenRuns.filter((r) => r.artifact_folder !== run.artifact_folder);
  recentRuns = [run, ...recentRuns.filter((r) => r.artifact_folder !== run.artifact_folder)];
  // Keep order roughly by mtime
  recentRuns.sort((a, b) => (b.mtime_iso || '').localeCompare(a.mtime_iso || ''));
  renderRecentRuns();
  renderHiddenRuns();

  try {
    const res = await fetch(`${BACKEND}/projects/unhide`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_folder: run.artifact_folder }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    debugLog('projects.unhide.ok', { folder: run.artifact_folder });
  } catch (err) {
    debugLog('projects.unhide.failed', { error: err && err.message });
    await loadRecentRunsFromBackend();
  }
}

function recordRecentRunFromResult(result, mode) {
  // Called after a fresh workflow completes so the new run appears immediately.
  if (!result || !result.artifact_folder) return;
  const name = result.artifact_folder.split(/[\\/]/).pop() || result.artifact_folder;
  const channel = mode === 'social' ? (currentRunMeta && currentRunMeta.channel) || '' : '';
  const newItem = {
    artifact_folder: result.artifact_folder,
    name,
    workflow: mode,
    channel,
    mtime_iso: new Date().toISOString(),
  };
  // Replace or prepend
  recentRuns = recentRuns.filter((r) => r.artifact_folder !== result.artifact_folder);
  recentRuns.unshift(newItem);
  activeRunFolder = result.artifact_folder;
  renderRecentRuns();
}

async function openProjectFromSidebar(run) {
  if (backendUp === false) {
    showError('Backend is not running. Start the FastAPI server first.');
    return;
  }
  try {
    const url = `${BACKEND}/projects/load?artifact_folder=${encodeURIComponent(run.artifact_folder)}`;
    const res = await fetch(url);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    const data = await res.json();
    let mode = 'business';
    if (data.workflow === 'social') mode = 'social';
    else if (data.workflow === 'agentic') mode = 'agentic';
    else if (data.workflow === 'notebooklm') mode = 'notebooklm';
    setMode(mode);
    activeRunFolder = data.artifact_folder;
    if (mode === 'social') currentRunMeta = parseSocialTaskMeta(data.task || '');
    else if (mode === 'agentic') currentRunMeta = parseAgenticTaskMeta(data.task || '');
    else if (mode === 'notebooklm') currentRunMeta = parseNotebookLMTaskMeta(data.task || '');
    else currentRunMeta = null;
    if (mode === 'social') renderSocialResults(data);
    else if (mode === 'agentic') renderAgenticResults(data);
    else if (mode === 'notebooklm') renderNotebookLMResults(data);
    else renderResults(data);
    renderRecentRuns(); // refresh active highlight
    debugLog('project.loaded', { folder: data.artifact_folder, workflow: data.workflow });
  } catch (err) {
    showError(err && err.message ? err.message : String(err));
  }
}

function parseSocialTaskMeta(taskText) {
  // task.txt for social workflows starts with: Channel: ... / Starting point: ... etc.
  const meta = { channel: '', starting_point: '', content_format: '', goal: '', output_depth: '', topic_notes: '' };
  const lines = (taskText || '').split(/\r?\n/);
  for (const line of lines) {
    const m = /^([A-Za-z ]+):\s*(.*)$/.exec(line);
    if (!m) continue;
    const key = m[1].trim().toLowerCase().replace(/\s+/g, '_');
    if (key in meta) meta[key] = m[2].trim();
  }
  // Topic notes might be after a "Topic notes:" header on its own line.
  const tIdx = taskText.indexOf('Topic notes:');
  if (tIdx >= 0) {
    meta.topic_notes = taskText.slice(tIdx + 'Topic notes:'.length).trim();
  }
  return meta;
}

function parseAgenticTaskMeta(taskText) {
  const meta = { time_window: '', output_depth: '', topic_focus: '' };
  const lines = (taskText || '').split(/\r?\n/);
  for (const line of lines) {
    const m = /^([A-Za-z ]+):\s*(.*)$/.exec(line);
    if (!m) continue;
    const key = m[1].trim().toLowerCase().replace(/\s+/g, '_');
    if (key in meta) meta[key] = m[2].trim();
  }
  const fIdx = taskText.indexOf('Topic focus');
  if (fIdx >= 0) {
    const after = taskText.slice(fIdx);
    const nl = after.indexOf('\n');
    if (nl >= 0) {
      meta.topic_focus = after.slice(nl + 1).split('\n\n')[0].trim();
    }
  }
  return meta;
}

function parseNotebookLMTaskMeta(taskText) {
  const meta = { subject: '', purpose: '', audience: '', output_type: '' };
  const lines = (taskText || '').split(/\r?\n/);
  for (const line of lines) {
    const m = /^([A-Za-z ]+):\s*(.*)$/.exec(line);
    if (!m) continue;
    const key = m[1].trim().toLowerCase().replace(/\s+/g, '_');
    if (key in meta) meta[key] = m[2].trim();
  }
  return meta;
}

/* ============================================================ */
/*               SIDEBAR — OUTPUT NAV + TAB LOGIC                */
/* ============================================================ */

function _tabsForMode(mode) {
  if (mode === 'social') return TABS_SOCIAL;
  if (mode === 'agentic') return TABS_AGENTIC;
  if (mode === 'notebooklm') return TABS_NOTEBOOKLM;
  return TABS_BUSINESS;
}

function _reviewChecklistForMode(mode) {
  if (mode === 'social') return REVIEW_CHECKLIST_SOCIAL;
  if (mode === 'agentic') return REVIEW_CHECKLIST_AGENTIC;
  if (mode === 'notebooklm') return REVIEW_CHECKLIST_NOTEBOOKLM;
  return REVIEW_CHECKLIST_BUSINESS;
}

function buildSidebarOutputNav(mode) {
  if (!els.sidebarOutputsList) return;
  const items = _tabsForMode(mode);
  currentResultTabs = items;
  els.sidebarOutputsList.innerHTML = '';
  items.forEach((item) => {
    const li = document.createElement('li');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'sidebar-list-item';
    btn.setAttribute('role', 'tab');
    btn.setAttribute('data-output-tab', item.panel);
    btn.setAttribute('aria-label', `Show ${item.label}`);
    btn.textContent = item.label;
    btn.addEventListener('click', () => showResultPanel(item.panel));
    li.appendChild(btn);
    els.sidebarOutputsList.appendChild(li);
  });
  renderReviewPublishChecklist(mode);
  showResultPanel('review-publish-card');
}

/* Build the Review & Publish list for the active mode. Resets all
 * checkbox state because a new run is starting. */
function renderReviewPublishChecklist(mode) {
  const list = document.getElementById('review-publish-list');
  if (!list) return;
  const items = _reviewChecklistForMode(mode);
  list.innerHTML = '';
  items.forEach((item) => {
    const li = document.createElement('li');
    li.className = 'review-item';
    li.dataset.itemId = item.id;

    const label = document.createElement('label');
    label.className = 'review-item-label';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'review-item-check';
    checkbox.id = `${item.id}-check`;
    checkbox.addEventListener('change', () => {
      li.classList.toggle('is-checked', checkbox.checked);
      updateReviewProgress();
    });

    const text = document.createElement('span');
    text.className = 'review-item-text';
    text.textContent = item.text;

    label.appendChild(checkbox);
    label.appendChild(text);
    li.appendChild(label);

    if (item.actionPanel) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-ghost btn-compact review-item-action';
      btn.textContent = item.actionLabel || 'Open';
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        showResultPanel(item.actionPanel);
      });
      li.appendChild(btn);
    }

    list.appendChild(li);
  });
  updateReviewProgress();
}

function updateReviewProgress() {
  const list = document.getElementById('review-publish-list');
  const text = document.getElementById('review-progress-text');
  const fill = document.getElementById('review-progress-fill');
  if (!list || !text || !fill) return;
  const boxes = list.querySelectorAll('.review-item-check');
  const total = boxes.length;
  const done = Array.from(boxes).filter((b) => b.checked).length;
  text.textContent = `${done} of ${total} reviewed`;
  fill.style.width = total ? `${Math.round((done / total) * 100)}%` : '0%';
}

function showResultPanel(panelId) {
  if (!currentResultTabs.length) return;
  audioStop();

  const valid = currentResultTabs.some((t) => t.panel === panelId);
  const target = valid ? panelId : currentResultTabs[0].panel;

  currentResultTabs.forEach((tab) => {
    const card = document.querySelector(`[data-result-panel="${tab.panel}"]`);
    if (card) card.classList.toggle('hidden', tab.panel !== target);
  });

  if (els.sidebarOutputsList) {
    els.sidebarOutputsList.querySelectorAll('[data-output-tab]').forEach((p) => {
      const isActive = p.getAttribute('data-output-tab') === target;
      p.classList.toggle('is-active', isActive);
      p.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });
  }

  const backNav = document.getElementById('panel-back-nav');
  if (backNav) {
    if (target === 'review-publish-card') hide(backNav);
    else show(backNav);
  }
}

/* ============================================================ */
/*                          RUN SUMMARY                          */
/* ============================================================ */

function _buildNextActionsHtml(mode) {
  // Contextual next-step chips. Each chip carries a data-next-action that
  // the run-summary click delegate maps to an existing handler. No new
  // model calls, no backend changes — just a friendly nudge toward the
  // most likely next step.
  const chips = [];
  if (mode === 'business') {
    chips.push({ id: 'review-doc', label: 'Review business document' });
    if (googleConnected) chips.push({ id: 'upload-drive', label: 'Upload to Google Drive' });
    chips.push({ id: 'review-email', label: 'Review draft email' });
  } else if (mode === 'agentic') {
    chips.push({ id: 'review-agentic', label: 'Review the brief' });
    chips.push({ id: 'email-agentic', label: 'Email brief to me' });
    if (googleConnected) chips.push({ id: 'upload-drive', label: 'Upload to Google Drive' });
  } else if (mode === 'notebooklm') {
    chips.push({ id: 'review-notebooklm', label: 'Review the package' });
    chips.push({ id: 'email-notebooklm', label: 'Email package to me' });
    if (googleConnected) chips.push({ id: 'upload-drive', label: 'Upload to Google Drive' });
  } else {
    chips.push({ id: 'review-script', label: 'Review the script' });
    chips.push({ id: 'review-caption', label: 'Review the caption' });
    chips.push({ id: 'review-visual', label: 'Review visual production' });
    if (googleConnected) chips.push({ id: 'upload-drive', label: 'Upload to Google Drive' });
  }
  const buttons = chips
    .map(
      (c) =>
        `<button type="button" class="run-next-chip" data-next-action="${c.id}">${escapeHtml(c.label)}</button>`
    )
    .join('');
  return `
    <div class="run-next">
      <span class="run-next-label">Next</span>
      <div class="run-next-chips">${buttons}</div>
    </div>
  `;
}

function _handleNextAction(action) {
  switch (action) {
    case 'review-doc':
      showResultPanel('business-document-card');
      break;
    case 'review-email':
      showResultPanel('draft-email-card');
      break;
    case 'review-script':
      showResultPanel('social-script-card');
      break;
    case 'review-caption':
      showResultPanel('social-caption-card');
      break;
    case 'review-visual':
      showResultPanel('social-visual-production-card');
      break;
    case 'review-agentic':
      showResultPanel('agentic-brief-card');
      break;
    case 'email-agentic':
      showResultPanel('agentic-brief-card');
      sendAgenticEmail();
      break;
    case 'review-notebooklm':
      showResultPanel('notebooklm-package-card');
      break;
    case 'email-notebooklm':
      showResultPanel('notebooklm-package-card');
      sendNotebookLMEmail();
      break;
    case 'upload-drive':
      // Show the Actions panel first so the operator sees the upload status
      // happen, then trigger the same flow the Actions card button does.
      showResultPanel('actions-card');
      uploadArtifactsToDrive();
      break;
    default:
      break;
  }
}

function _runAgainForMode(mode) {
  if (mode === 'social') return runSocialWorkflow();
  if (mode === 'agentic') return runAgenticAdvancesWorkflow();
  if (mode === 'notebooklm') return runNotebookLMWorkflow();
  return runWorkflow();
}

function updateRunSummary(mode, result) {
  if (!els.runSummary) return;
  let html = '';
  const folder = (result && result.artifact_folder) || '';
  if (mode === 'business') {
    const task = (result && result.task) || (els.taskInput && els.taskInput.value) || '';
    const taskPreview = task.slice(0, 360);
    html = `
      <div class="run-summary-head">
        <div class="run-summary-meta">
          <span class="run-summary-eyebrow">Business Workflow</span>
          <h2 class="run-summary-title">${escapeHtml(firstNonEmptyLine(task) || 'Business task')}</h2>
        </div>
        <div class="run-summary-actions">
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-edit">Edit task</button>
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-rerun">Run again</button>
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-new">New workflow</button>
        </div>
      </div>
      ${taskPreview ? `<div class="run-summary-task">${escapeHtml(taskPreview)}${task.length > 360 ? '…' : ''}</div>` : ''}
      ${_buildNextActionsHtml('business')}
      <div class="run-summary-folder">${escapeHtml(folder)}</div>
    `;
    updateWorkspaceHeader('Business Workflow', folderTail(folder));
  } else if (mode === 'agentic') {
    const meta = currentRunMeta || {};
    const badges = [
      meta.time_window && `<span class="run-summary-badge">${escapeHtml(meta.time_window)}</span>`,
      meta.output_depth && `<span class="run-summary-badge">${escapeHtml(meta.output_depth)}</span>`,
    ].filter(Boolean).join('');
    const focusPreview = (meta.topic_focus || '').slice(0, 360);
    html = `
      <div class="run-summary-head">
        <div class="run-summary-meta">
          <span class="run-summary-eyebrow">Agentic Advances Brief</span>
          <h2 class="run-summary-title">${escapeHtml(meta.topic_focus || 'Daily agentic AI brief')}</h2>
          <div class="run-summary-badges">${badges}</div>
        </div>
        <div class="run-summary-actions">
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-edit">Edit brief</button>
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-rerun">Run again</button>
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-new">New workflow</button>
        </div>
      </div>
      ${focusPreview ? `<div class="run-summary-task">${escapeHtml(focusPreview)}${(meta.topic_focus || '').length > 360 ? '…' : ''}</div>` : ''}
      ${_buildNextActionsHtml('agentic')}
      <div class="run-summary-folder">${escapeHtml(folder)}</div>
    `;
    updateWorkspaceHeader('Agentic Advances Brief', folderTail(folder));
  } else if (mode === 'notebooklm') {
    const meta = currentRunMeta || {};
    const badges = [
      meta.purpose && `<span class="run-summary-badge">${escapeHtml(meta.purpose)}</span>`,
      meta.audience && `<span class="run-summary-badge">${escapeHtml(meta.audience)}</span>`,
      meta.output_type && `<span class="run-summary-badge">${escapeHtml(meta.output_type)}</span>`,
    ].filter(Boolean).join('');
    const subjectPreview = (meta.subject || '').slice(0, 360);
    html = `
      <div class="run-summary-head">
        <div class="run-summary-meta">
          <span class="run-summary-eyebrow">NotebookLM Builder</span>
          <h2 class="run-summary-title">${escapeHtml(meta.subject || 'NotebookLM package')}</h2>
          <div class="run-summary-badges">${badges}</div>
        </div>
        <div class="run-summary-actions">
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-edit">Edit package</button>
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-rerun">Run again</button>
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-new">New workflow</button>
        </div>
      </div>
      ${subjectPreview ? `<div class="run-summary-task">${escapeHtml(subjectPreview)}</div>` : ''}
      ${_buildNextActionsHtml('notebooklm')}
      <div class="run-summary-folder">${escapeHtml(folder)}</div>
    `;
    updateWorkspaceHeader('NotebookLM Builder', folderTail(folder));
  } else {
    const meta = currentRunMeta || {};
    const badges = [
      meta.channel && `<span class="run-summary-badge">${escapeHtml(meta.channel)}</span>`,
      meta.starting_point && `<span class="run-summary-badge">${escapeHtml(meta.starting_point)}</span>`,
      meta.content_format && `<span class="run-summary-badge">${escapeHtml(meta.content_format)}</span>`,
      meta.output_depth && `<span class="run-summary-badge">${escapeHtml(meta.output_depth)}</span>`,
      meta.goal && `<span class="run-summary-badge">${escapeHtml(meta.goal)}</span>`,
    ].filter(Boolean).join('');
    const topicPreview = (meta.topic_notes || '').slice(0, 360);
    const imageIndicator = thumbnailData && thumbnailData.name
      ? `<div class="run-summary-image-used">Image used: ${escapeHtml(thumbnailData.name)}</div>`
      : '';
    html = `
      <div class="run-summary-head">
        <div class="run-summary-meta">
          <span class="run-summary-eyebrow">Social Media Production</span>
          <h2 class="run-summary-title">${escapeHtml(meta.channel || 'Social media run')}</h2>
          <div class="run-summary-badges">${badges}</div>
          ${imageIndicator}
        </div>
        <div class="run-summary-actions">
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-edit">Edit task</button>
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-rerun">Run again</button>
          <button type="button" class="btn btn-ghost btn-compact" id="run-summary-new">New workflow</button>
        </div>
      </div>
      ${topicPreview ? `<div class="run-summary-task">${escapeHtml(topicPreview)}${(meta.topic_notes || '').length > 360 ? '…' : ''}</div>` : ''}
      ${_buildNextActionsHtml('social')}
      <div class="run-summary-folder">${escapeHtml(folder)}</div>
    `;
    updateWorkspaceHeader('Social Media Production', meta.channel || folderTail(folder));
  }
  els.runSummary.innerHTML = html;

  const editBtn = document.getElementById('run-summary-edit');
  const rerunBtn = document.getElementById('run-summary-rerun');
  const newBtn = document.getElementById('run-summary-new');
  if (editBtn) editBtn.addEventListener('click', () => setWorkspaceView('input'));
  if (rerunBtn) rerunBtn.addEventListener('click', () => _runAgainForMode(mode));
  if (newBtn) newBtn.addEventListener('click', () => startNewWorkflow(mode));

  // Delegated handler for the next-action chips.
  els.runSummary.querySelectorAll('[data-next-action]').forEach((btn) => {
    btn.addEventListener('click', () => _handleNextAction(btn.getAttribute('data-next-action')));
  });
}

function firstNonEmptyLine(s) {
  return (s || '').split(/\r?\n/).map((x) => x.trim()).find(Boolean) || '';
}

function folderTail(folder) {
  if (!folder) return '';
  const parts = folder.split(/[\\/]/);
  return parts[parts.length - 1] || folder;
}

/* ============================================================ */
/*                    NEW WORKFLOW WIZARD                        */
/* ============================================================ */

const PLATFORM_CHANNEL_MAP = {
  'TikTok': 'Open Gulf TikTok',
  'YouTube': 'Open Gulf YouTube',
  'Instagram': 'Open Gulf Instagram',
  'LinkedIn': 'Open Gulf LinkedIn',
  'X / Twitter': 'Open Gulf X / Twitter',
};

function _cleanPrompt(text) {
  return text
    .replace(/([.?!])\s*\./g, '$1')   // "?." → "?", ".." → ".", "!." → "!"
    .replace(/\.\s*([?!])/g, '$1')     // ".?" → "?"
    .replace(/([.?!])\1+/g, '$1')      // "..." → ".", "??" → "?"
    .replace(/\s{2,}/g, ' ')           // collapse multiple spaces
    .trim();
}

const WIZARD_FIELDS = {
  research: {
    mode: 'business',
    fields: [
      { id: 'market', label: 'What market, business type, or opportunity should we research?', type: 'text', placeholder: 'e.g. AI tutoring for K-12 schools' },
      { id: 'audience', label: 'What location or audience should we focus on?', type: 'text', placeholder: 'e.g. Gulf Coast small businesses' },
      { id: 'decision', label: 'What decision are you trying to make?', type: 'text', placeholder: 'e.g. Should I launch a consulting offer for this market?' },
      { id: 'focus', label: 'Any special focus?', type: 'text', placeholder: 'e.g. pricing models, competitor landscape', optional: true },
    ],
    build(v) {
      let t = `Research practical opportunities for ${v.market || 'this market'}`;
      if (v.audience) t += `, focused on ${v.audience}`;
      t += '.';
      if (v.decision) t += ` The key decision: ${v.decision}`;
      if (v.focus) t += ` Special focus: ${v.focus}`;
      t += ' Summarize the market, pain points, opportunities, recommended next steps, a slide outline, and a draft outreach email.';
      return { task: _cleanPrompt(t) };
    },
  },
  proposal: {
    mode: 'business',
    fields: [
      { id: 'client', label: 'Who is the proposal or document for?', type: 'text', placeholder: 'e.g. Bay Area Chamber of Commerce' },
      { id: 'problem', label: 'What problem or opportunity should it address?', type: 'text', placeholder: 'e.g. They need help using AI to streamline member communications' },
      { id: 'solution', label: 'What service or solution are you proposing?', type: 'text', placeholder: 'e.g. A 3-month AI workflow integration package' },
      { id: 'tone', label: 'What tone should it use?', type: 'select', options: ['Concise', 'Warm', 'Formal', 'Persuasive'] },
    ],
    build(v) {
      let t = `Create a concise client-ready proposal for ${v.client || 'the client'}`;
      if (v.problem) t += ` addressing ${v.problem}`;
      t += '.';
      if (v.solution) t += ` The proposed solution: ${v.solution}`;
      t += ` Tone: ${v.tone || 'concise'}.`;
      t += ' Include context, problem, recommendation, deliverables, timeline, and next steps.';
      return { task: _cleanPrompt(t) };
    },
  },
  email: {
    mode: 'business',
    fields: [
      { id: 'recipient', label: 'Who is the email going to?', type: 'text', placeholder: 'e.g. Sarah Kim, director of partnerships at Gulf Innovations' },
      { id: 'purpose', label: 'What is the purpose of the email?', type: 'text', placeholder: 'e.g. Follow up after our meeting about AI workflow consulting' },
      { id: 'cta', label: 'What should the recipient do next?', type: 'text', placeholder: 'e.g. Schedule a 15-minute call next week' },
      { id: 'tone', label: 'Tone', type: 'select', options: ['Warm', 'Concise', 'Professional', 'Friendly'] },
    ],
    build(v) {
      let t = `Draft a professional outreach or follow-up email to ${v.recipient || 'the recipient'}`;
      if (v.purpose) t += ` about ${v.purpose}`;
      t += '.';
      if (v.cta) t += ` The recipient should: ${v.cta}`;
      t += ` Tone: ${v.tone || 'warm'}.`;
      t += ' Keep it concise, specific, and action-oriented.';
      return { task: _cleanPrompt(t) };
    },
  },
  'social-post': {
    mode: 'social',
    fields: [
      { id: 'platform', label: 'Which platform?', type: 'select', options: ['TikTok', 'YouTube', 'Instagram', 'LinkedIn', 'X / Twitter'] },
      { id: 'topic', label: 'What is the topic or idea?', type: 'text', placeholder: 'e.g. Why educators should treat AI as a thinking partner, not a search box' },
      { id: 'audience', label: 'Who is the audience?', type: 'text', placeholder: 'e.g. Teachers and education professionals' },
      { id: 'goal', label: 'Goal', type: 'select', options: ['Educate', 'Inspire curiosity', 'Build trust', 'Promote a service', 'Grow audience'] },
    ],
    build(v) {
      const channel = PLATFORM_CHANNEL_MAP[v.platform] || 'Open Gulf TikTok';
      const isVideo = ['TikTok', 'YouTube'].includes(v.platform);
      let notes = v.topic || '';
      if (v.audience) notes += (notes ? '. ' : '') + `Audience: ${v.audience}`;
      return {
        social: {
          channel,
          starting_point: 'I have a topic',
          content_format: isVideo ? 'Short-form video' : 'Caption/post only',
          goal: (v.goal || 'Educate').replace('Inspire curiosity', 'Inspire').replace('Build trust', 'Build trust').replace('Promote a service', 'Promote').replace('Grow audience', 'Grow audience'),
          output_depth: 'Quick post package',
          topic_notes: _cleanPrompt(notes),
          media_notes: '',
        },
      };
    },
  },
  'weekly-plan': {
    mode: 'social',
    fields: [
      { id: 'theme', label: 'What is the weekly theme?', type: 'text', placeholder: 'e.g. How AI helps small businesses save 5 hours per week' },
      { id: 'platforms', label: 'Which platforms should be included?', type: 'text', placeholder: 'e.g. TikTok, YouTube, Instagram, LinkedIn, X / Twitter' },
      { id: 'audience', label: 'Who is the audience?', type: 'text', placeholder: 'e.g. Entrepreneurs, educators, and solo professionals' },
      { id: 'goal', label: 'What is the main goal?', type: 'select', options: ['Grow audience', 'Educate', 'Build trust', 'Drive traffic', 'Test ideas'] },
    ],
    build(v) {
      let notes = `Create a 7-day Open Gulf content plan`;
      if (v.theme) notes += ` around the theme: ${v.theme}`;
      notes += '.';
      if (v.platforms) notes += ` Platforms: ${v.platforms}`;
      else notes += ' Platforms: TikTok, YouTube, Instagram, LinkedIn, and X / Twitter.';
      if (v.audience) notes += ` Audience: ${v.audience}`;
      if (v.goal) notes += ` Main goal: ${v.goal.toLowerCase()}`;
      return {
        social: {
          channel: 'Custom',
          starting_point: 'Generate ideas from scratch',
          content_format: 'Content calendar',
          goal: v.goal || 'Grow audience',
          output_depth: 'Weekly content plan',
          topic_notes: _cleanPrompt(notes + '.'),
          media_notes: '',
        },
      };
    },
  },
  repurpose: {
    mode: 'social',
    fields: [
      { id: 'footage', label: 'Describe the footage, photo, or clip.', type: 'textarea', placeholder: 'e.g. A 45-second clip of me explaining how AI helps plan my week, filmed at my desk with natural light' },
      { id: 'platform', label: 'Which platform should this be for?', type: 'select', options: ['TikTok', 'YouTube', 'Instagram', 'LinkedIn', 'X / Twitter'] },
      { id: 'feeling', label: 'What should viewers feel or do after watching?', type: 'text', placeholder: 'e.g. Feel curious enough to try AI tools this week' },
      { id: 'message', label: 'Any important message to include?', type: 'text', placeholder: 'e.g. AI saves me 5 hours a week', optional: true },
    ],
    build(v) {
      const channel = PLATFORM_CHANNEL_MAP[v.platform] || 'Custom';
      let notes = '';
      if (v.feeling) notes += `Viewer takeaway: ${v.feeling}`;
      if (v.message) notes += (notes ? '. ' : '') + `Key message: ${v.message}`;
      return {
        social: {
          channel,
          starting_point: 'I have existing footage or a thumbnail',
          content_format: 'Repurposed clip',
          goal: 'Grow audience',
          output_depth: 'Full production package',
          topic_notes: _cleanPrompt(notes ? notes + '.' : ''),
          media_notes: v.footage || '',
        },
      };
    },
  },
  presentation: {
    mode: 'business',
    fields: [
      { id: 'audience', label: 'Who is the audience?', type: 'text', placeholder: 'e.g. Chamber of Commerce members, small business owners' },
      { id: 'topic', label: 'What is the topic?', type: 'text', placeholder: 'e.g. How to use AI to save 5 hours per week in your business' },
      { id: 'length', label: 'How long is the session?', type: 'text', placeholder: 'e.g. 45 minutes with 15 minutes for Q&A' },
      { id: 'outcome', label: 'What should participants be able to do afterward?', type: 'text', placeholder: 'e.g. Set up one AI-powered workflow for their business' },
    ],
    build(v) {
      let t = `Create a practical workshop or presentation package`;
      if (v.audience) t += ` for ${v.audience}`;
      if (v.topic) t += ` on ${v.topic}`;
      t += '.';
      if (v.length) t += ` Session length: ${v.length}`;
      if (v.outcome) t += ` Participants should be able to: ${v.outcome}`;
      t += ' Include learning goals, agenda, talking points, slide outline, audience engagement ideas, and a follow-up email.';
      return { task: _cleanPrompt(t) };
    },
  },
};

let wizardStep = 1;
let wizardSelectedKey = null;

function openWizard() {
  const modal = document.getElementById('wizard-modal');
  if (!modal) return;
  _wizardShowStep(1);
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  document.addEventListener('keydown', _handleWizardKeydown);
  const first = modal.querySelector('.wizard-option');
  if (first) first.focus();
}

function closeWizard() {
  const modal = document.getElementById('wizard-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  document.removeEventListener('keydown', _handleWizardKeydown);
  wizardStep = 1;
  wizardSelectedKey = null;
}

function _handleWizardKeydown(e) {
  if (e.key === 'Escape') { e.preventDefault(); closeWizard(); }
}

function _wizardShowStep(step) {
  wizardStep = step;
  const step1 = document.getElementById('wizard-step1');
  const step2 = document.getElementById('wizard-step2');
  const heading = document.getElementById('wizard-heading');
  const footer = document.getElementById('wizard-footer');
  if (!step1 || !step2 || !footer) return;

  if (step === 1) {
    show(step1); hide(step2);
    if (heading) heading.textContent = 'What would you like to create?';
    footer.innerHTML = `
      <button id="wizard-blank-btn" type="button" class="btn btn-ghost">Start blank</button>
      <span class="modal-foot-spacer"></span>
      <button id="wizard-cancel-btn" type="button" class="btn btn-ghost">Cancel</button>
    `;
    footer.querySelector('#wizard-blank-btn').addEventListener('click', () => { closeWizard(); startNewWorkflow(currentMode); });
    footer.querySelector('#wizard-cancel-btn').addEventListener('click', closeWizard);
  } else {
    hide(step1); show(step2);
    const cfg = WIZARD_FIELDS[wizardSelectedKey];
    if (heading && cfg) {
      const titles = { research: 'Business research package', proposal: 'Client proposal', email: 'Outreach or follow-up email', 'social-post': 'Social media post', 'weekly-plan': 'Weekly content plan', repurpose: 'Repurpose footage', presentation: 'Presentation or workshop' };
      heading.textContent = titles[wizardSelectedKey] || 'Details';
    }
    _wizardRenderFields();
    footer.innerHTML = `
      <button id="wizard-back-btn" type="button" class="btn btn-ghost">Back</button>
      <span class="modal-foot-spacer"></span>
      <button id="wizard-create-btn" type="button" class="btn btn-primary">Create draft</button>
    `;
    footer.querySelector('#wizard-back-btn').addEventListener('click', () => _wizardShowStep(1));
    footer.querySelector('#wizard-create-btn').addEventListener('click', _wizardCreateDraft);
  }
}

function _wizardRenderFields() {
  const container = document.getElementById('wizard-fields');
  if (!container) return;
  container.innerHTML = '';
  const cfg = WIZARD_FIELDS[wizardSelectedKey];
  if (!cfg) return;

  cfg.fields.forEach((f) => {
    const group = document.createElement('div');
    group.className = 'wizard-field-group';

    const label = document.createElement('label');
    label.className = 'wizard-field-label';
    label.setAttribute('for', `wiz-${f.id}`);
    label.textContent = f.label;
    if (f.optional) {
      const opt = document.createElement('span');
      opt.className = 'wizard-field-optional';
      opt.textContent = ' (optional)';
      label.appendChild(opt);
    }
    group.appendChild(label);

    let input;
    if (f.type === 'select') {
      input = document.createElement('select');
      input.className = 'wizard-field-select';
      (f.options || []).forEach((o) => {
        const option = document.createElement('option');
        option.value = o;
        option.textContent = o;
        input.appendChild(option);
      });
    } else if (f.type === 'textarea') {
      input = document.createElement('textarea');
      input.className = 'wizard-field-textarea';
      input.rows = 3;
      if (f.placeholder) input.placeholder = f.placeholder;
    } else {
      input = document.createElement('input');
      input.type = 'text';
      input.className = 'wizard-field-input';
      if (f.placeholder) input.placeholder = f.placeholder;
    }
    input.id = `wiz-${f.id}`;
    input.name = f.id;
    input.autocomplete = 'off';
    input.spellcheck = false;
    group.appendChild(input);
    container.appendChild(group);
  });

  const firstInput = container.querySelector('input, textarea, select');
  if (firstInput) setTimeout(() => firstInput.focus(), 60);
}

function _wizardGatherValues() {
  const cfg = WIZARD_FIELDS[wizardSelectedKey];
  if (!cfg) return {};
  const vals = {};
  cfg.fields.forEach((f) => {
    const el = document.getElementById(`wiz-${f.id}`);
    vals[f.id] = el ? el.value.trim() : '';
  });
  return vals;
}

function _wizardCreateDraft() {
  const cfg = WIZARD_FIELDS[wizardSelectedKey];
  if (!cfg) return;
  const vals = _wizardGatherValues();
  const result = cfg.build(vals);
  closeWizard();

  if (cfg.mode === 'business') {
    if (els.taskInput) els.taskInput.value = result.task || '';
    setMode('business');
    setWorkspaceView('input');
    if (els.taskInput) els.taskInput.focus();
  } else if (cfg.mode === 'social') {
    clearSocialFormValuesOnly();
    const s = result.social || {};
    if (s.channel && els.socialChannel) els.socialChannel.value = s.channel;
    if (s.starting_point && els.socialStartingPoint) els.socialStartingPoint.value = s.starting_point;
    if (s.content_format && els.socialContentFormat) els.socialContentFormat.value = s.content_format;
    if (s.goal && els.socialGoal) els.socialGoal.value = s.goal;
    if (s.output_depth && els.socialOutputDepth) els.socialOutputDepth.value = s.output_depth;
    if (els.socialTopicNotes) els.socialTopicNotes.value = s.topic_notes || '';
    if (els.socialMediaNotes) els.socialMediaNotes.value = s.media_notes || '';
    setMode('social');
    setWorkspaceView('input');
    if (els.socialTopicNotes) els.socialTopicNotes.focus({ preventScroll: true });
  }
  currentResult = null;
  currentRunMeta = null;
  activeRunFolder = null;
  renderRecentRuns();
}

function _wizardSelectOption(key) {
  if (!WIZARD_FIELDS[key]) return;
  wizardSelectedKey = key;
  _wizardShowStep(2);
}

function startNewWorkflow(mode) {
  audioStop();
  const targetMode = mode || currentMode;
  if (targetMode === 'business') {
    if (els.taskInput) els.taskInput.value = '';
  } else if (targetMode === 'social') {
    clearSocialFormValuesOnly();
  } else if (targetMode === 'agentic') {
    clearAgenticFormValuesOnly();
  } else if (targetMode === 'notebooklm') {
    clearNotebookLMFormValuesOnly();
  }
  currentResult = null;
  currentRunMeta = null;
  activeRunFolder = null;
  renderRecentRuns();
  setMode(targetMode);
  setWorkspaceView('input');
}

function clearAgenticFormValuesOnly() {
  if (els.agenticTimeWindow) els.agenticTimeWindow.value = 'Last 7 days';
  if (els.agenticOutputDepth) els.agenticOutputDepth.value = 'Strategic brief';
  if (els.agenticTopicFocus) els.agenticTopicFocus.value = '';
}

function clearNotebookLMFormValuesOnly() {
  if (els.notebooklmSubject) els.notebooklmSubject.value = '';
  if (els.notebooklmPurpose) els.notebooklmPurpose.value = 'Learn';
  if (els.notebooklmAudience) els.notebooklmAudience.value = 'Ryan';
  if (els.notebooklmOutputType) els.notebooklmOutputType.value = 'Full NotebookLM package';
  if (els.notebooklmNotes) els.notebooklmNotes.value = '';
}

function clearSocialFormValuesOnly() {
  if (els.socialMediaNotes) els.socialMediaNotes.value = '';
  if (els.socialTopicNotes) els.socialTopicNotes.value = '';
  if (els.socialChannel) els.socialChannel.value = 'Open Gulf TikTok';
  if (els.socialStartingPoint) els.socialStartingPoint.value = 'I have a topic';
  if (els.socialContentFormat) els.socialContentFormat.value = 'Short-form video';
  if (els.socialGoal) els.socialGoal.value = 'Educate';
  if (els.socialOutputDepth) els.socialOutputDepth.value = 'Quick post package';
  clearThumbnail();
}

function clearThumbnail() {
  thumbnailData = null;
  if (els.thumbnailFileInput) els.thumbnailFileInput.value = '';
  if (els.thumbnailFilename) {
    els.thumbnailFilename.textContent = 'No image selected';
    els.thumbnailFilename.classList.remove('has-file');
  }
  if (els.thumbnailClearBtn) hide(els.thumbnailClearBtn);
}

function handleThumbnailSelect(file) {
  if (!file) return;
  const allowed = /\.(png|jpe?g|webp)$/i;
  if (!allowed.test(file.name)) {
    alert('Unsupported image format. Please use PNG, JPG, or WEBP.');
    return;
  }
  if (file.size > THUMBNAIL_MAX_BYTES) {
    alert('Image is too large (max 5 MB). Please choose a smaller file.');
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    thumbnailData = { name: file.name, dataUri: reader.result };
    if (els.thumbnailFilename) {
      els.thumbnailFilename.textContent = file.name;
      els.thumbnailFilename.classList.add('has-file');
    }
    if (els.thumbnailClearBtn) show(els.thumbnailClearBtn);
  };
  reader.onerror = () => {
    alert('Could not read the image file. Please try again.');
  };
  reader.readAsDataURL(file);
}

/* ============================================================ */
/*                         RUN HANDLERS                          */
/* ============================================================ */

function setRunning(isRunning) {
  if (els.runBtn) els.runBtn.disabled = isRunning;
  if (els.clearBtn) els.clearBtn.disabled = isRunning;
  if (els.exampleBtn) els.exampleBtn.disabled = isRunning;
  if (els.runBtn) els.runBtn.textContent = isRunning ? 'Running…' : 'Run workflow';
}

let elapsedTimer = null;
function startElapsed() {
  const t0 = Date.now();
  if (els.elapsed) els.elapsed.textContent = '0s';
  elapsedTimer = setInterval(() => {
    if (els.elapsed) els.elapsed.textContent = `${Math.floor((Date.now() - t0) / 1000)}s`;
  }, 1000);
}
function stopElapsed() {
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = null;
}

function setRunningStatusForMode(mode) {
  if (!els.statusSub) return;
  if (mode === 'social') {
    els.statusSub.innerHTML = 'This typically takes <strong>30&ndash;90 seconds</strong>. The social media agent builds a five-section package: content, script, caption, posting checklist, visual production.';
  } else if (mode === 'agentic') {
    els.statusSub.innerHTML = 'This typically takes <strong>60&ndash;180 seconds</strong>. The analyst is running live web searches across the agentic AI ecosystem and citing sources.';
  } else if (mode === 'notebooklm') {
    els.statusSub.innerHTML = 'This typically takes <strong>20&ndash;60 seconds</strong>. The builder is composing the source prompt, Audio Overview prompt, and study-guide prompts.';
  } else {
    els.statusSub.innerHTML = 'This typically takes <strong>60&ndash;90 seconds</strong>. The five agents run in sequence: research &rarr; writer &rarr; reviewer &rarr; presentation &rarr; email.';
  }
}

function showError(msg) {
  if (els.errorMessage) els.errorMessage.textContent = msg || '';
  show(els.errorRegion);
}

async function runWorkflow() {
  const task = els.taskInput.value.trim();
  if (task.length < 10) { showError('Please describe the task in at least 10 characters.'); return; }
  if (backendUp === false) { showError('Backend is not running. Start the FastAPI server first.'); return; }
  if (openaiKeyConfigured === false) { showError('OpenAI API key is not configured. Open Settings to add your key.'); return; }

  hide(els.errorRegion);
  setRunning(true);
  setRunningStatusForMode('business');
  show(els.status);
  hide(els.viewInputBusiness);
  hide(els.viewInputSocial);
  hide(els.viewRun);
  startElapsed();

  try {
    const res = await fetch(`${BACKEND}/workflows/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    data.task = task; // attach so run summary can show it
    setMode('business');
    renderResults(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      showError('Backend is not reachable. Start the FastAPI server first.');
      setBackendStatus(false);
    } else { showError(msg); }
    setWorkspaceView('input');
  } finally {
    setRunning(false);
    hide(els.status);
    stopElapsed();
  }
}

async function runSocialWorkflow() {
  const payload = {
    channel: (els.socialChannel && els.socialChannel.value) || '',
    starting_point: (els.socialStartingPoint && els.socialStartingPoint.value) || '',
    content_format: (els.socialContentFormat && els.socialContentFormat.value) || '',
    media_notes: (els.socialMediaNotes && els.socialMediaNotes.value) || '',
    topic_notes: (els.socialTopicNotes && els.socialTopicNotes.value) || '',
    goal: (els.socialGoal && els.socialGoal.value) || '',
    output_depth: (els.socialOutputDepth && els.socialOutputDepth.value) || '',
  };
  if (thumbnailData && thumbnailData.dataUri) {
    payload.image_data = thumbnailData.dataUri;
  }
  if (!payload.channel) { showError('Choose a Channel / Brand before running the social workflow.'); return; }
  if (backendUp === false) { showError('Backend is not running.'); return; }
  if (openaiKeyConfigured === false) { showError('OpenAI API key is not configured. Open Settings to add your key.'); return; }

  // Capture meta now so the run summary has it after completion
  currentRunMeta = { ...payload };

  hide(els.errorRegion);
  if (els.socialRunBtn) { els.socialRunBtn.disabled = true; els.socialRunBtn.textContent = 'Running…'; }
  if (els.socialClearBtn) els.socialClearBtn.disabled = true;
  setRunningStatusForMode('social');
  show(els.status);
  hide(els.viewInputBusiness);
  hide(els.viewInputSocial);
  hide(els.viewRun);
  startElapsed();

  try {
    const res = await fetch(`${BACKEND}/workflows/social-media/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    setMode('social');
    renderSocialResults(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      showError('Backend is not reachable.');
      setBackendStatus(false);
    } else { showError(msg); }
    setWorkspaceView('input');
  } finally {
    if (els.socialRunBtn) { els.socialRunBtn.disabled = false; els.socialRunBtn.textContent = 'Run social workflow'; }
    if (els.socialClearBtn) els.socialClearBtn.disabled = false;
    hide(els.status);
    stopElapsed();
  }
}

async function runAgenticAdvancesWorkflow() {
  const payload = {
    topic_focus: (els.agenticTopicFocus && els.agenticTopicFocus.value) || '',
    time_window: (els.agenticTimeWindow && els.agenticTimeWindow.value) || 'Last 7 days',
    output_depth: (els.agenticOutputDepth && els.agenticOutputDepth.value) || 'Strategic brief',
  };
  if (backendUp === false) { showError('Backend is not running.'); return; }
  if (openaiKeyConfigured === false) { showError('OpenAI API key is not configured. Open Settings to add your key.'); return; }

  currentRunMeta = { ...payload };

  hide(els.errorRegion);
  if (els.agenticRunBtn) { els.agenticRunBtn.disabled = true; els.agenticRunBtn.textContent = 'Running…'; }
  if (els.agenticClearBtn) els.agenticClearBtn.disabled = true;
  setRunningStatusForMode('agentic');
  show(els.status);
  hide(els.viewInputBusiness);
  hide(els.viewInputSocial);
  hide(els.viewInputAgentic);
  hide(els.viewInputNotebooklm);
  hide(els.viewRun);
  startElapsed();

  try {
    const res = await fetch(`${BACKEND}/workflows/agentic-advances/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    setMode('agentic');
    renderAgenticResults(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      showError('Backend is not reachable.');
      setBackendStatus(false);
    } else { showError(msg); }
    setWorkspaceView('input');
  } finally {
    if (els.agenticRunBtn) { els.agenticRunBtn.disabled = false; els.agenticRunBtn.textContent = 'Run Agentic Advances brief'; }
    if (els.agenticClearBtn) els.agenticClearBtn.disabled = false;
    hide(els.status);
    stopElapsed();
  }
}

async function runNotebookLMWorkflow() {
  const subject = (els.notebooklmSubject && els.notebooklmSubject.value || '').trim();
  if (subject.length < 3) {
    showError('Please give the NotebookLM package a subject (at least 3 characters).');
    return;
  }
  const payload = {
    subject,
    purpose: (els.notebooklmPurpose && els.notebooklmPurpose.value) || 'Learn',
    audience: (els.notebooklmAudience && els.notebooklmAudience.value) || 'Ryan',
    output_type: (els.notebooklmOutputType && els.notebooklmOutputType.value) || 'Full NotebookLM package',
    notes: (els.notebooklmNotes && els.notebooklmNotes.value) || '',
  };
  if (backendUp === false) { showError('Backend is not running.'); return; }
  if (openaiKeyConfigured === false) { showError('OpenAI API key is not configured. Open Settings to add your key.'); return; }

  currentRunMeta = { ...payload };

  hide(els.errorRegion);
  if (els.notebooklmRunBtn) { els.notebooklmRunBtn.disabled = true; els.notebooklmRunBtn.textContent = 'Running…'; }
  if (els.notebooklmClearBtn) els.notebooklmClearBtn.disabled = true;
  setRunningStatusForMode('notebooklm');
  show(els.status);
  hide(els.viewInputBusiness);
  hide(els.viewInputSocial);
  hide(els.viewInputAgentic);
  hide(els.viewInputNotebooklm);
  hide(els.viewRun);
  startElapsed();

  try {
    const res = await fetch(`${BACKEND}/workflows/notebooklm/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { const j = await res.json(); if (j && j.detail) detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch (_) {}
      throw new Error(detail);
    }
    const data = await res.json();
    setMode('notebooklm');
    renderNotebookLMResults(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      showError('Backend is not reachable.');
      setBackendStatus(false);
    } else { showError(msg); }
    setWorkspaceView('input');
  } finally {
    if (els.notebooklmRunBtn) { els.notebooklmRunBtn.disabled = false; els.notebooklmRunBtn.textContent = 'Build NotebookLM package'; }
    if (els.notebooklmClearBtn) els.notebooklmClearBtn.disabled = false;
    hide(els.status);
    stopElapsed();
  }
}

/* ============================================================ */
/*                       RENDER RESULTS                          */
/* ============================================================ */

function _hideAllResultsModes() {
  hide(els.resultsBusiness);
  hide(els.resultsSocial);
  hide(els.resultsAgentic);
  hide(els.resultsNotebooklm);
}

function renderResults(result) {
  currentResult = result;
  document.querySelector('[data-field="artifact_folder"]').textContent = result.artifact_folder;
  document.querySelector('[data-field="research_summary"]').innerHTML = renderMarkdown(result.research_summary);
  document.querySelector('[data-field="business_document"]').innerHTML = renderMarkdown(result.business_document);
  document.querySelector('[data-field="slide_outline"]').innerHTML = renderMarkdown(result.slide_outline);
  document.querySelector('[data-field="draft_email"]').innerHTML = renderMarkdown(result.draft_email);

  _hideAllResultsModes();
  show(els.resultsBusiness);
  resetEmailStatus();
  resetActionsStatus();
  _resetAllAudioStatuses();

  setMode('business');
  recordRecentRunFromResult(result, 'business');
  updateRunSummary('business', result);
  setWorkspaceView('run');
  buildSidebarOutputNav('business');
}

function renderSocialResults(result) {
  currentResult = result;
  document.querySelector('[data-field="artifact_folder"]').textContent = result.artifact_folder;
  document.querySelector('[data-field="content_package"]').innerHTML = renderMarkdown(result.content_package);
  document.querySelector('[data-field="script"]').innerHTML = renderMarkdown(result.script);
  document.querySelector('[data-field="caption_package"]').innerHTML = renderMarkdown(result.caption_package);
  document.querySelector('[data-field="posting_checklist"]').innerHTML = renderMarkdown(result.posting_checklist);
  const vpEl = document.querySelector('[data-field="visual_production"]');
  if (vpEl) vpEl.innerHTML = renderMarkdown(result.visual_production || '');

  _hideAllResultsModes();
  show(els.resultsSocial);
  resetEmailStatus();
  resetActionsStatus();
  _resetAllAudioStatuses();

  setMode('social');
  recordRecentRunFromResult(result, 'social');
  updateRunSummary('social', result);
  setWorkspaceView('run');
  buildSidebarOutputNav('social');
}

function renderAgenticResults(result) {
  currentResult = result;
  document.querySelector('[data-field="artifact_folder"]').textContent = result.artifact_folder;
  const briefEl = document.querySelector('[data-field="agentic_advances_brief"]');
  if (briefEl) briefEl.innerHTML = renderMarkdown(result.agentic_advances_brief || '');

  _hideAllResultsModes();
  show(els.resultsAgentic);
  // Reveal the single agentic result card so showResultPanel can flip it later.
  const card = document.getElementById('agentic-brief-card');
  if (card) card.classList.remove('hidden');
  resetAgenticEmailStatus();
  resetActionsStatus();
  _resetAllAudioStatuses();

  setMode('agentic');
  recordRecentRunFromResult(result, 'agentic');
  updateRunSummary('agentic', result);
  setWorkspaceView('run');
  buildSidebarOutputNav('agentic');
}

function renderNotebookLMResults(result) {
  currentResult = result;
  document.querySelector('[data-field="artifact_folder"]').textContent = result.artifact_folder;
  const pkgEl = document.querySelector('[data-field="notebooklm_package"]');
  if (pkgEl) pkgEl.innerHTML = renderMarkdown(result.notebooklm_package || '');

  _hideAllResultsModes();
  show(els.resultsNotebooklm);
  const card = document.getElementById('notebooklm-package-card');
  if (card) card.classList.remove('hidden');
  resetNotebookLMEmailStatus();
  resetActionsStatus();
  _resetAllAudioStatuses();

  setMode('notebooklm');
  recordRecentRunFromResult(result, 'notebooklm');
  updateRunSummary('notebooklm', result);
  setWorkspaceView('run');
  buildSidebarOutputNav('notebooklm');
}

/* ============================================================ */
/*                  COPY / ACTIONS / AUDIO                       */
/* ============================================================ */

function wireCopyButtons() {
  document.querySelectorAll('.copy-btn').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!currentResult) return;
      const key = btn.getAttribute('data-target');
      const value = currentResult[key];
      if (typeof value !== 'string') return;
      const ok = await copyToClipboard(value);
      const original = btn.textContent;
      btn.textContent = ok ? 'Copied' : 'Copy failed';
      btn.classList.toggle('is-copied', ok);
      setTimeout(() => { btn.textContent = original; btn.classList.remove('is-copied'); }, 1400);
    });
  });
}

function writeStatus(el, text, kind) {
  if (!el) return;
  el.textContent = text || '';
  el.classList.remove('is-ok', 'is-err');
  if (kind === 'ok') el.classList.add('is-ok');
  if (kind === 'err') el.classList.add('is-err');
}

function setActionsStatus(text, kind) { writeStatus(els.actionsStatus, text, kind); }

function resetActionsStatus() {
  setActionsStatus('');
  document.querySelectorAll('.card-action-status').forEach((el) => writeStatus(el, ''));
  hideDriveSuccessCard();
}

function hideDriveSuccessCard() {
  if (els.driveSuccessCard) els.driveSuccessCard.classList.add('hidden');
  if (els.actionDriveLink) {
    els.actionDriveLink.removeAttribute('href');
  }
  if (els.driveSuccessCopyStatus) els.driveSuccessCopyStatus.textContent = '';
}

function showDriveSuccessCard(data) {
  if (!els.driveSuccessCard) return;
  const count = (data.uploaded_files || []).length;
  if (els.driveSuccessCount) {
    els.driveSuccessCount.textContent = `${count} file${count === 1 ? '' : 's'} uploaded`;
  }
  if (els.driveSuccessPath) {
    const path = data.drive_path || data.drive_folder_name || '';
    els.driveSuccessPath.textContent = path;
    els.driveSuccessPath.title = path;
  }
  if (els.actionDriveLink && data.drive_folder_url) {
    els.actionDriveLink.href = data.drive_folder_url;
  }
  if (els.driveSuccessCopyStatus) els.driveSuccessCopyStatus.textContent = '';
  els.driveSuccessCard.classList.remove('hidden');
}

async function copyDriveLink() {
  if (!els.actionDriveLink) return;
  const url = els.actionDriveLink.href;
  if (!url || url === '#') return;
  const ok = await copyToClipboard(url);
  if (els.driveSuccessCopyStatus) {
    els.driveSuccessCopyStatus.textContent = ok ? 'Link copied.' : 'Copy failed.';
    setTimeout(() => {
      if (els.driveSuccessCopyStatus) els.driveSuccessCopyStatus.textContent = '';
    }, 1600);
  }
}

function statusForButton(btn) {
  const row = btn.closest('.card-actions-row');
  if (row) {
    const span = row.querySelector('.card-action-status');
    if (span) return span;
  }
  return els.actionsStatus;
}

async function postJson(endpoint, body) {
  const res = await fetch(`${BACKEND}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

async function runAction({ statusEl, label, endpoint, body, success }) {
  if (!currentResult || !currentResult.artifact_folder) {
    writeStatus(statusEl, 'Run a workflow before exporting.', 'err');
    return;
  }
  writeStatus(statusEl, `${label}…`);
  try {
    const data = await postJson(endpoint, body);
    writeStatus(statusEl, success(data), 'ok');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      setBackendStatus(false);
      writeStatus(statusEl, 'Backend is not reachable. Start the FastAPI server first.', 'err');
    } else { writeStatus(statusEl, msg, 'err'); }
  }
}

function openArtifactFolderAction() {
  return runAction({
    statusEl: els.actionsStatus, label: 'Opening folder',
    endpoint: '/artifacts/open-folder',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `Opened ${data.path}.`,
  });
}

async function copyArtifactFolderAction() {
  if (!currentResult || !currentResult.artifact_folder) {
    setActionsStatus('Run a workflow before copying.', 'err');
    return;
  }
  const ok = await copyToClipboard(currentResult.artifact_folder);
  setActionsStatus(ok ? 'Folder path copied to clipboard.' : 'Could not copy folder path.', ok ? 'ok' : 'err');
}

function exportZipAction() {
  return runAction({
    statusEl: els.actionsStatus, label: 'Building ZIP',
    endpoint: '/artifacts/export-zip',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `ZIP saved: ${data.zip_path}`,
  });
}

function openFileFromCard(statusEl, filename) {
  return runAction({
    statusEl, label: `Opening ${filename}`,
    endpoint: '/artifacts/open-file',
    body: { artifact_folder: currentResult && currentResult.artifact_folder, filename },
    success: (data) => `Opened ${data.path}.`,
  });
}

function openFolderFromCard(statusEl) {
  return runAction({
    statusEl, label: 'Opening folder',
    endpoint: '/artifacts/open-folder',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `Opened ${data.path}.`,
  });
}

function exportDocxFromCard(statusEl) {
  return runAction({
    statusEl, label: 'Exporting DOCX',
    endpoint: '/artifacts/export-docx',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `DOCX exported: ${data.docx_path}`,
  });
}

function exportPptxFromCard(statusEl) {
  return runAction({
    statusEl, label: 'Exporting PPTX',
    endpoint: '/artifacts/export-pptx',
    body: { artifact_folder: currentResult && currentResult.artifact_folder },
    success: (data) => `PPTX exported: ${data.pptx_path}`,
  });
}

function handleCardAction(e) {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const action = btn.getAttribute('data-action');
  const statusEl = statusForButton(btn);
  if (action === 'open-folder') openFolderFromCard(statusEl);
  else if (action === 'open-file') {
    const filename = btn.getAttribute('data-filename');
    if (filename) openFileFromCard(statusEl, filename);
  }
  else if (action === 'export-docx') exportDocxFromCard(statusEl);
  else if (action === 'export-pptx') exportPptxFromCard(statusEl);
}

/* ---------- Audio ---------- */

const audioState = { panelId: null, utterance: null, status: 'idle' };

function _getReadablePanelText(panelId) {
  const card = document.getElementById(panelId);
  if (!card) return '';
  const body = card.querySelector('.result-body, .result-folder');
  if (!body) return '';
  let text = (body.textContent || '').replace(/\s+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1');
  return text;
}

function _audioContainerFor(panelId) {
  return document.querySelector(`[data-audio-card="${panelId}"]`);
}

function _setAudioStatus(panelId, text, kind) {
  const container = _audioContainerFor(panelId);
  if (!container) return;
  const span = container.querySelector('.audio-status');
  if (!span) return;
  span.textContent = text || '';
  span.className = 'audio-status';
  if (kind) span.classList.add(`is-${kind}`);
}

function _updateAudioButtons(panelId) {
  const container = _audioContainerFor(panelId);
  if (!container) return;
  const isActive = audioState.panelId === panelId;
  const isReading = isActive && audioState.status === 'reading';
  const isPaused = isActive && audioState.status === 'paused';
  const listenBtn = container.querySelector('[data-audio-action="listen"]');
  const pauseBtn = container.querySelector('[data-audio-action="pause"]');
  const stopBtn = container.querySelector('[data-audio-action="stop"]');
  if (listenBtn) listenBtn.disabled = isReading || isPaused;
  if (pauseBtn) {
    pauseBtn.disabled = !isReading && !isPaused;
    pauseBtn.textContent = isPaused ? 'Resume' : 'Pause';
  }
  if (stopBtn) stopBtn.disabled = !isReading && !isPaused;
}

function _resetAllAudioStatuses() {
  document.querySelectorAll('.audio-controls').forEach((container) => {
    const span = container.querySelector('.audio-status');
    if (span) { span.textContent = ''; span.className = 'audio-status'; }
    const panelId = container.getAttribute('data-audio-card');
    if (panelId) _updateAudioButtons(panelId);
  });
}

function audioListen(panelId) {
  if (typeof window.speechSynthesis === 'undefined') {
    _setAudioStatus(panelId, 'Audio not supported.', 'empty');
    return;
  }
  audioStop();
  const text = _getReadablePanelText(panelId);
  if (!text) { _setAudioStatus(panelId, 'Nothing to read yet.', 'empty'); return; }
  const utt = new SpeechSynthesisUtterance(text);
  utt.rate = 1.0; utt.pitch = 1.0;
  utt.onend = () => {
    if (audioState.panelId !== panelId) return;
    audioState.panelId = null; audioState.utterance = null; audioState.status = 'idle';
    _updateAudioButtons(panelId);
    _setAudioStatus(panelId, 'Finished.', 'finished');
  };
  utt.onerror = (e) => {
    if (audioState.panelId !== panelId) return;
    audioState.panelId = null; audioState.utterance = null; audioState.status = 'idle';
    _updateAudioButtons(panelId);
    if (e && e.error === 'interrupted') return;
    _setAudioStatus(panelId, 'Audio error.', 'empty');
  };
  audioState.panelId = panelId; audioState.utterance = utt; audioState.status = 'reading';
  try { window.speechSynthesis.speak(utt); } catch (err) {
    audioState.panelId = null; audioState.utterance = null; audioState.status = 'idle';
    _setAudioStatus(panelId, 'Audio failed to start.', 'empty');
    return;
  }
  _updateAudioButtons(panelId);
  _setAudioStatus(panelId, 'Reading…', 'reading');
}

function audioPauseResume(panelId) {
  if (audioState.panelId !== panelId) return;
  if (audioState.status === 'reading') {
    window.speechSynthesis.pause(); audioState.status = 'paused';
    _updateAudioButtons(panelId); _setAudioStatus(panelId, 'Paused.', 'paused');
  } else if (audioState.status === 'paused') {
    window.speechSynthesis.resume(); audioState.status = 'reading';
    _updateAudioButtons(panelId); _setAudioStatus(panelId, 'Reading…', 'reading');
  }
}

function audioStop() {
  if (typeof window.speechSynthesis === 'undefined') return;
  if (!audioState.panelId) return;
  const prev = audioState.panelId;
  audioState.panelId = null; audioState.utterance = null; audioState.status = 'idle';
  try { window.speechSynthesis.cancel(); } catch (_) {}
  _updateAudioButtons(prev);
  _setAudioStatus(prev, 'Stopped.', 'stopped');
}

function handleAudioClick(e) {
  const btn = e.target.closest('[data-audio-action]');
  if (!btn) return;
  const container = btn.closest('[data-audio-card]');
  if (!container) return;
  const panelId = container.getAttribute('data-audio-card');
  const action = btn.getAttribute('data-audio-action');
  if (action === 'listen') audioListen(panelId);
  else if (action === 'pause') audioPauseResume(panelId);
  else if (action === 'stop') audioStop();
}

/* ============================================================ */
/*                      APPROVE & SEND EMAIL                     */
/* ============================================================ */

function resetEmailStatus() {
  if (!els.sendEmailStatus) return;
  els.sendEmailStatus.textContent = '';
  els.sendEmailStatus.className = 'email-status';
}

function parseDraftEmail(raw) {
  const lines = (raw || '').split(/\r?\n/);
  if (lines.length && /^subject:\s*/i.test(lines[0])) {
    const subject = lines[0].replace(/^subject:\s*/i, '').trim();
    let i = 1;
    while (i < lines.length && lines[i].trim() === '') i++;
    const body = lines.slice(i).join('\n').trim();
    return { subject, body };
  }
  return { subject: '', body: (raw || '').trim() };
}

function resetAgenticEmailStatus() {
  if (!els.agenticSendEmailStatus) return;
  els.agenticSendEmailStatus.textContent = '';
  els.agenticSendEmailStatus.className = 'email-status';
}

function resetNotebookLMEmailStatus() {
  if (!els.notebooklmSendEmailStatus) return;
  els.notebooklmSendEmailStatus.textContent = '';
  els.notebooklmSendEmailStatus.className = 'email-status';
}

async function _sendApprovedArtifactEmail({ subject, body, btn, statusEl, confirmText, doneLabel }) {
  if (!body || !body.trim()) {
    statusEl.className = 'email-status is-err';
    statusEl.textContent = 'Nothing to send — the artifact is empty.';
    return;
  }
  const ok = window.confirm(confirmText);
  if (!ok) return;
  if (btn) btn.disabled = true;
  statusEl.className = 'email-status';
  statusEl.textContent = 'Sending…';
  try {
    const res = await fetch(`${BACKEND}/email/send-approved`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subject, body }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    statusEl.className = 'email-status is-ok';
    const to = data && data.to_email ? ` to ${data.to_email}` : '';
    statusEl.textContent = `${doneLabel}${to}.`;
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    statusEl.className = 'email-status is-err';
    statusEl.textContent = /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
      ? 'Backend is not reachable.'
      : msg;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function sendAgenticEmail() {
  if (!currentResult || !currentResult.agentic_advances_brief) return;
  await _sendApprovedArtifactEmail({
    subject: 'Ridian Command Center — Agentic Advances Brief',
    body: currentResult.agentic_advances_brief,
    btn: els.agenticSendEmailBtn,
    statusEl: els.agenticSendEmailStatus,
    confirmText: 'Send this Agentic Advances brief to your configured email address?',
    doneLabel: 'Brief sent successfully',
  });
}

async function sendNotebookLMEmail() {
  if (!currentResult || !currentResult.notebooklm_package) return;
  await _sendApprovedArtifactEmail({
    subject: 'Ridian Command Center — NotebookLM Package',
    body: currentResult.notebooklm_package,
    btn: els.notebooklmSendEmailBtn,
    statusEl: els.notebooklmSendEmailStatus,
    confirmText: 'Send this NotebookLM package to your configured email address?',
    doneLabel: 'Package sent successfully',
  });
}

async function sendApprovedEmail() {
  if (!currentResult || !currentResult.draft_email) return;
  const ok = window.confirm('Send this generated email to your configured email address?');
  if (!ok) return;
  const { subject, body } = parseDraftEmail(currentResult.draft_email);
  if (!body) {
    els.sendEmailStatus.className = 'email-status is-err';
    els.sendEmailStatus.textContent = 'Email body is empty — nothing to send.';
    return;
  }
  const finalSubject = subject || DEFAULT_EMAIL_SUBJECT;
  els.sendEmailBtn.disabled = true;
  els.sendEmailStatus.className = 'email-status';
  els.sendEmailStatus.textContent = 'Sending…';
  try {
    const res = await fetch(`${BACKEND}/email/send-approved`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subject: finalSubject, body }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    els.sendEmailStatus.className = 'email-status is-ok';
    const to = data && data.to_email ? ` to ${data.to_email}` : '';
    els.sendEmailStatus.textContent = `Email sent successfully${to}.`;
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    els.sendEmailStatus.className = 'email-status is-err';
    els.sendEmailStatus.textContent = /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
      ? 'Backend is not reachable.' : msg;
  } finally { els.sendEmailBtn.disabled = false; }
}

/* ============================================================ */
/*                       GOOGLE DRIVE                            */
/* ============================================================ */

let googleConnected = null;
let googleConnectedEmail = null;

function setGoogleStatusUI(state) {
  if (els.googleStatusLabel) {
    googleConnected = !!state.connected;
    googleConnectedEmail = state.email || null;
    els.googleStatusLabel.classList.remove('is-connected', 'is-disconnected', 'is-err');
    if (state.error) {
      els.googleStatusLabel.classList.add('is-err');
      els.googleStatusLabel.textContent = state.error;
    } else if (state.connected) {
      els.googleStatusLabel.classList.add('is-connected');
      els.googleStatusLabel.textContent = state.email ? `Connected as ${state.email}` : 'Connected';
    } else {
      els.googleStatusLabel.classList.add('is-disconnected');
      els.googleStatusLabel.textContent = 'Not connected';
    }
    if (els.googleConnectBtn) {
      els.googleConnectBtn.disabled = !!state.busy;
      els.googleConnectBtn.textContent = state.busy ? 'Waiting for sign-in…' : (state.connected ? 'Reconnect Google Drive' : 'Connect Google Drive');
    }
    if (els.googleDisconnectBtn) els.googleDisconnectBtn.disabled = !state.connected || !!state.busy;
  }
  // Header pill mirrors connection state
  if (els.googlePill && els.googlePillLabel) {
    els.googlePill.classList.remove('is-up', 'is-down', 'is-unknown');
    els.googlePill.classList.remove('hidden');
    if (state.connected) {
      els.googlePill.classList.add('is-up');
      els.googlePillLabel.textContent = 'Drive online';
    } else {
      els.googlePill.classList.add('is-down');
      els.googlePillLabel.textContent = 'Drive offline';
    }
  }
}

async function loadGoogleStatus() {
  setGoogleStatusUI({ connected: false, email: null, error: 'Checking…' });
  try {
    const res = await fetch(`${BACKEND}/google/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setGoogleStatusUI(await res.json());
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setGoogleStatusUI({ connected: false, email: null, error: /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg) ? 'Backend not reachable.' : `Status error: ${msg}` });
  }
}

async function connectGoogleDrive() {
  setGoogleStatusUI({ connected: googleConnected, email: googleConnectedEmail, busy: true });
  setSettingsStatus('A browser tab opened for Google sign-in. Complete it to continue…');
  try {
    const res = await fetch(`${BACKEND}/google/connect`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    setGoogleStatusUI(data);
    setSettingsStatus(data.connected ? `Connected as ${data.email || 'your Google account'}.` : 'Connect did not complete.', data.connected ? 'ok' : 'err');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setGoogleStatusUI({ connected: false, email: null });
    setSettingsStatus(/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg) ? 'Backend is not reachable.' : msg, 'err');
  }
}

async function disconnectGoogleDrive() {
  const ok = window.confirm('Disconnect Google Drive? The saved token will be deleted from this machine.');
  if (!ok) return;
  setGoogleStatusUI({ connected: googleConnected, email: googleConnectedEmail, busy: true });
  try {
    const res = await fetch(`${BACKEND}/google/disconnect`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setGoogleStatusUI(data);
    setSettingsStatus('Disconnected from Google Drive.', 'ok');
  } catch (err) {
    setSettingsStatus(`Disconnect failed: ${err && err.message ? err.message : err}`, 'err');
    loadGoogleStatus();
  }
}

async function uploadArtifactsToDrive() {
  if (!currentResult || !currentResult.artifact_folder) {
    setActionsStatus('Run a workflow before uploading to Drive.', 'err');
    return;
  }
  if (googleConnected === false) {
    setActionsStatus('Google Drive is not connected. Open Settings to connect.', 'err');
    return;
  }
  if (googleConnected === null) {
    await loadGoogleStatus();
    if (googleConnected === false) {
      setActionsStatus('Google Drive is not connected. Open Settings to connect.', 'err');
      return;
    }
  }
  const ok = window.confirm("Upload this workflow's output folder to your connected Google Drive?");
  if (!ok) return;
  hideDriveSuccessCard();
  setActionsStatus('Uploading to Google Drive…');
  if (els.actionUploadDrive) els.actionUploadDrive.disabled = true;
  try {
    const res = await fetch(`${BACKEND}/google/upload-artifacts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_folder: currentResult.artifact_folder }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    // Clear the in-progress status line; the rich card carries the full
    // success state from here on.
    setActionsStatus('');
    showDriveSuccessCard(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    if (/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)) {
      setBackendStatus(false);
      setActionsStatus('Backend is not reachable.', 'err');
    } else { setActionsStatus(msg, 'err'); }
  } finally {
    if (els.actionUploadDrive) els.actionUploadDrive.disabled = false;
  }
}

/* ============================================================ */
/*                       SETTINGS MODAL                          */
/* ============================================================ */

function setSettingsStatus(text, kind) {
  if (!els.settingsStatus) return;
  els.settingsStatus.textContent = text || '';
  els.settingsStatus.className = 'modal-status';
  if (kind === 'ok') els.settingsStatus.classList.add('is-ok');
  if (kind === 'err') els.settingsStatus.classList.add('is-err');
}

/* ============================================================ */
/*                     THEME / APPEARANCE                        */
/* ============================================================ */

function applyTheme(pref) {
  const val = (pref || 'system').toLowerCase();
  if (val === 'dark') {
    document.documentElement.dataset.theme = 'dark';
  } else if (val === 'light') {
    document.documentElement.dataset.theme = 'light';
  } else {
    // system
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.dataset.theme = prefersDark ? 'dark' : 'light';
  }
}

// Listen for OS theme changes when in system mode
try {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    const saved = (cachedSettings && cachedSettings.appearance) || 'system';
    if (saved === 'system' || !saved) applyTheme('system');
  });
} catch (_) { /* matchMedia listener not supported */ }

function applySettingsToForm(settings) {
  if (!els.settingsForm) return;
  SETTINGS_FIELDS.forEach((name) => {
    const input = els.settingsForm.elements.namedItem(name);
    if (!input) return;
    if (name === 'appearance') {
      input.value = settings[name] || 'system';
    } else {
      input.value = settings[name] || '';
    }
  });
  SETTINGS_SECRET_FIELDS.forEach((name) => {
    const input = els.settingsForm.elements.namedItem(name);
    if (input) input.value = '';
  });
  if (els.settingsOpenaiKeyHint) {
    if (settings.openai_api_key_configured) {
      els.settingsOpenaiKeyHint.className = 'field-hint is-ok';
      els.settingsOpenaiKeyHint.textContent = 'An OpenAI API key is currently saved. Leave blank to keep it; type a new one to replace it.';
    } else {
      els.settingsOpenaiKeyHint.className = 'field-hint';
      els.settingsOpenaiKeyHint.textContent = 'Paste your OpenAI API key here. Get one at platform.openai.com/api-keys.';
    }
  }
  if (els.settingsPasswordHint) {
    if (settings.smtp_password_configured) {
      els.settingsPasswordHint.className = 'field-hint is-ok';
      els.settingsPasswordHint.textContent = 'A password is currently saved. Leave blank to keep it; type a new one to replace it.';
    } else {
      els.settingsPasswordHint.className = 'field-hint';
      els.settingsPasswordHint.textContent = 'No password saved yet. For Gmail use an App Password.';
    }
  }
  if (els.settingsOutputsPath) els.settingsOutputsPath.textContent = settings.outputs_path || '—';
}

async function loadSettingsIntoForm() {
  setSettingsStatus('Loading…');
  try {
    const res = await fetch(`${BACKEND}/settings`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    cachedSettings = data;
    applySettingsToForm(data);
    setSettingsStatus('');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setSettingsStatus(/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg) ? 'Backend is not reachable.' : `Could not load settings: ${msg}`, 'err');
  }
}

function openSettings() {
  if (!els.settingsModal) return;
  els.settingsModal.classList.remove('hidden');
  els.settingsModal.setAttribute('aria-hidden', 'false');
  document.addEventListener('keydown', handleSettingsKeydown);
  loadSettingsIntoForm().then(() => {
    const first = els.settingsForm && els.settingsForm.elements.namedItem('openai_api_key');
    if (first && typeof first.focus === 'function') first.focus();
  });
  loadGoogleStatus();
}

function openTips() {
  if (!els.tipsModal) return;
  els.tipsModal.classList.remove('hidden');
  els.tipsModal.setAttribute('aria-hidden', 'false');
  document.addEventListener('keydown', handleTipsKeydown);
  if (els.tipsDoneBtn) els.tipsDoneBtn.focus();
}

function closeTips() {
  if (!els.tipsModal) return;
  els.tipsModal.classList.add('hidden');
  els.tipsModal.setAttribute('aria-hidden', 'true');
  document.removeEventListener('keydown', handleTipsKeydown);
  if (els.sidebarTipsBtn) els.sidebarTipsBtn.focus();
}

function handleTipsKeydown(e) {
  if (e.key === 'Escape') { e.preventDefault(); closeTips(); }
}

function closeSettings() {
  if (!els.settingsModal) return;
  els.settingsModal.classList.add('hidden');
  els.settingsModal.setAttribute('aria-hidden', 'true');
  document.removeEventListener('keydown', handleSettingsKeydown);
  setSettingsStatus('');
  if (els.sidebarSettingsBtn) els.sidebarSettingsBtn.focus();
}

function handleSettingsKeydown(e) {
  if (e.key === 'Escape') { e.preventDefault(); closeSettings(); }
}

async function saveSettings(e) {
  if (e) e.preventDefault();
  if (!els.settingsForm) return;
  const fd = new FormData(els.settingsForm);
  const payload = {};
  SETTINGS_FIELDS.forEach((name) => { payload[name] = (fd.get(name) || '').toString().trim(); });
  SETTINGS_SECRET_FIELDS.forEach((name) => {
    const v = (fd.get(name) || '').toString();
    if (v !== '') payload[name] = v;
  });
  els.settingsSaveBtn.disabled = true;
  setSettingsStatus('Saving…');
  try {
    const res = await fetch(`${BACKEND}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    cachedSettings = data;
    applySettingsToForm(data);
    applyTheme(data.appearance);
    pollHealth();
    setSettingsStatus('Settings saved.', 'ok');
    setTimeout(() => {
      if (els.settingsStatus && els.settingsStatus.textContent === 'Settings saved.') setSettingsStatus('');
    }, 2500);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setSettingsStatus(/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg) ? 'Backend is not reachable.' : `Could not save: ${msg}`, 'err');
  } finally { els.settingsSaveBtn.disabled = false; }
}

async function testEmailSettings() {
  const ok = window.confirm('Send a test email to your saved default recipient using the current SMTP settings?');
  if (!ok) return;
  els.settingsTestEmailBtn.disabled = true;
  setSettingsStatus('Sending test email…');
  try {
    const res = await fetch(`${BACKEND}/email/send-approved`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subject: 'Ridian Agency SMTP test',
        body: 'This is a test email sent from the Ridian Agency Settings panel. If you received this, your SMTP credentials are working.\n\n— Ridian Agency',
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = data && data.detail ? data.detail : `HTTP ${res.status}`;
      throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
    const to = data && data.to_email ? ` to ${data.to_email}` : '';
    setSettingsStatus(`Test email sent${to}.`, 'ok');
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setSettingsStatus(/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg) ? 'Backend is not reachable.' : msg, 'err');
  } finally { els.settingsTestEmailBtn.disabled = false; }
}

/* ============================================================ */
/*                  PROMPT LIBRARIES (BUSINESS + SOCIAL)         */
/* ============================================================ */

function buildPromptLibrary() {
  const tabsEl = els.promptsTabsBusiness;
  const panelsEl = els.promptsPanelsBusiness;
  if (!tabsEl || !panelsEl) return;
  PROMPT_CATEGORIES.forEach((cat, i) => {
    const isFirst = i === 0;
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'prompt-tab' + (isFirst ? ' is-active' : '');
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-selected', isFirst ? 'true' : 'false');
    tab.setAttribute('data-cat', cat.id);
    tab.textContent = cat.label;
    tab.addEventListener('click', () => activatePromptCategory(cat.id));
    tabsEl.appendChild(tab);

    const panel = document.createElement('div');
    panel.className = 'prompts-grid' + (isFirst ? '' : ' hidden');
    panel.setAttribute('data-cat-panel', cat.id);
    panel.setAttribute('role', 'tabpanel');
    panel.setAttribute('aria-label', cat.label);
    cat.prompts.forEach((p) => {
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'prompt-pill';
      pill.textContent = p;
      pill.addEventListener('click', () => fillTaskFromPrompt(p));
      panel.appendChild(pill);
    });
    panelsEl.appendChild(panel);
  });
}

function activatePromptCategory(id) {
  document.querySelectorAll('#view-input-business [data-cat]').forEach((t) => {
    const active = t.getAttribute('data-cat') === id;
    t.classList.toggle('is-active', active);
    t.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('#view-input-business [data-cat-panel]').forEach((p) => {
    p.classList.toggle('hidden', p.getAttribute('data-cat-panel') !== id);
  });
}

function fillTaskFromPrompt(text) {
  els.taskInput.value = text;
  els.taskInput.focus();
  els.taskInput.setSelectionRange(text.length, text.length);
}

function applySocialPromptFields(fields) {
  for (const [key, value] of Object.entries(fields)) {
    const elKey = SOCIAL_FIELD_MAP[key];
    const el = elKey && els[elKey];
    if (el) el.value = value;
  }
}

function buildSocialPromptLibrary() {
  const tabsEl = els.socialPromptsTabs;
  const panelsEl = els.socialPromptsPanels;
  if (!tabsEl || !panelsEl) return;
  SOCIAL_PROMPT_CATEGORIES.forEach((cat, i) => {
    const isFirst = i === 0;
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'prompt-tab' + (isFirst ? ' is-active' : '');
    tab.setAttribute('role', 'tab');
    tab.setAttribute('data-social-cat', cat.id);
    tab.textContent = cat.label;
    tab.addEventListener('click', () => activateSocialCategory(cat.id));
    tabsEl.appendChild(tab);

    const panel = document.createElement('div');
    panel.className = 'prompts-grid' + (isFirst ? '' : ' hidden');
    panel.setAttribute('data-social-cat-panel', cat.id);
    cat.prompts.forEach((p) => {
      const pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'prompt-pill';
      pill.textContent = p.text;
      pill.addEventListener('click', () => {
        applySocialPromptFields(p.fields);
        if (els.socialTopicNotes) els.socialTopicNotes.focus({ preventScroll: true });
      });
      panel.appendChild(pill);
    });
    panelsEl.appendChild(panel);
  });
}

function activateSocialCategory(id) {
  document.querySelectorAll('[data-social-cat]').forEach((t) => {
    t.classList.toggle('is-active', t.getAttribute('data-social-cat') === id);
  });
  document.querySelectorAll('[data-social-cat-panel]').forEach((p) => {
    p.classList.toggle('hidden', p.getAttribute('data-social-cat-panel') !== id);
  });
}

/* ============================================================ */
/*                       HEALTH POLLING                          */
/* ============================================================ */

let backendUp = null;
let openaiKeyConfigured = null;

function setBackendStatus(up) {
  if (backendUp === up) return;
  backendUp = up;
  els.backendPill.classList.remove('is-up', 'is-down', 'is-unknown');
  if (up) {
    els.backendPill.classList.add('is-up');
    els.backendLabel.textContent = 'Backend online';
    hide(els.backendDownBanner);
  } else {
    els.backendPill.classList.add('is-down');
    els.backendLabel.textContent = 'Backend offline';
    show(els.backendDownBanner);
  }
}

function setOpenAIKeyState(configured) {
  if (openaiKeyConfigured === configured) return;
  openaiKeyConfigured = configured;
  if (configured) {
    hide(els.openaiMissingBanner);
    if (els.runBtn) { els.runBtn.disabled = false; els.runBtn.title = ''; }
    if (els.socialRunBtn) { els.socialRunBtn.disabled = false; els.socialRunBtn.title = ''; }
  } else {
    show(els.openaiMissingBanner);
    if (els.runBtn) { els.runBtn.disabled = true; els.runBtn.title = 'Configure your OpenAI API key in Settings first.'; }
    if (els.socialRunBtn) { els.socialRunBtn.disabled = true; els.socialRunBtn.title = 'Configure your OpenAI API key in Settings first.'; }
  }
}

async function pollHealth() {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 2500);
    const res = await fetch(`${BACKEND}/health`, { signal: ctrl.signal });
    clearTimeout(t);
    setBackendStatus(res.ok);
    if (res.ok) {
      const data = await res.json().catch(() => ({}));
      setOpenAIKeyState(!!data.openai_key_loaded);
    }
  } catch (_) { setBackendStatus(false); }
}

function startHealthPolling() {
  pollHealth();
  setInterval(pollHealth, 5000);
}

/* ============================================================ */
/*                          WIRE UP                              */
/* ============================================================ */

// Welcome cards
document.querySelectorAll('.welcome-card').forEach((card) => {
  card.addEventListener('click', () => {
    const mode = card.getAttribute('data-mode');
    if (mode === 'business' || mode === 'social' || mode === 'agentic' || mode === 'notebooklm') {
      setMode(mode);
      setWorkspaceView('input');
    }
  });
});

// Sidebar mode buttons
if (els.sidebarModeBusiness) {
  els.sidebarModeBusiness.addEventListener('click', () => { setMode('business'); setWorkspaceView('input'); });
}
if (els.sidebarModeSocial) {
  els.sidebarModeSocial.addEventListener('click', () => { setMode('social'); setWorkspaceView('input'); });
}
if (els.sidebarModeAgentic) {
  els.sidebarModeAgentic.addEventListener('click', () => { setMode('agentic'); setWorkspaceView('input'); });
}
if (els.sidebarModeNotebooklm) {
  els.sidebarModeNotebooklm.addEventListener('click', () => { setMode('notebooklm'); setWorkspaceView('input'); });
}

// Workspace back arrow
if (els.workspaceBackBtn) {
  els.workspaceBackBtn.addEventListener('click', goBackToWelcome);
}

// Sidebar "Show hidden runs" disclosure toggle
if (els.sidebarHiddenToggle) {
  els.sidebarHiddenToggle.addEventListener('click', toggleHiddenRunsList);
}

// Sidebar Recent runs search — pure client-side filter; no backend call.
if (els.sidebarRunsSearch) {
  els.sidebarRunsSearch.addEventListener('input', () => {
    recentRunsSearch = els.sidebarRunsSearch.value || '';
    runsExpanded = false;
    renderRecentRuns();
  });
}

// Sidebar filter chips
document.querySelectorAll('[data-runs-filter]').forEach((chip) => {
  chip.addEventListener('click', () => {
    runsFilter = chip.getAttribute('data-runs-filter') || 'all';
    runsExpanded = false;
    renderRecentRuns();
  });
});

// Show more / Show less toggle
if (els.sidebarRunsShowMore) {
  els.sidebarRunsShowMore.addEventListener('click', () => {
    runsExpanded = !runsExpanded;
    renderRecentRuns();
  });
}

// Welcome tip dismiss (persistent via localStorage)
if (els.welcomeTipDismiss) {
  els.welcomeTipDismiss.addEventListener('click', dismissWelcomeTip);
}

// New Workflow button
if (els.sidebarNewWorkflowBtn) {
  els.sidebarNewWorkflowBtn.addEventListener('click', openWizard);
}

// Wizard option clicks (Step 1 → Step 2)
document.querySelectorAll('[data-wizard]').forEach((btn) => {
  btn.addEventListener('click', () => _wizardSelectOption(btn.getAttribute('data-wizard')));
});

// Wizard close + initial footer buttons (re-wired on each step transition)
const wizardCloseBtn = document.getElementById('wizard-close-btn');
if (wizardCloseBtn) wizardCloseBtn.addEventListener('click', closeWizard);
const wizardCancelBtn = document.getElementById('wizard-cancel-btn');
const wizardBlankBtn = document.getElementById('wizard-blank-btn');
if (wizardCancelBtn) wizardCancelBtn.addEventListener('click', closeWizard);
if (wizardBlankBtn) wizardBlankBtn.addEventListener('click', () => {
  closeWizard();
  startNewWorkflow(currentMode);
});

// Settings + Quick tips buttons
if (els.sidebarSettingsBtn) els.sidebarSettingsBtn.addEventListener('click', openSettings);
if (els.sidebarTipsBtn) els.sidebarTipsBtn.addEventListener('click', openTips);
if (els.tipsCloseBtn) els.tipsCloseBtn.addEventListener('click', closeTips);
if (els.tipsDoneBtn) els.tipsDoneBtn.addEventListener('click', closeTips);
if (els.tipsModal) {
  els.tipsModal.addEventListener('click', (e) => { if (e.target === els.tipsModal) closeTips(); });
}
if (els.settingsCloseBtn) els.settingsCloseBtn.addEventListener('click', closeSettings);
if (els.settingsCancelBtn) els.settingsCancelBtn.addEventListener('click', closeSettings);
if (els.settingsForm) els.settingsForm.addEventListener('submit', saveSettings);
if (els.settingsTestEmailBtn) els.settingsTestEmailBtn.addEventListener('click', testEmailSettings);
if (els.settingsModal) {
  els.settingsModal.addEventListener('click', (e) => { if (e.target === els.settingsModal) closeSettings(); });
}

// Business form
if (els.runBtn) els.runBtn.addEventListener('click', runWorkflow);
if (els.clearBtn) els.clearBtn.addEventListener('click', () => startNewWorkflow('business'));
if (els.exampleBtn) els.exampleBtn.addEventListener('click', () => { els.taskInput.value = EXAMPLE_TASK; els.taskInput.focus(); });
if (els.taskInput) {
  els.taskInput.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runWorkflow(); }
  });
}

// Social form
if (els.socialRunBtn) els.socialRunBtn.addEventListener('click', runSocialWorkflow);
if (els.socialClearBtn) els.socialClearBtn.addEventListener('click', () => startNewWorkflow('social'));

// Agentic Advances form
if (els.agenticRunBtn) els.agenticRunBtn.addEventListener('click', runAgenticAdvancesWorkflow);
if (els.agenticClearBtn) els.agenticClearBtn.addEventListener('click', () => startNewWorkflow('agentic'));
if (els.agenticSendEmailBtn) els.agenticSendEmailBtn.addEventListener('click', sendAgenticEmail);
if (els.agenticTopicFocus) {
  els.agenticTopicFocus.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runAgenticAdvancesWorkflow(); }
  });
}

// NotebookLM form
if (els.notebooklmRunBtn) els.notebooklmRunBtn.addEventListener('click', runNotebookLMWorkflow);
if (els.notebooklmClearBtn) els.notebooklmClearBtn.addEventListener('click', () => startNewWorkflow('notebooklm'));
if (els.notebooklmSendEmailBtn) els.notebooklmSendEmailBtn.addEventListener('click', sendNotebookLMEmail);
if (els.notebooklmSubject) {
  els.notebooklmSubject.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runNotebookLMWorkflow(); }
  });
}

// Panel back-to-review navigation
const panelBackBtn = document.getElementById('panel-back-btn');
if (panelBackBtn) panelBackBtn.addEventListener('click', () => showResultPanel('review-publish-card'));

// Thumbnail / image input
if (els.thumbnailSelectBtn && els.thumbnailFileInput) {
  els.thumbnailSelectBtn.addEventListener('click', () => els.thumbnailFileInput.click());
  els.thumbnailFileInput.addEventListener('change', () => {
    const file = els.thumbnailFileInput.files && els.thumbnailFileInput.files[0];
    if (file) handleThumbnailSelect(file);
  });
}
if (els.thumbnailClearBtn) els.thumbnailClearBtn.addEventListener('click', clearThumbnail);
if (els.socialTopicNotes) {
  els.socialTopicNotes.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runSocialWorkflow(); }
  });
}

// Email approval
if (els.sendEmailBtn) els.sendEmailBtn.addEventListener('click', sendApprovedEmail);

// Actions card (global)
if (els.actionOpenFolder) els.actionOpenFolder.addEventListener('click', openArtifactFolderAction);
if (els.actionCopyFolder) els.actionCopyFolder.addEventListener('click', copyArtifactFolderAction);
if (els.actionExportZip) els.actionExportZip.addEventListener('click', exportZipAction);
if (els.actionUploadDrive) els.actionUploadDrive.addEventListener('click', uploadArtifactsToDrive);
if (els.driveSuccessCopy) els.driveSuccessCopy.addEventListener('click', copyDriveLink);

// Google Drive (in Settings modal)
if (els.googleConnectBtn) els.googleConnectBtn.addEventListener('click', connectGoogleDrive);
if (els.googleDisconnectBtn) els.googleDisconnectBtn.addEventListener('click', disconnectGoogleDrive);

// Delegated handlers for per-card action buttons + audio buttons
if (els.resultPanelArea) {
  els.resultPanelArea.addEventListener('click', handleCardAction);
  els.resultPanelArea.addEventListener('click', handleAudioClick);
}

/* ============================================================ */
/*           DASHBOARD + MEMORY (Ridian Command Center)          */
/* ============================================================ */

async function loadDashboard() {
  try {
    const res = await fetch(`${BACKEND}/dashboard`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderDashboard(data);
  } catch (err) {
    debugLog('dashboard.load_failed', { error: err && err.message });
  }
}

function _iconForTarget(target) {
  if (!target) return '→';
  if (target === 'wizard') return '✦';
  if (target.startsWith('memory:')) return 'M';
  if (target.startsWith('run:')) return '▶';
  return '→';
}

function _ctaLabelForTarget(target) {
  if (!target) return 'Open';
  if (target === 'wizard') return 'Open wizard';
  if (target === 'memory:contacts') return 'Open contacts';
  if (target === 'memory:brand') return 'Open brand';
  if (target === 'memory:facts') return 'Open facts';
  if (target === 'memory:follow-ups') return 'Open follow-ups';
  if (target === 'memory:decisions') return 'Open decisions';
  if (target.startsWith('memory:')) return 'Open memory';
  if (target.startsWith('run:')) return 'Open run';
  return 'Open';
}

function _statusLineFor(data) {
  const counts = data.memory_counts || {};
  const open = (data.open_follow_ups || []).length;
  const recent = (data.recent_runs || []).length;
  const memoryEmpty = !(counts.contacts || counts.facts || counts.decisions || counts.brand_voices);

  if (memoryEmpty && recent === 0) {
    return "Welcome back. Set up your context, then launch your first workflow when you're ready.";
  }
  if (open > 0) {
    return `${open} follow-up${open === 1 ? '' : 's'} need attention. Pick one below or jump into recent work.`;
  }
  if (recent > 0) {
    return "Inbox is clear. Pick up where you left off or launch something new.";
  }
  return "All caught up. Ready when you are.";
}

function _renderFocus(actions, data) {
  const body = document.getElementById('dashboard-focus-body');
  const meta = document.getElementById('dashboard-focus-meta');
  if (!body) return;
  body.innerHTML = '';

  if (!actions || !actions.length) {
    if (meta) meta.textContent = '';
    const block = document.createElement('div');
    block.className = 'dashboard-focus-allclear';
    block.innerHTML = `
      <span class="dashboard-focus-allclear-icon" aria-hidden="true">✓</span>
      <span class="dashboard-focus-allclear-text">
        <span class="dashboard-focus-allclear-title">You're all caught up.</span>
        <span class="dashboard-focus-allclear-hint">Use Quick launch below to start something new.</span>
      </span>
    `;
    body.appendChild(block);
    return;
  }

  if (meta) meta.textContent = `${actions.length} suggestion${actions.length === 1 ? '' : 's'}`;

  const primary = actions[0];
  const primaryRow = document.createElement('div');
  primaryRow.className = 'dashboard-focus-primary';
  const primaryText = document.createElement('div');
  primaryText.className = 'dashboard-focus-primary-text';
  primaryText.innerHTML = `
    <span class="dashboard-focus-primary-label">${escapeHtml(primary.label || '')}</span>
    ${primary.hint ? `<span class="dashboard-focus-primary-hint">${escapeHtml(primary.hint)}</span>` : ''}
  `;
  const primaryCta = document.createElement('button');
  primaryCta.type = 'button';
  primaryCta.className = 'dashboard-focus-primary-cta';
  primaryCta.textContent = _ctaLabelForTarget(primary.target) + ' →';
  primaryCta.addEventListener('click', () => _handleDashboardActionTarget(primary.target));
  primaryRow.appendChild(primaryText);
  primaryRow.appendChild(primaryCta);
  body.appendChild(primaryRow);

  const rest = actions.slice(1, 4);
  if (rest.length) {
    const secondaries = document.createElement('div');
    secondaries.className = 'dashboard-focus-secondaries';
    rest.forEach((a) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'dashboard-focus-secondary';
      btn.innerHTML = `
        <span class="dashboard-focus-secondary-icon" aria-hidden="true">${escapeHtml(_iconForTarget(a.target))}</span>
        <span class="dashboard-focus-secondary-body">
          <span class="dashboard-focus-secondary-label">${escapeHtml(a.label || '')}</span>
          ${a.hint ? `<span class="dashboard-focus-secondary-hint">${escapeHtml(a.hint)}</span>` : ''}
        </span>
        <span class="dashboard-focus-secondary-chevron" aria-hidden="true">→</span>
      `;
      btn.addEventListener('click', () => _handleDashboardActionTarget(a.target));
      secondaries.appendChild(btn);
    });
    body.appendChild(secondaries);
  }
}

function _renderFollowUps(items) {
  const fuEl = document.getElementById('dashboard-follow-ups');
  const metaEl = document.getElementById('dashboard-followups-meta');
  if (!fuEl) return;
  fuEl.innerHTML = '';
  const list = items || [];
  if (metaEl) metaEl.textContent = list.length ? `${list.length} open` : 'All clear';

  if (!list.length) {
    const li = document.createElement('li');
    li.className = 'dashboard-empty';
    li.textContent = "Your follow-up list is clear. Add one when you need to remember a next step.";
    fuEl.appendChild(li);
    return;
  }

  list.slice(0, 6).forEach((f) => {
    const li = document.createElement('li');
    li.className = 'dashboard-follow-up-item';
    const meta = [f.who, f.due_iso].filter(Boolean).join(' · ');
    li.innerHTML = `
      <span class="dashboard-follow-up-check" aria-hidden="true"></span>
      <span class="dashboard-follow-up-item-text">
        <span class="dashboard-follow-up-what">${escapeHtml(f.what || '')}</span>
        ${meta ? `<span class="dashboard-follow-up-meta">${escapeHtml(meta)}</span>` : ''}
      </span>
    `;
    const doneBtn = document.createElement('button');
    doneBtn.type = 'button';
    doneBtn.className = 'dashboard-follow-up-done';
    doneBtn.textContent = 'Mark done';
    doneBtn.setAttribute('aria-label', `Mark follow-up done: ${f.what || ''}`);
    doneBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      markFollowUpDone(f.id);
    });
    li.appendChild(doneBtn);
    fuEl.appendChild(li);
  });

  if (list.length > 6) {
    const more = document.createElement('li');
    more.className = 'dashboard-empty';
    more.textContent = `+${list.length - 6} more — open Edit memory to see all.`;
    fuEl.appendChild(more);
  }
}

function _renderRecent(items) {
  const recentEl = document.getElementById('dashboard-recent');
  const metaEl = document.getElementById('dashboard-recent-meta');
  if (!recentEl) return;
  recentEl.innerHTML = '';
  const list = (items || []).slice(0, 6);
  if (metaEl) metaEl.textContent = list.length ? `Last ${list.length}` : '';

  if (!list.length) {
    const li = document.createElement('li');
    li.className = 'dashboard-empty';
    li.textContent = "Nothing yet. Use Quick launch above to start your first workflow.";
    recentEl.appendChild(li);
    return;
  }

  list.forEach((r) => {
    const li = document.createElement('li');
    li.className = 'dashboard-recent-item';
    let channel, icon;
    if (r.workflow === 'social') { channel = r.channel || 'Social'; icon = 'S'; }
    else if (r.workflow === 'agentic') { channel = 'Agentic Brief'; icon = 'A'; }
    else if (r.workflow === 'notebooklm') { channel = 'NotebookLM'; icon = 'N'; }
    else { channel = 'Business'; icon = 'B'; }
    const title = prettifyRunName(r.name || '');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dashboard-recent-item-button';
    btn.innerHTML = `
      <span class="dashboard-recent-item-icon" aria-hidden="true">${icon}</span>
      <span class="dashboard-recent-item-text">
        <span class="dashboard-recent-item-title">${escapeHtml(title)}</span>
        <span class="dashboard-recent-item-meta">${escapeHtml(channel)} · ${escapeHtml(fmtDateShort(r.mtime_iso))}</span>
      </span>
      <span class="dashboard-recent-item-chevron" aria-hidden="true">→</span>
    `;
    btn.addEventListener('click', () => openProjectFromSidebar(r));
    li.appendChild(btn);
    recentEl.appendChild(li);
  });
}

function _renderContext(counts) {
  const map = {
    contacts: counts.contacts || 0,
    brand_voices: counts.brand_voices || 0,
    facts: counts.facts || 0,
    decisions: counts.decisions || 0,
    open_follow_ups: counts.open_follow_ups || 0,
  };
  Object.keys(map).forEach((k) => {
    const el = document.querySelector(`[data-count="${k}"]`);
    if (el) el.textContent = String(map[k]);
  });

  // Show the "memory is empty" friendly explainer only when nothing is set.
  const emptyEl = document.getElementById('dashboard-context-empty');
  const memoryEmpty = !(
    map.contacts || map.facts || map.decisions || map.brand_voices || map.open_follow_ups
  );
  if (emptyEl) {
    if (memoryEmpty) emptyEl.removeAttribute('hidden');
    else emptyEl.setAttribute('hidden', '');
  }

  // Wire each context row to open the matching memory tab.
  document.querySelectorAll('[data-ctx-target]').forEach((row) => {
    if (row.dataset.ctxWired === 'true') return;
    row.dataset.ctxWired = 'true';
    row.setAttribute('role', 'button');
    row.setAttribute('tabindex', '0');
    const tab = row.getAttribute('data-ctx-target');
    const open = () => openMemoryModal(tab);
    row.addEventListener('click', open);
    row.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
    });
  });
}

function renderDashboard(data) {
  const todayEl = document.getElementById('dashboard-today');
  if (todayEl) todayEl.textContent = data.today || '';

  const statusEl = document.getElementById('dashboard-status-line');
  if (statusEl) statusEl.textContent = _statusLineFor(data);

  _renderFocus(data.suggested_next_actions || [], data);
  _renderFollowUps(data.open_follow_ups || []);
  _renderRecent(data.recent_runs || []);
  _renderContext(data.memory_counts || {});
}

function _handleQuickLaunch(key) {
  if (!key) return;
  if (key === 'new-business') { startNewWorkflow('business'); return; }
  if (key === 'new-social') { startNewWorkflow('social'); return; }
  if (key === 'agentic-brief') { startNewWorkflow('agentic'); return; }
  if (key === 'notebooklm') { startNewWorkflow('notebooklm'); return; }
  if (key === 'open-wizard') { openWizard(); return; }
  if (key === 'add-contact') { openMemoryModal('contacts'); return; }
  if (key === 'add-follow-up') { openMemoryModal('follow-ups'); return; }
  if (key === 'edit-memory') { openMemoryModal('contacts'); return; }
}

async function markFollowUpDone(id) {
  if (!id) return;
  try {
    const res = await fetch(`${BACKEND}/memory/follow-ups/${encodeURIComponent(id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'done' }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    await loadDashboard();
  } catch (err) {
    debugLog('follow_up.mark_done_failed', { error: err && err.message });
  }
}

function _handleDashboardActionTarget(target) {
  if (!target) return;
  if (target === 'wizard') {
    openWizard();
  } else if (target.startsWith('workflow:')) {
    const mode = target.split(':')[1];
    if (mode === 'agentic' || mode === 'notebooklm' || mode === 'business' || mode === 'social') {
      startNewWorkflow(mode);
    }
  } else if (target.startsWith('memory:')) {
    const tab = target.split(':')[1];
    openMemoryModal(tab);
  } else if (target.startsWith('run:')) {
    const folder = target.slice(4);
    if (folder) openProjectFromSidebar({ artifact_folder: folder });
  }
}

/* ---------- Memory modal ---------- */

let memoryActiveTab = 'contacts';

function openMemoryModal(tab) {
  const modal = document.getElementById('memory-modal');
  if (!modal) return;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  document.addEventListener('keydown', _handleMemoryKeydown);
  if (tab) _switchMemoryTab(tab);
  else _switchMemoryTab(memoryActiveTab);
}

function closeMemoryModal() {
  const modal = document.getElementById('memory-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  document.removeEventListener('keydown', _handleMemoryKeydown);
  loadDashboard(); // refresh counts after possible edits
}

function _handleMemoryKeydown(e) {
  if (e.key === 'Escape') { e.preventDefault(); closeMemoryModal(); }
}

function _switchMemoryTab(tab) {
  memoryActiveTab = tab;
  document.querySelectorAll('[data-memory-tab]').forEach((t) => {
    t.classList.toggle('is-active', t.getAttribute('data-memory-tab') === tab);
  });
  document.querySelectorAll('[data-memory-panel]').forEach((p) => {
    p.classList.toggle('hidden', p.getAttribute('data-memory-panel') !== tab);
  });
  if (tab === 'contacts') loadMemoryContacts();
  else if (tab === 'brand') loadMemoryBrand();
  else if (tab === 'facts') loadMemoryFacts();
  else if (tab === 'follow-ups') loadMemoryFollowUps();
  else if (tab === 'decisions') loadMemoryDecisions();
}

function _setMemoryStatus(text, kind) {
  const el = document.getElementById('memory-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'modal-status';
  if (kind === 'ok') el.classList.add('is-ok');
  if (kind === 'err') el.classList.add('is-err');
}

async function loadMemoryContacts() {
  const listEl = document.getElementById('memory-contacts-list');
  if (!listEl) return;
  try {
    const res = await fetch(`${BACKEND}/memory/contacts`);
    const data = await res.json();
    const items = (data && data.contacts) || [];
    listEl.innerHTML = '';
    if (!items.length) {
      listEl.innerHTML = '<li class="memory-empty">No contacts yet.</li>';
      return;
    }
    items.forEach((c) => {
      const li = document.createElement('li');
      li.className = 'memory-list-item';
      const meta = [c.role, c.company, c.email, c.phone].filter(Boolean).join(' · ');
      li.innerHTML = `
        <span class="memory-list-item-text">
          <span class="memory-list-item-title">${escapeHtml(c.name || '(no name)')}</span>
          ${meta ? `<span class="memory-list-item-meta">${escapeHtml(meta)}</span>` : ''}
          ${c.notes ? `<span class="memory-list-item-meta">${escapeHtml(c.notes)}</span>` : ''}
        </span>
        <span class="memory-list-item-actions"></span>
      `;
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'memory-delete-btn';
      del.textContent = 'Delete';
      del.addEventListener('click', () => deleteMemoryEntry('contacts', c.id, loadMemoryContacts));
      li.querySelector('.memory-list-item-actions').appendChild(del);
      listEl.appendChild(li);
    });
  } catch (err) {
    _setMemoryStatus('Could not load contacts.', 'err');
  }
}

async function loadMemoryFacts() {
  const listEl = document.getElementById('memory-facts-list');
  if (!listEl) return;
  try {
    const res = await fetch(`${BACKEND}/memory/facts`);
    const data = await res.json();
    const items = (data && data.facts) || [];
    listEl.innerHTML = '';
    if (!items.length) {
      listEl.innerHTML = '<li class="memory-empty">No facts yet.</li>';
      return;
    }
    items.forEach((f) => {
      const li = document.createElement('li');
      li.className = 'memory-list-item';
      const meta = [f.topic, f.source].filter(Boolean).join(' · ');
      li.innerHTML = `
        <span class="memory-list-item-text">
          <span class="memory-list-item-title">${escapeHtml(f.fact || '')}</span>
          ${meta ? `<span class="memory-list-item-meta">${escapeHtml(meta)}</span>` : ''}
        </span>
        <span class="memory-list-item-actions"></span>
      `;
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'memory-delete-btn';
      del.textContent = 'Delete';
      del.addEventListener('click', () => deleteMemoryEntry('facts', f.id, loadMemoryFacts));
      li.querySelector('.memory-list-item-actions').appendChild(del);
      listEl.appendChild(li);
    });
  } catch (err) {
    _setMemoryStatus('Could not load facts.', 'err');
  }
}

async function loadMemoryFollowUps() {
  const listEl = document.getElementById('memory-follow-ups-list');
  if (!listEl) return;
  try {
    const res = await fetch(`${BACKEND}/memory/follow-ups`);
    const data = await res.json();
    const items = (data && data.follow_ups) || [];
    listEl.innerHTML = '';
    if (!items.length) {
      listEl.innerHTML = '<li class="memory-empty">No follow-ups yet.</li>';
      return;
    }
    items.forEach((f) => {
      const li = document.createElement('li');
      li.className = 'memory-list-item';
      const meta = [f.who, f.due_iso, f.status === 'done' ? '✓ done' : 'open'].filter(Boolean).join(' · ');
      li.innerHTML = `
        <span class="memory-list-item-text">
          <span class="memory-list-item-title">${escapeHtml(f.what || '')}</span>
          ${meta ? `<span class="memory-list-item-meta">${escapeHtml(meta)}</span>` : ''}
        </span>
        <span class="memory-list-item-actions"></span>
      `;
      const actions = li.querySelector('.memory-list-item-actions');
      if (f.status !== 'done') {
        const doneBtn = document.createElement('button');
        doneBtn.type = 'button';
        doneBtn.className = 'memory-delete-btn';
        doneBtn.textContent = 'Mark done';
        doneBtn.addEventListener('click', async () => {
          await fetch(`${BACKEND}/memory/follow-ups/${encodeURIComponent(f.id)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'done' }),
          });
          loadMemoryFollowUps();
        });
        actions.appendChild(doneBtn);
      }
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'memory-delete-btn';
      del.textContent = 'Delete';
      del.addEventListener('click', () => deleteMemoryEntry('follow-ups', f.id, loadMemoryFollowUps));
      actions.appendChild(del);
      listEl.appendChild(li);
    });
  } catch (err) {
    _setMemoryStatus('Could not load follow-ups.', 'err');
  }
}

async function loadMemoryDecisions() {
  const listEl = document.getElementById('memory-decisions-list');
  if (!listEl) return;
  try {
    const res = await fetch(`${BACKEND}/memory/decisions`);
    const data = await res.json();
    const items = (data && data.decisions) || [];
    listEl.innerHTML = '';
    if (!items.length) {
      listEl.innerHTML = '<li class="memory-empty">No decisions logged yet.</li>';
      return;
    }
    items.forEach((d) => {
      const li = document.createElement('li');
      li.className = 'memory-list-item';
      const meta = [d.date_iso, d.context].filter(Boolean).join(' · ');
      li.innerHTML = `
        <span class="memory-list-item-text">
          <span class="memory-list-item-title">${escapeHtml(d.decision || '')}</span>
          ${meta ? `<span class="memory-list-item-meta">${escapeHtml(meta)}</span>` : ''}
        </span>
        <span class="memory-list-item-actions"></span>
      `;
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'memory-delete-btn';
      del.textContent = 'Delete';
      del.addEventListener('click', () => deleteMemoryEntry('decisions', d.id, loadMemoryDecisions));
      li.querySelector('.memory-list-item-actions').appendChild(del);
      listEl.appendChild(li);
    });
  } catch (err) {
    _setMemoryStatus('Could not load decisions.', 'err');
  }
}

async function loadMemoryBrand() {
  try {
    const res = await fetch(`${BACKEND}/memory/brand`);
    const brand = await res.json();
    document.querySelectorAll('[data-brand]').forEach((el) => {
      const [section, field] = el.getAttribute('data-brand').split('.');
      const val = (brand[section] && brand[section][field]) || '';
      if (field === 'do' || field === 'avoid') {
        el.value = Array.isArray(val) ? val.join(', ') : '';
      } else {
        el.value = val || '';
      }
    });
  } catch (err) {
    _setMemoryStatus('Could not load brand.', 'err');
  }
}

async function saveBrandForm(e) {
  if (e) e.preventDefault();
  const payload = { ridian: {}, open_gulf: {}, buns: {} };
  document.querySelectorAll('[data-brand]').forEach((el) => {
    const [section, field] = el.getAttribute('data-brand').split('.');
    if (field === 'do' || field === 'avoid') {
      payload[section][field] = el.value.split(',').map((s) => s.trim()).filter(Boolean);
    } else {
      payload[section][field] = el.value;
    }
  });
  try {
    const res = await fetch(`${BACKEND}/memory/brand`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _setMemoryStatus('Brand saved.', 'ok');
    setTimeout(() => _setMemoryStatus(''), 2000);
  } catch (err) {
    _setMemoryStatus('Could not save brand.', 'err');
  }
}

async function deleteMemoryEntry(kind, id, reload) {
  if (!id) return;
  if (!window.confirm('Delete this entry?')) return;
  try {
    const res = await fetch(`${BACKEND}/memory/${kind}/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    if (typeof reload === 'function') reload();
  } catch (err) {
    _setMemoryStatus('Could not delete.', 'err');
  }
}

function _wireMemoryForms() {
  const contactForm = document.getElementById('memory-contact-form');
  if (contactForm) contactForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(contactForm);
    const payload = Object.fromEntries(fd.entries());
    if (!payload.name && !payload.email) { _setMemoryStatus('Name or email required.', 'err'); return; }
    try {
      const res = await fetch(`${BACKEND}/memory/contacts`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      contactForm.reset();
      _setMemoryStatus('Contact added.', 'ok');
      setTimeout(() => _setMemoryStatus(''), 1500);
      loadMemoryContacts();
    } catch (err) { _setMemoryStatus('Could not add contact.', 'err'); }
  });

  const factForm = document.getElementById('memory-fact-form');
  if (factForm) factForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(factForm);
    const payload = Object.fromEntries(fd.entries());
    if (!payload.fact) { _setMemoryStatus('Fact required.', 'err'); return; }
    try {
      const res = await fetch(`${BACKEND}/memory/facts`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      factForm.reset();
      _setMemoryStatus('Fact added.', 'ok');
      setTimeout(() => _setMemoryStatus(''), 1500);
      loadMemoryFacts();
    } catch (err) { _setMemoryStatus('Could not add fact.', 'err'); }
  });

  const fuForm = document.getElementById('memory-follow-up-form');
  if (fuForm) fuForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(fuForm);
    const payload = Object.fromEntries(fd.entries());
    if (!payload.what) { _setMemoryStatus('What is required.', 'err'); return; }
    try {
      const res = await fetch(`${BACKEND}/memory/follow-ups`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      fuForm.reset();
      _setMemoryStatus('Follow-up added.', 'ok');
      setTimeout(() => _setMemoryStatus(''), 1500);
      loadMemoryFollowUps();
    } catch (err) { _setMemoryStatus('Could not add follow-up.', 'err'); }
  });

  const decForm = document.getElementById('memory-decision-form');
  if (decForm) decForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(decForm);
    const payload = Object.fromEntries(fd.entries());
    if (!payload.decision) { _setMemoryStatus('Decision required.', 'err'); return; }
    try {
      const res = await fetch(`${BACKEND}/memory/decisions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      decForm.reset();
      _setMemoryStatus('Decision logged.', 'ok');
      setTimeout(() => _setMemoryStatus(''), 1500);
      loadMemoryDecisions();
    } catch (err) { _setMemoryStatus('Could not log decision.', 'err'); }
  });

  const brandForm = document.getElementById('memory-brand-form');
  if (brandForm) brandForm.addEventListener('submit', saveBrandForm);
}

// Wire memory tabs + close button
document.querySelectorAll('[data-memory-tab]').forEach((tab) => {
  tab.addEventListener('click', () => _switchMemoryTab(tab.getAttribute('data-memory-tab')));
});
const memoryCloseBtn = document.getElementById('memory-close-btn');
if (memoryCloseBtn) memoryCloseBtn.addEventListener('click', closeMemoryModal);

// Dashboard buttons
if (els.sidebarDashboardBtn) {
  els.sidebarDashboardBtn.addEventListener('click', () => setWorkspaceView('welcome'));
}
const dashEditMemoryBtn = document.getElementById('dashboard-edit-memory-btn');
if (dashEditMemoryBtn) dashEditMemoryBtn.addEventListener('click', () => openMemoryModal('contacts'));
const dashAddFollowUpBtn = document.getElementById('dashboard-add-follow-up-btn');
if (dashAddFollowUpBtn) dashAddFollowUpBtn.addEventListener('click', () => openMemoryModal('follow-ups'));
const dashNewWorkflowBtn = document.getElementById('dashboard-new-workflow-btn');
if (dashNewWorkflowBtn) dashNewWorkflowBtn.addEventListener('click', () => openWizard());

// Quick Launch row
document.querySelectorAll('[data-ql]').forEach((btn) => {
  btn.addEventListener('click', () => _handleQuickLaunch(btn.getAttribute('data-ql')));
});

_wireMemoryForms();

/* ============================================================ */
/*                       INITIAL LOAD                            */
/* ============================================================ */

buildPromptLibrary();
buildSocialPromptLibrary();
wireCopyButtons();
startHealthPolling();
loadGoogleStatus();
loadRecentRunsFromBackend();
setMode('business');
setWorkspaceView('welcome');

// Load saved theme on startup
fetch(`${BACKEND}/settings`).then(r => r.ok ? r.json() : null).then(data => {
  if (data) { cachedSettings = data; applyTheme(data.appearance); }
}).catch(() => { applyTheme('system'); });
