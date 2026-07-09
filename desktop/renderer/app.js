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
// Bool fields are stored on the backend as "true"/"false" strings but rendered
// as checkboxes here. Handled separately because FormData omits unchecked
// boxes entirely (which would otherwise look like "unset" instead of "false").
const SETTINGS_BOOL_FIELDS = ['operator_auto_upload_drive'];

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
    updateWorkspaceHeader('Ridian Operator', '');
    loadDashboard();
    // Defensive: never let a missing element / typo in the v1.5 context
    // strip code throw out of setWorkspaceView. If this raised, the
    // single-pane class toggle (further down) would not run, leaving the
    // sidebar half-mounted + the workspace collapsed.
    try { loadOperatorContextStrip(); } catch (_) {}
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

  // v1.5 single-pane: the operator (welcome) view is sidebar-less; the
  // legacy workflow views (input / run) keep the sidebar so the mode +
  // outputs + recent-runs nav stay reachable inside that flow.
  const shell = document.getElementById('app-shell');
  if (shell) shell.classList.toggle('single-pane', view === 'welcome');
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
  else if (run.workflow === 'operator') labelChannel = 'Operator';
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
    // Operator runs rehydrate into the Operator panel — read operation_log.json
    // from disk, restore operatorState, re-render the timeline + artifacts, and
    // wire the audio player. No prompt-wrapper card UI.
    if (data.workflow === 'operator') {
      activeRunFolder = data.artifact_folder;
      renderRecentRuns();
      await loadOperatorRun({ artifact_folder: data.artifact_folder, name: data.name });
      return;
    }
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

/* ----- Web Speech voice selection (Option A) ----------------------------
 * speechSynthesis delegates to the OS voices. The legacy Windows SAPI
 * voices ("Microsoft David/Zira Desktop") sound robotic; newer
 * "Natural"/"Neural"/"Online" voices sound far better. We cache the voice
 * list (getVoices() populates asynchronously, hence the 'voiceschanged'
 * refresh) and explicitly pick the best-installed English voice instead of
 * letting the system default play. Returns null when nothing is installed,
 * in which case we leave utter.voice unset (OS default).
 * --------------------------------------------------------------------- */
let _voiceCache = [];
function _refreshVoiceCache() {
  try { _voiceCache = window.speechSynthesis.getVoices() || []; }
  catch (_) { _voiceCache = []; }
}
if (typeof window !== 'undefined' && window.speechSynthesis) {
  _refreshVoiceCache();
  try { window.speechSynthesis.addEventListener('voiceschanged', _refreshVoiceCache); }
  catch (_) {}
}
function _pickBestVoice() {
  if (!_voiceCache.length) _refreshVoiceCache();
  if (!_voiceCache.length) return null;
  const enVoices = _voiceCache.filter((v) => /^en[-_]?/i.test(v.lang || ''));
  const pool = enVoices.length ? enVoices : _voiceCache;
  const scoreOf = (v) => {
    const name = (v.name || '').toLowerCase();
    const lang = (v.lang || '').toLowerCase();
    let s = 0;
    if (name.includes('natural')) s += 100;
    if (name.includes('neural')) s += 90;
    if (name.includes('online')) s += 60;       // Edge/Azure-backed, very natural
    if (/\b(aria|jenny|guy|andrew|ava|emma|brian|michelle)\b/.test(name)) s += 40;
    if (name.includes('zira') || name.includes('mark')) s += 10; // legacy SAPI
    if (name.includes('david')) s += 5;
    if (lang === 'en-us') s += 8;
    if (v.localService === false) s += 3;        // network neural voices
    if (v.default) s += 2;
    return s;
  };
  let best = null, bestScore = -1;
  for (const v of pool) {
    const s = scoreOf(v);
    if (s > bestScore) { best = v; bestScore = s; }
  }
  return best;
}
function _applyBestVoice(utter) {
  const v = _pickBestVoice();
  if (v) { utter.voice = v; utter.lang = v.lang || 'en-US'; }
  return utter;
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
  _applyBestVoice(utt);
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

const THEME_STORAGE_KEY = 'ridian.appearance';

function _readSavedTheme() {
  try { return window.localStorage.getItem(THEME_STORAGE_KEY) || ''; }
  catch (_) { return ''; }
}

function _writeSavedTheme(pref) {
  try {
    if (pref) window.localStorage.setItem(THEME_STORAGE_KEY, pref);
    else window.localStorage.removeItem(THEME_STORAGE_KEY);
  } catch (_) { /* private mode / disabled storage */ }
}

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
  _writeSavedTheme(val);
}

// Apply the saved theme IMMEDIATELY at script eval time so the UI doesn't
// flash light on startup while the backend /settings fetch is still in
// flight, and so a slow or unreachable backend doesn't wipe the user's
// preference back to 'system'. The later /settings fetch updates this
// once a fresh value is available.
(function bootstrapTheme() {
  const saved = _readSavedTheme();
  applyTheme(saved || 'system');
})();

// Listen for OS theme changes when in system mode
try {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    const saved = (cachedSettings && cachedSettings.appearance) || _readSavedTheme() || 'system';
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
  SETTINGS_BOOL_FIELDS.forEach((name) => {
    const input = els.settingsForm.elements.namedItem(name);
    if (!input) return;
    // Treat "true"/"1"/"yes"/"on" as checked; everything else (including
    // empty / missing) as unchecked. operator_auto_upload_drive defaults
    // server-side to "true" but if the key is missing entirely we still
    // want the box checked on first launch — mirror the backend default.
    const raw = (settings[name] == null ? 'true' : String(settings[name])).toLowerCase().trim();
    input.checked = (raw === 'true' || raw === '1' || raw === 'yes' || raw === 'on');
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
  // Bool fields: read .checked directly because FormData omits unchecked boxes,
  // and "omitted" on the backend means "leave alone" rather than "false."
  SETTINGS_BOOL_FIELDS.forEach((name) => {
    const input = els.settingsForm.elements.namedItem(name);
    if (input) payload[name] = input.checked ? 'true' : 'false';
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
    // Surface the Drive root-folder validation result inline. The save
    // itself succeeded; this only tells the operator whether uploads into
    // the configured folder will work.
    if (data.root_folder_validation) {
      renderRootFolderValidation(data.root_folder_validation);
      // If validation has anything actionable to say, keep the toast neutral
      // and let the inline message carry the detail.
      setSettingsStatus('Settings saved.', 'ok');
    } else {
      setSettingsStatus('Settings saved.', 'ok');
    }
    setTimeout(() => {
      if (els.settingsStatus && els.settingsStatus.textContent === 'Settings saved.') setSettingsStatus('');
    }, 2500);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    setSettingsStatus(/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg) ? 'Backend is not reachable.' : `Could not save: ${msg}`, 'err');
  } finally { els.settingsSaveBtn.disabled = false; }
}

function renderRootFolderValidation(result) {
  const status = document.getElementById('settings-root-folder-status');
  if (!status) return;
  status.classList.remove('is-ok', 'is-err', 'is-warn');
  if (!result) { status.textContent = ''; return; }
  if (result.ok && result.blank) {
    status.classList.add('is-ok');
    status.textContent = result.detail || 'No root folder set — Ridian will manage its own folder.';
  } else if (result.ok) {
    status.classList.add('is-ok');
    status.textContent = result.detail || `Connected to '${result.folder_name || result.folder_id}'.`;
  } else {
    status.classList.add('is-err');
    status.textContent = result.detail || 'Folder is not accessible.';
  }
}

async function testRootFolderAccess() {
  const input = document.getElementById('settings-root-folder-input');
  const status = document.getElementById('settings-root-folder-status');
  if (!input || !status) return;
  const value = (input.value || '').trim();
  status.classList.remove('is-ok', 'is-err', 'is-warn');
  status.textContent = 'Checking Drive access…';
  try {
    const res = await fetch(
      `${BACKEND}/google/validate-root-folder?folder_id_or_url=${encodeURIComponent(value)}`,
    );
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = (data && data.detail) || `HTTP ${res.status}`;
      status.classList.add('is-err');
      status.textContent = typeof msg === 'string' ? msg : JSON.stringify(msg);
      return;
    }
    renderRootFolderValidation(data);
  } catch (err) {
    const msg = err && err.message ? err.message : String(err);
    status.classList.add('is-err');
    status.textContent = /Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
      ? 'Backend is not reachable.'
      : `Check failed: ${msg}`;
  }
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
const _rootFolderTestBtn = document.getElementById('settings-root-folder-test-btn');
if (_rootFolderTestBtn) _rootFolderTestBtn.addEventListener('click', testRootFolderAccess);
// When the input changes, clear stale validation copy so the user isn't
// staring at a green "connected" line for a folder they just edited.
const _rootFolderInput = document.getElementById('settings-root-folder-input');
if (_rootFolderInput) _rootFolderInput.addEventListener('input', () => {
  const status = document.getElementById('settings-root-folder-status');
  if (status) { status.textContent = ''; status.classList.remove('is-ok', 'is-err', 'is-warn'); }
});
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
    else if (r.workflow === 'operator') { channel = 'Operator'; icon = 'O'; }
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
  else if (tab === 'profile') loadMemoryProfile();
  else if (tab === 'brand') loadMemoryBrand();
  else if (tab === 'facts') loadMemoryFacts();
  else if (tab === 'follow-ups') loadMemoryFollowUps();
  else if (tab === 'decisions') loadMemoryDecisions();
}

async function loadMemoryProfile() {
  try {
    const res = await fetch(`${BACKEND}/memory/profile`);
    const profile = await res.json();
    document.querySelectorAll('[data-profile]').forEach((el) => {
      const field = el.getAttribute('data-profile');
      el.value = profile[field] || '';
    });
  } catch (err) {
    _setMemoryStatus('Could not load profile.', 'err');
  }
}

async function saveProfileForm(e) {
  if (e) e.preventDefault();
  const payload = {};
  document.querySelectorAll('[data-profile]').forEach((el) => {
    payload[el.getAttribute('data-profile')] = (el.value || '').trim();
  });
  _setMemoryStatus('Saving profile…');
  try {
    const res = await fetch(`${BACKEND}/memory/profile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _setMemoryStatus('Profile saved. Every operation now uses this context.', 'ok');
  } catch (err) {
    _setMemoryStatus(`Could not save profile: ${err.message || err}`, 'err');
  }
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
  const profileForm = document.getElementById('memory-profile-form');
  if (profileForm) profileForm.addEventListener('submit', saveProfileForm);
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

// Load saved theme on startup. The bootstrap above already applied the
// localStorage-cached value synchronously, so this fetch only refines the
// theme if the backend disagrees. If the backend is unreachable we keep
// whatever the bootstrap chose — do NOT fall back to 'system' and wipe the
// user's saved preference.
fetch(`${BACKEND}/settings`).then(r => r.ok ? r.json() : null).then(data => {
  if (data) { cachedSettings = data; if (data.appearance) applyTheme(data.appearance); }
}).catch(() => { /* keep the bootstrap theme */ });


/* ============================================================ */
/*               OPERATOR v1 — natural-command surface          */
/* ============================================================ */
/*
 * SSE protocol (from /operations/run):
 *   start    | step | artifact | message | error | complete | end
 *
 * Streams via fetch + ReadableStream (EventSource doesn't allow POST).
 */

const OPERATOR = {
  form:           document.getElementById('operator-form'),
  command:        document.getElementById('operator-command'),
  // Conversational thread (Phase 1): frozen past turns + the live current turn.
  scroll:         document.getElementById('operator-scroll'),
  thread:         document.getElementById('operator-thread'),
  live:           document.getElementById('operator-live'),
  runBtn:         document.getElementById('operator-run-btn'),
  cancelBtn:      document.getElementById('operator-cancel-btn'),
  elapsed:        document.getElementById('operator-elapsed'),
  active:         document.getElementById('operator-active'),
  statusDot:      document.getElementById('operator-active-status-dot'),
  statusLabel:    document.getElementById('operator-active-status-label'),
  folder:         document.getElementById('operator-active-folder'),
  timeline:       document.getElementById('operator-timeline'),
  artifactsCard:  document.getElementById('operator-artifacts-card'),
  artifactsList:  document.getElementById('operator-artifacts-list'),
  audioPlayer:    document.getElementById('operator-audio-player'),
  audio:          document.getElementById('operator-audio'),
  audioName:      document.getElementById('operator-audio-name'),
  errors:         document.getElementById('operator-errors'),
  status:         document.getElementById('operator-actions-status'),
  openFolderBtn:  document.getElementById('operator-open-folder'),
  uploadDriveBtn: document.getElementById('operator-upload-drive'),
  emailMeBtn:     document.getElementById('operator-email-me'),
  // v1.2: memory proposals panel
  proposalsPanel:    document.getElementById('operator-proposals'),
  proposalsList:     document.getElementById('operator-proposals-list'),
  proposalsConfirmAll: document.getElementById('operator-proposals-confirm-all'),
  proposalsDismissAll: document.getElementById('operator-proposals-dismiss-all'),
  proposalsStatus:   document.getElementById('operator-proposals-status'),
};

const operatorState = {
  abortController: null,
  active: null,        // { id, artifact_folder, command, intent }
  finalRecord: null,   // populated on 'complete'
  artifacts: [],
  elapsedTimer: null,
  elapsedStart: null,
  drive: null,         // { drive_path, drive_folder_url, uploaded_files }  set after a successful upload
  proposals: [],       // v1.2: planner-proposed memory updates awaiting user decision
  receipt: '',         // v1.7: the planner's final receipt text (shown + spoken)
};

function _opSetStatus(text, kind) {
  if (!OPERATOR.status) return;
  OPERATOR.status.textContent = text || '';
  OPERATOR.status.className = 'operator-actions-status';
  if (kind === 'ok') OPERATOR.status.classList.add('is-ok');
  if (kind === 'err') OPERATOR.status.classList.add('is-err');
}

function _opSetRunning(running) {
  operatorState.running = running;
  if (OPERATOR.runBtn) {
    // Icon button — don't overwrite the SVG; reflect state via class + label.
    OPERATOR.runBtn.classList.toggle('is-running', running);
    OPERATOR.runBtn.setAttribute('aria-label', running ? 'Running…' : 'Send');
  }
  if (OPERATOR.cancelBtn) OPERATOR.cancelBtn.classList.toggle('hidden', !running);
  _opUpdateSendEnabled();
}

// The send arrow is enabled only when there's text AND no run is in flight
// (standard chat behavior — greyed when the field is empty).
function _opUpdateSendEnabled() {
  if (!OPERATOR.runBtn) return;
  const hasText = !!(OPERATOR.command && OPERATOR.command.value.trim());
  OPERATOR.runBtn.disabled = !!operatorState.running || !hasText;
}

function _opSetStatusDot(kind) {
  if (!OPERATOR.statusDot) return;
  OPERATOR.statusDot.classList.remove('is-running', 'is-completed', 'is-failed', 'is-partial', 'is-awaiting_input');
  OPERATOR.statusDot.classList.add('is-' + kind);
  if (OPERATOR.statusLabel) {
    OPERATOR.statusLabel.textContent =
      kind === 'running'   ? 'Running…' :
      kind === 'completed' ? 'Completed' :
      kind === 'partial'   ? 'Completed with issues' :
      kind === 'awaiting_input' ? 'Waiting for your answer' :
      kind === 'failed'    ? 'Failed' :
      'Idle';
  }
}

function _opStartElapsed() {
  operatorState.elapsedStart = Date.now();
  if (OPERATOR.elapsed) OPERATOR.elapsed.textContent = '0:00';
  operatorState.elapsedTimer = setInterval(() => {
    const s = Math.floor((Date.now() - operatorState.elapsedStart) / 1000);
    if (OPERATOR.elapsed) {
      OPERATOR.elapsed.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }
  }, 1000);
}

function _opStopElapsed() {
  if (operatorState.elapsedTimer) clearInterval(operatorState.elapsedTimer);
  operatorState.elapsedTimer = null;
}

function _opClearLive() {
  // Remove the current turn's command echo so the next run starts clean.
  if (OPERATOR.active) {
    const echo = OPERATOR.active.querySelector('.operator-command-echo');
    if (echo) echo.remove();
  }
  if (OPERATOR.timeline) OPERATOR.timeline.innerHTML = '';
  if (OPERATOR.artifactsList) OPERATOR.artifactsList.innerHTML = '';
  if (OPERATOR.errors) { OPERATOR.errors.innerHTML = ''; OPERATOR.errors.classList.add('hidden'); }
  if (OPERATOR.artifactsCard) OPERATOR.artifactsCard.classList.add('hidden');
  if (OPERATOR.audioPlayer) OPERATOR.audioPlayer.classList.add('hidden');
  if (OPERATOR.audio) { OPERATOR.audio.pause(); OPERATOR.audio.removeAttribute('src'); }
  if (OPERATOR.folder) OPERATOR.folder.textContent = '';
  if (OPERATOR.proposalsPanel) OPERATOR.proposalsPanel.classList.add('hidden');
  if (OPERATOR.proposalsList) OPERATOR.proposalsList.innerHTML = '';
  if (OPERATOR.proposalsStatus) { OPERATOR.proposalsStatus.textContent = ''; OPERATOR.proposalsStatus.className = 'operator-actions-status'; }
  // v1.7: receipt + needs-input + spoken reply + rendered-error dedupe set
  const receiptEl = document.getElementById('operator-receipt');
  if (receiptEl) receiptEl.classList.add('hidden');
  const receiptText = document.getElementById('operator-receipt-text');
  if (receiptText) receiptText.textContent = '';
  const needsEl = document.getElementById('operator-needs-input');
  if (needsEl) needsEl.classList.add('hidden');
  const needsList = document.getElementById('operator-needs-input-list');
  if (needsList) needsList.innerHTML = '';
  _opStopSpeaking();
  _opRenderedErrors.clear();
  _opSetStatus('');
  operatorState.artifacts = [];
  operatorState.finalRecord = null;
  operatorState.drive = null;
  operatorState.proposals = [];
  operatorState.receipt = '';
}

// Full reset: clear the whole conversation thread (past turns) AND the live
// area. Used when loading a different run from history or starting fresh.
function _opResetUI() {
  if (OPERATOR.thread) OPERATOR.thread.innerHTML = '';
  _opClearLive();
}

// Freeze the finished/aborted current turn into a static block in the thread so
// a new run appends below it instead of wiping it. Cloned nodes lose their
// listeners and ids, so past-turn action buttons are disabled — act on a prior
// run by reopening it from the sidebar history (which reloads it live).
function _opArchiveCurrentTurn() {
  if (!OPERATOR.thread || !OPERATOR.live) return;
  const hasRun = (operatorState.active && operatorState.active.command)
    || operatorState.finalRecord
    || (OPERATOR.timeline && OPERATOR.timeline.children.length);
  if (!hasRun) return;
  const blocks = [
    OPERATOR.active,
    document.getElementById('operator-needs-input'),
    OPERATOR.artifactsCard,
  ];
  const turn = document.createElement('div');
  turn.className = 'operator-turn';
  let any = false;
  for (const el of blocks) {
    if (!el || el.classList.contains('hidden')) continue;
    const clone = el.cloneNode(true);
    clone.removeAttribute('id');
    clone.classList.add('is-frozen');
    clone.querySelectorAll('[id]').forEach((n) => n.removeAttribute('id'));
    clone.querySelectorAll('button, input, textarea, audio').forEach((b) => {
      try { b.disabled = true; } catch (_) {}
      b.classList.add('is-frozen');
    });
    turn.appendChild(clone);
    any = true;
  }
  if (any) OPERATOR.thread.appendChild(turn);
}

// Keep newest content in view as events stream (chat behavior).
function _opScrollToBottom() {
  const s = OPERATOR.scroll;
  if (s) s.scrollTop = s.scrollHeight;
}

const STEP_LABELS = {
  interpret: 'Interpreting command',
  research:  'Researching the live web',
  script:    'Writing audiobook script',
  audio:     'Synthesizing audio',
};

function _opRenderStep(step) {
  if (!OPERATOR.timeline) return;
  let li = OPERATOR.timeline.querySelector(`[data-step="${step.name}"]`);
  if (!li) {
    li = document.createElement('li');
    li.className = 'operator-step';
    li.setAttribute('data-step', step.name);
    li.innerHTML = `
      <span class="operator-step-icon"></span>
      <span class="operator-step-body">
        <span class="operator-step-name"></span>
        <span class="operator-step-detail"></span>
      </span>
      <span class="operator-step-time"></span>
    `;
    OPERATOR.timeline.appendChild(li);
  }
  li.classList.remove('is-running', 'is-completed', 'is-failed', 'is-skipped');
  li.classList.add('is-' + step.status);
  li.querySelector('.operator-step-name').textContent = STEP_LABELS[step.name] || step.name;
  li.querySelector('.operator-step-detail').textContent = step.detail || '';
  const icon = li.querySelector('.operator-step-icon');
  icon.textContent =
    step.status === 'completed' ? '✓' :
    step.status === 'failed'    ? '!' :
    step.status === 'skipped'   ? '–' :
    '•';
  const time = li.querySelector('.operator-step-time');
  if (step.started_at && step.completed_at) {
    const dt = (new Date(step.completed_at).getTime() - new Date(step.started_at).getTime()) / 1000;
    time.textContent = `${dt.toFixed(0)}s`;
  } else {
    time.textContent = '';
  }
}

// External artifact kinds: live things in Google's apps, not local files.
// Each renders an anchor that opens in the default browser.
const OPERATOR_EXTERNAL_KINDS = {
  gmail_draft:  { label: 'Open in Gmail',  meta: 'Gmail draft (sits in Drafts until you send)' },
  drive_folder: { label: 'Open in Drive',  meta: 'Google Drive folder (auto-filed by Ridian)' },
  spreadsheet:  { label: 'Open in Sheets', meta: 'Live Google Sheet (in your Drive)' },
  slides:       { label: 'Open in Slides', meta: 'Live Google Slides deck (in your Drive)' },
  browser:      { label: 'Open again',     meta: 'Opened in your browser' },
};

function _opIconForKind(kind) {
  if (kind === 'audio') return '♪';
  if (kind === 'markdown') return 'M';
  if (kind === 'json') return 'J';
  if (kind === 'gmail_draft') return '✉';
  if (kind === 'drive_folder') return '☁';
  if (kind === 'spreadsheet') return '⊞';
  if (kind === 'slides') return '▤';
  if (kind === 'browser') return '🌐';
  return '·';
}

function _opRenderArtifact(art) {
  if (!OPERATOR.artifactsCard) return;
  OPERATOR.artifactsCard.classList.remove('hidden');
  // Replace if same name already present (avoid duplicates).
  const existing = OPERATOR.artifactsList.querySelector(`[data-artifact-name="${art.name}"]`);
  if (existing) existing.remove();

  const li = document.createElement('li');
  li.className = 'operator-artifact-item';
  li.setAttribute('data-artifact-name', art.name);
  // External-artifact meta lines (don't show the raw kind token).
  const extKind = OPERATOR_EXTERNAL_KINDS[art.kind];
  const metaLine = extKind ? extKind.meta : art.kind;
  li.innerHTML = `
    <span class="operator-artifact-icon" aria-hidden="true">${escapeHtml(_opIconForKind(art.kind))}</span>
    <span>
      <span class="operator-artifact-name">${escapeHtml(art.name)}</span>
      <span class="operator-artifact-meta">${escapeHtml(metaLine)}</span>
    </span>
  `;

  if (extKind && art.path && art.path.startsWith('http')) {
    // External artifacts (Gmail draft, Drive folder, Sheet, Slides deck)
    // open in the user's default browser. Electron renderer respects
    // target=_blank for http(s) URLs via shell.openExternal.
    const openA = document.createElement('a');
    openA.className = 'operator-artifact-open';
    openA.textContent = extKind.label;
    openA.href = art.path;
    openA.target = '_blank';
    openA.rel = 'noopener noreferrer';
    li.appendChild(openA);
  } else {
    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'operator-artifact-open';
    openBtn.textContent = 'Open';
    openBtn.addEventListener('click', () => _opOpenArtifactFile(art.name));
    li.appendChild(openBtn);
  }
  OPERATOR.artifactsList.appendChild(li);

  if (art.kind === 'audio' && art.name.toLowerCase().endsWith('.mp3')) {
    _opShowAudio(art.name);
  }
}

function _opShowAudio(filename) {
  if (!OPERATOR.audio || !operatorState.active || !operatorState.active.artifact_folder) return;
  const url = `${BACKEND}/operations/audio?artifact_folder=${encodeURIComponent(operatorState.active.artifact_folder)}&filename=${encodeURIComponent(filename)}`;
  OPERATOR.audio.src = url;
  if (OPERATOR.audioName) OPERATOR.audioName.textContent = filename;
  if (OPERATOR.audioPlayer) OPERATOR.audioPlayer.classList.remove('hidden');
}

// v1.7: identical repeated errors render once (with a ×N counter) instead of
// six red rows — the wall-of-red from repeated Gmail 403s taught us that.
const _opRenderedErrors = new Map();  // message → <li>

function _opRenderError(message) {
  if (!OPERATOR.errors) return;
  OPERATOR.errors.classList.remove('hidden');
  if (!OPERATOR.errors.querySelector('.operator-errors-title')) {
    OPERATOR.errors.innerHTML = `
      <div class="operator-errors-title">Issues during this operation</div>
      <ul class="operator-errors-list"></ul>
    `;
    _opRenderedErrors.clear();
  }
  const existing = _opRenderedErrors.get(message);
  if (existing) {
    const n = (parseInt(existing.dataset.count || '1', 10) || 1) + 1;
    existing.dataset.count = String(n);
    existing.textContent = `${message}  (×${n})`;
    return;
  }
  const ul = OPERATOR.errors.querySelector('.operator-errors-list');
  const li = document.createElement('li');
  li.textContent = message;
  li.dataset.count = '1';
  ul.appendChild(li);
  _opRenderedErrors.set(message, li);
}

/* ----- v1.7: receipt + needs-input + voice ----- */

function _opRenderReceipt(text) {
  const card = document.getElementById('operator-receipt');
  const body = document.getElementById('operator-receipt-text');
  if (!card || !body) return;
  body.textContent = text;
  card.classList.remove('hidden');
}

// interactive=false when replaying from history (session is gone, so option
// buttons are inert — clicking would hit "operation no longer active").
function _opRenderNeedsInput(need, interactive = true) {
  const card = document.getElementById('operator-needs-input');
  const list = document.getElementById('operator-needs-input-list');
  if (!card || !list) return;
  // Dedupe by id on rehydrate + live double-fires.
  if (need.id && list.querySelector(`[data-need-id="${need.id}"]`)) return;
  const li = document.createElement('li');
  li.className = 'operator-needs-input-item';
  if (need.id) li.setAttribute('data-need-id', need.id);

  const q = document.createElement('div');
  q.className = 'operator-needs-input-q';
  q.textContent = need.question || '';
  li.appendChild(q);
  if (need.context_hint) {
    const hint = document.createElement('span');
    hint.className = 'operator-needs-input-hint';
    hint.textContent = need.context_hint;
    li.appendChild(hint);
  }

  const options = Array.isArray(need.options) ? need.options : [];
  const answerBtn = document.getElementById('operator-needs-input-answer');

  if (options.length) {
    // Structured question → tappable choice buttons (the tool declared them).
    const row = document.createElement('div');
    row.className = 'operator-needs-input-options';
    options.forEach((opt) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'btn btn-compact operator-choice-btn';
      b.textContent = opt.label || opt.value || 'Option';
      const action = opt.action || 'submit';
      if (action === 'disabled' || !interactive) {
        b.disabled = true;
        b.classList.add('is-disabled');
      } else if (action === 'compose') {
        b.classList.add('btn-ghost');
        b.addEventListener('click', () => {
          _opScrollToBottom();
          if (OPERATOR.command) {
            if (opt.placeholder) OPERATOR.command.setAttribute('placeholder', opt.placeholder);
            OPERATOR.command.focus();
          }
        });
      } else if (action === 'upload') {
        b.classList.add('btn-ghost');
        b.addEventListener('click', () => _opAttachPdfClick());  // → uploads to this awaiting op
      } else { // submit → resume the SAME operation with this choice
        b.classList.add('btn-primary');
        b.addEventListener('click', () => {
          if (operatorState.active && operatorState.active.id) {
            _opContinue(operatorState.active.id, opt.value || opt.label || '');
          }
        });
      }
      row.appendChild(b);
    });
    li.appendChild(row);
    if (answerBtn) answerBtn.classList.add('hidden');   // buttons are the answer path
  } else if (answerBtn) {
    // Open-ended question → keep the free-text composer path.
    answerBtn.classList.remove('hidden');
    answerBtn.textContent = "Answer Ridian's question";
  }

  list.appendChild(li);
  card.classList.remove('hidden');
}

const VOICE_REPLIES_KEY = 'ridian.voiceReplies';

function _opVoiceEnabled() {
  try { return window.localStorage.getItem(VOICE_REPLIES_KEY) !== 'false'; }
  catch (_) { return true; }
}

function _opSetVoiceEnabled(on) {
  try { window.localStorage.setItem(VOICE_REPLIES_KEY, on ? 'true' : 'false'); } catch (_) {}
  const chk = document.getElementById('settings-voice-replies');
  if (chk) chk.checked = !!on;
  if (!on) _opStopSpeaking();
}

function _opSetSpeakPressed(on) {
  const btn = document.getElementById('operator-receipt-speak');
  if (btn) btn.setAttribute('aria-pressed', on ? 'true' : 'false');
}

function _opStopSpeaking() {
  try { window.speechSynthesis.cancel(); } catch (_) {}
  _opSetSpeakPressed(false);
}

// force=true: the user clicked the speaker on the receipt — play regardless
// of the auto-read preference (on-demand always works, like ChatGPT's
// per-message "Read aloud").
function _opSpeak(text, force) {
  if (!text) return;
  if (!force && !_opVoiceEnabled()) return;
  try {
    // Strip markdown-ish noise so the OS voice doesn't read asterisks.
    const clean = text.replace(/[*_#`>]+/g, ' ').replace(/\s{2,}/g, ' ').trim();
    if (!clean) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(clean.slice(0, 1200));
    _applyBestVoice(utter);
    utter.rate = 1.05;
    utter.onend = () => _opSetSpeakPressed(false);
    utter.onerror = () => _opSetSpeakPressed(false);
    _opSetSpeakPressed(true);
    window.speechSynthesis.speak(utter);
  } catch (_) { /* speechSynthesis unavailable — silently skip */ }
}

/* ----- v1.7: voice input (MediaRecorder → Whisper) ----- */

const _micState = { recorder: null, chunks: [], timer: null };

async function _opMicToggle() {
  const btn = document.getElementById('operator-mic-btn');
  if (!btn) return;
  if (_micState.recorder && _micState.recorder.state === 'recording') {
    _micState.recorder.stop();
    return;
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    _opSetStatus('Microphone unavailable — check Windows mic permissions for Ridian.', 'err');
    return;
  }
  _micState.chunks = [];
  const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
  _micState.recorder = recorder;
  recorder.ondataavailable = (e) => { if (e.data && e.data.size) _micState.chunks.push(e.data); };
  recorder.onstop = async () => {
    clearTimeout(_micState.timer);
    btn.classList.remove('is-recording');
    btn.classList.add('is-transcribing');
    _opSetStatus('Transcribing…');
    stream.getTracks().forEach((t) => t.stop());
    try {
      const blob = new Blob(_micState.chunks, { type: 'audio/webm' });
      const b64 = await new Promise((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(',')[1] || '');
        r.onerror = reject;
        r.readAsDataURL(blob);
      });
      const res = await fetch(`${BACKEND}/operations/transcribe`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ audio_base64: b64, mime: 'audio/webm' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
      const text = (data.text || '').trim();
      if (text && OPERATOR.command) {
        OPERATOR.command.value = OPERATOR.command.value
          ? OPERATOR.command.value.trimEnd() + ' ' + text
          : text;
        OPERATOR.command.focus();
        _opUpdateSendEnabled();
        _opSetStatus('Heard you. Review and send.', 'ok');
      } else {
        _opSetStatus("Didn't catch that — try again closer to the mic.", 'err');
      }
    } catch (err) {
      _opSetStatus(`Voice input failed: ${err && err.message ? err.message : err}`, 'err');
    } finally {
      btn.classList.remove('is-transcribing');
      _micState.recorder = null;
      _micState.chunks = [];
    }
  };
  recorder.start();
  btn.classList.add('is-recording');
  _opSetStatus('Recording… click the mic again to stop.');
  // Hard cap: auto-stop at 60 seconds.
  _micState.timer = setTimeout(() => {
    if (recorder.state === 'recording') recorder.stop();
  }, 60000);
}

function _opProposalSummary(prop) {
  const k = prop.kind;
  const p = prop.payload || {};
  if (k === 'fact')      return p.fact || '(empty fact)';
  if (k === 'contact')   return [p.name, p.role, p.company].filter(Boolean).join(' · ') || '(empty contact)';
  if (k === 'follow_up') return [p.what, p.due_iso && `due ${p.due_iso}`, p.who && `for ${p.who}`].filter(Boolean).join(' · ') || '(empty follow-up)';
  if (k === 'decision')  return p.decision || '(empty decision)';
  return JSON.stringify(p).slice(0, 200);
}

function _opRenderMemoryProposal(prop) {
  if (!OPERATOR.proposalsPanel || !OPERATOR.proposalsList) return;
  OPERATOR.proposalsPanel.classList.remove('hidden');

  let li = OPERATOR.proposalsList.querySelector(`[data-proposal-id="${prop.id}"]`);
  if (!li) {
    li = document.createElement('li');
    li.className = 'operator-proposal-item';
    li.setAttribute('data-proposal-id', prop.id);
    li.innerHTML = `
      <span class="operator-proposal-kind"></span>
      <span class="operator-proposal-body">
        <span class="operator-proposal-summary"></span>
        <span class="operator-proposal-reason"></span>
      </span>
      <span class="operator-proposal-actions">
        <button type="button" class="operator-proposal-btn is-confirm">Confirm</button>
        <button type="button" class="operator-proposal-btn is-dismiss">Dismiss</button>
      </span>
    `;
    li.querySelector('.operator-proposal-actions .is-confirm')
      .addEventListener('click', () => _opCommitProposals({ confirmed: [prop.id] }));
    li.querySelector('.operator-proposal-actions .is-dismiss')
      .addEventListener('click', () => _opCommitProposals({ dismissed: [prop.id] }));
    OPERATOR.proposalsList.appendChild(li);
  }

  li.querySelector('.operator-proposal-kind').textContent = (prop.kind || '').replace('_', ' ');
  li.querySelector('.operator-proposal-summary').textContent = _opProposalSummary(prop);
  const reasonEl = li.querySelector('.operator-proposal-reason');
  reasonEl.textContent = prop.reason ? `Why: ${prop.reason}` : '';
  _opPaintProposalStatus(li, prop.status || 'proposed');
}

function _opPaintProposalStatus(li, status) {
  li.classList.remove('is-committed', 'is-dismissed');
  if (status === 'committed') li.classList.add('is-committed');
  if (status === 'dismissed') li.classList.add('is-dismissed');
  li.querySelectorAll('.operator-proposal-btn').forEach((b) => {
    b.disabled = status === 'committed' || status === 'dismissed';
  });
  if (status === 'committed' || status === 'dismissed') {
    const actions = li.querySelector('.operator-proposal-actions');
    if (actions && !actions.querySelector('.operator-proposal-tag')) {
      const tag = document.createElement('span');
      tag.className = 'operator-proposal-tag';
      tag.style.fontSize = 'var(--fs-xs)';
      tag.style.color = status === 'committed' ? 'var(--color-success)' : 'var(--color-muted-soft)';
      tag.style.fontWeight = '600';
      tag.style.alignSelf = 'center';
      tag.textContent = status === 'committed' ? 'Saved' : 'Dismissed';
      actions.appendChild(tag);
    }
  }
}

function _opSetProposalsStatus(text, kind) {
  if (!OPERATOR.proposalsStatus) return;
  OPERATOR.proposalsStatus.textContent = text || '';
  OPERATOR.proposalsStatus.className = 'operator-actions-status';
  if (kind === 'ok') OPERATOR.proposalsStatus.classList.add('is-ok');
  if (kind === 'err') OPERATOR.proposalsStatus.classList.add('is-err');
}

async function _opCommitProposals({ confirmed = [], dismissed = [] }) {
  if (!operatorState.active || !operatorState.active.id) {
    _opSetProposalsStatus('No active operation id — cannot save proposals.', 'err');
    return;
  }
  if (!confirmed.length && !dismissed.length) return;
  _opSetProposalsStatus('Saving…');
  try {
    const res = await fetch(`${BACKEND}/operations/${encodeURIComponent(operatorState.active.id)}/memory/commit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed, dismissed }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    (data.written || []).forEach((w) => {
      const li = OPERATOR.proposalsList.querySelector(`[data-proposal-id="${w.id}"]`);
      if (li) _opPaintProposalStatus(li, 'committed');
      const prop = operatorState.proposals.find((p) => p.id === w.id);
      if (prop) prop.status = 'committed';
    });
    (data.dismissed || []).forEach((id) => {
      const li = OPERATOR.proposalsList.querySelector(`[data-proposal-id="${id}"]`);
      if (li) _opPaintProposalStatus(li, 'dismissed');
      const prop = operatorState.proposals.find((p) => p.id === id);
      if (prop) prop.status = 'dismissed';
    });
    const summary = [];
    if ((data.written || []).length) summary.push(`${data.written.length} saved to memory`);
    if ((data.dismissed || []).length) summary.push(`${data.dismissed.length} dismissed`);
    if ((data.skipped || []).length) summary.push(`${data.skipped.length} skipped`);
    _opSetProposalsStatus(summary.join(' · ') || 'Done.', 'ok');
  } catch (err) {
    _opSetProposalsStatus(`Could not save: ${err && err.message ? err.message : err}`, 'err');
  }
}

function _opPendingProposalIds() {
  return operatorState.proposals
    .filter((p) => (p.status || 'proposed') === 'proposed')
    .map((p) => p.id);
}

async function _opParseSSE(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const evt = _opParseSSEBlock(rawEvent);
      if (evt) _opHandleEvent(evt);
    }
  }
}

function _opParseSSEBlock(block) {
  let event = 'message';
  const dataLines = [];
  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(':')) continue;
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  let data = null;
  try { data = JSON.parse(dataLines.join('\n')); } catch (_) { data = { raw: dataLines.join('\n') }; }
  return { event, data };
}

function _opHandleEvent(evt) {
  switch (evt.event) {
    case 'start':
      operatorState.active = evt.data || {};
      if (OPERATOR.folder) OPERATOR.folder.textContent = evt.data.artifact_folder || '';
      _opSetStatusDot('running');
      break;
    case 'step':
      _opRenderStep(evt.data || {});
      break;
    case 'artifact': {
      const a = evt.data || {};
      operatorState.artifacts.push(a);
      _opRenderArtifact(a);
      break;
    }
    case 'memory_proposal': {
      const prop = evt.data || {};
      if (prop && prop.id) {
        operatorState.proposals.push(prop);
        _opRenderMemoryProposal(prop);
      }
      break;
    }
    case 'needs_input': {
      const need = evt.data || {};
      if (need && need.question) _opRenderNeedsInput(need);
      break;
    }
    case 'message': {
      const text = (evt.data && evt.data.text) || '';
      // "Planner → calling tool: X" markers are debug noise; the planner's
      // actual receipt (final message) is the thing worth showing + speaking.
      if (text && !text.startsWith('Planner →')) {
        operatorState.receipt = text;
        _opRenderReceipt(text);
      }
      break;
    }
    case 'error':
      _opRenderError((evt.data && evt.data.message) || 'Unknown error');
      break;
    case 'complete':
      operatorState.finalRecord = evt.data || null;
      _opSetStatusDot(((evt.data && evt.data.status) || 'completed'));
      // v1.7: speak the receipt aloud if voice replies are on.
      if (operatorState.receipt) _opSpeak(operatorState.receipt);
      break;
    case 'end':
      _opStopElapsed();
      _opSetRunning(false);
      // If we never received a 'complete' event, mark as failed for clarity.
      if (!operatorState.finalRecord) _opSetStatusDot('failed');
      break;
  }
  _opScrollToBottom();
}

async function _opSubmit(e) {
  if (e && e.preventDefault) e.preventDefault();
  // Ignore a submit while a run is in flight (e.g. Enter mid-run) so we don't
  // orphan the running turn. (Phase 2 will route answers to /continue.)
  if (operatorState.running) return;
  const command = (OPERATOR.command && OPERATOR.command.value || '').trim();
  if (command.length < 4) {
    _opSetStatus('Type a command first.', 'err');
    return;
  }
  // Answer mode (v2): if the current turn is paused awaiting an answer, resume
  // the SAME operation instead of starting a new run.
  if (operatorState.finalRecord && operatorState.finalRecord.awaiting_input
      && operatorState.active && operatorState.active.id) {
    return _opContinue(operatorState.active.id, command);
  }
  // Conversation flow: archive the finished turn into the thread instead of
  // wiping it, then start a fresh live turn. Clear the composer so it's always
  // empty and ready at the bottom.
  _opArchiveCurrentTurn();
  _opClearLive();
  if (OPERATOR.command) OPERATOR.command.value = '';
  _opSetRunning(true);
  if (OPERATOR.active) OPERATOR.active.classList.remove('hidden');
  _opRenderCommandEcho(command);   // the user's message, at the top of this turn
  _opSetStatusDot('running');
  _opStartElapsed();
  _opScrollToBottom();

  operatorState.abortController = new AbortController();

  try {
    const res = await fetch(`${BACKEND}/operations/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify({ command }),
      signal: operatorState.abortController.signal,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    await _opParseSSE(res);
  } catch (err) {
    if (err.name === 'AbortError') {
      _opSetStatus('Operation cancelled.', 'err');
    } else {
      const msg = err && err.message ? err.message : String(err);
      _opRenderError(/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
        ? 'Backend is not reachable.' : msg);
    }
    _opSetStatusDot('failed');
  } finally {
    _opStopElapsed();
    _opSetRunning(false);
    operatorState.abortController = null;
  }
}

// Append the operator's answer inline in the SAME live turn (not a new turn).
function _opAppendUserMessage(text) {
  const host = OPERATOR.live || OPERATOR.active;
  if (!host) return;
  const echo = document.createElement('div');
  echo.className = 'operator-command-echo';
  const label = document.createElement('span');
  label.className = 'operator-command-echo-label';
  label.textContent = 'You';
  const body = document.createElement('span');
  body.className = 'operator-command-echo-body';
  body.textContent = text;
  echo.appendChild(label);
  echo.appendChild(body);
  host.appendChild(echo);
}

// v2: resume a paused operation. Streams the continued run into the SAME turn
// via POST /operations/{id}/continue — the operation keeps its context/folder.
async function _opContinue(opId, answer) {
  if (operatorState.running) return;
  if (OPERATOR.command) OPERATOR.command.value = '';
  _opAppendUserMessage(answer);
  // The question has been answered — hide its card.
  const needsEl = document.getElementById('operator-needs-input');
  if (needsEl) needsEl.classList.add('hidden');
  const needsList = document.getElementById('operator-needs-input-list');
  if (needsList) needsList.innerHTML = '';
  operatorState.finalRecord = null;   // no longer awaiting; reset on next complete
  _opSetRunning(true);
  _opSetStatusDot('running');
  _opStartElapsed();
  _opScrollToBottom();

  operatorState.abortController = new AbortController();
  try {
    const res = await fetch(`${BACKEND}/operations/${encodeURIComponent(opId)}/continue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
      body: JSON.stringify({ answer }),
      signal: operatorState.abortController.signal,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    await _opParseSSE(res);
  } catch (err) {
    if (err.name === 'AbortError') {
      _opSetStatus('Operation cancelled.', 'err');
    } else {
      const msg = err && err.message ? err.message : String(err);
      _opRenderError(/Failed to fetch|NetworkError|ECONNREFUSED/i.test(msg)
        ? 'Backend is not reachable.' : msg);
    }
    _opSetStatusDot('failed');
  } finally {
    _opStopElapsed();
    _opSetRunning(false);
    operatorState.abortController = null;
  }
}

/* ----- v2.3: PDF / text as a grounding source ----- */

function _opAttachPdfClick() {
  const input = document.getElementById('operator-pdf-input');
  if (input) input.click();
}

async function _opHandlePdfFile(file) {
  if (!file) return;
  // If a run is paused awaiting a grounding answer, the PDF ANSWERS it and
  // resumes that run. Otherwise it's staged as the source for the next command.
  const awaiting = operatorState.finalRecord && operatorState.finalRecord.awaiting_input
    && operatorState.active && operatorState.active.id;
  if (awaiting) {
    await _opUploadPdfToOperation(operatorState.active.id, file);
  } else {
    await _opStageSourcePdf(file);
  }
}

async function _opStageSourcePdf(file) {
  _opSetStatus(`Reading ${file.name}…`);
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const res = await fetch(`${BACKEND}/sources/stage-pdf`, { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
    _opShowSourceChip(`📎 ${file.name} — ${data.chars} chars${data.truncated ? ' (truncated)' : ''} · Ridian will build only from this`);
    _opSetStatus('Source attached. Type your command and Ridian will build only from it.', 'ok');
  } catch (err) {
    _opSetStatus(`Couldn't attach PDF: ${err && err.message ? err.message : err}`, 'err');
  }
}

async function _opUploadPdfToOperation(opId, file) {
  if (operatorState.running) return;
  _opAppendUserMessage(`📎 Uploaded ${file.name}`);
  const needsEl = document.getElementById('operator-needs-input');
  if (needsEl) needsEl.classList.add('hidden');
  const needsList = document.getElementById('operator-needs-input-list');
  if (needsList) needsList.innerHTML = '';
  operatorState.finalRecord = null;
  _opSetRunning(true);
  _opSetStatusDot('running');
  _opStartElapsed();
  _opScrollToBottom();
  operatorState.abortController = new AbortController();
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const res = await fetch(`${BACKEND}/operations/${encodeURIComponent(opId)}/upload-source`, {
      method: 'POST', body: fd, signal: operatorState.abortController.signal,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    await _opParseSSE(res);
  } catch (err) {
    if (err.name === 'AbortError') _opSetStatus('Cancelled.', 'err');
    else _opRenderError(err && err.message ? err.message : String(err));
    _opSetStatusDot('failed');
  } finally {
    _opStopElapsed();
    _opSetRunning(false);
    operatorState.abortController = null;
  }
}

// Stage the text currently in the composer as the grounding source. If a run
// is paused awaiting a grounding answer, the text ANSWERS it (resumes); else
// it's staged for the next command (build strictly from it).
async function _opPasteAsSource() {
  const text = (OPERATOR.command && OPERATOR.command.value || '').trim();
  if (text.length < 40) {
    _opSetStatus('Paste the source text into the box first (a few sentences), then choose “+ → Paste text as source”.', 'err');
    if (OPERATOR.command) OPERATOR.command.focus();
    return;
  }
  const awaiting = operatorState.finalRecord && operatorState.finalRecord.awaiting_input
    && operatorState.active && operatorState.active.id;
  if (awaiting) {
    return _opContinue(operatorState.active.id, text);   // answer the paused question
  }
  _opSetStatus('Attaching pasted text as source…');
  try {
    const res = await fetch(`${BACKEND}/sources/stage-text`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error((data && data.detail) || `HTTP ${res.status}`);
    if (OPERATOR.command) OPERATOR.command.value = '';
    _opUpdateSendEnabled();
    _opShowSourceChip(`📋 Pasted text — ${data.chars} chars · Ridian will build only from this`);
    _opSetStatus('Source attached. Type your command and Ridian will build only from it.', 'ok');
  } catch (err) {
    _opSetStatus(`Couldn't attach text: ${err && err.message ? err.message : err}`, 'err');
  }
}

function _opShowSourceChip(text) {
  const chip = document.getElementById('operator-source-chip');
  const label = document.getElementById('operator-source-chip-text');
  if (label) label.textContent = text;
  if (chip) chip.classList.remove('hidden');
}

async function _opClearSource() {
  const chip = document.getElementById('operator-source-chip');
  if (chip) chip.classList.add('hidden');
  try { await fetch(`${BACKEND}/sources/clear`, { method: 'POST' }); } catch (_) { /* ignore */ }
}

async function _opOpenArtifactFile(filename) {
  if (!operatorState.active || !operatorState.active.artifact_folder) return;
  if (filename.toLowerCase().endsWith('.json')) {
    // No JSON allowlisting in the open-file endpoint; just open the folder.
    return _opOpenArtifactFolder();
  }
  try {
    const res = await fetch(`${BACKEND}/artifacts/open-file`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        artifact_folder: operatorState.active.artifact_folder,
        filename,
      }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    _opSetStatus(`Opened ${filename}.`, 'ok');
  } catch (err) {
    _opSetStatus(`Could not open ${filename}: ${err.message || err}`, 'err');
  }
}

async function _opOpenArtifactFolder() {
  if (!operatorState.active || !operatorState.active.artifact_folder) {
    _opSetStatus('No active operation.', 'err');
    return;
  }
  try {
    const res = await fetch(`${BACKEND}/artifacts/open-folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_folder: operatorState.active.artifact_folder }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    _opSetStatus('Output folder opened.', 'ok');
  } catch (err) {
    _opSetStatus(`Could not open folder: ${err.message || err}`, 'err');
  }
}

async function _opUploadDrive() {
  if (!operatorState.active || !operatorState.active.artifact_folder) {
    _opSetStatus('No active operation to upload.', 'err');
    return;
  }
  _opSetStatus('Uploading to Google Drive…');
  try {
    const res = await fetch(`${BACKEND}/google/upload-artifacts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artifact_folder: operatorState.active.artifact_folder }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    const path = (data && data.drive_path) || (data && data.drive_folder_name) || 'Drive';
    const files = (data && data.uploaded_files) || [];
    operatorState.drive = {
      drive_path: data.drive_path || '',
      drive_folder_name: data.drive_folder_name || '',
      drive_folder_url: data.drive_folder_url || '',
      uploaded_files: files,
    };
    _opSetStatus(`Uploaded to ${path} (${files.length} files).`, 'ok');
  } catch (err) {
    _opSetStatus(`Upload failed: ${err && err.message ? err.message : err}`, 'err');
  }
}

// Kind -> human label for the email's artifact list. Unknown kinds fall back
// to the raw kind so we never silently mislabel a new artifact type.
const _ARTIFACT_KIND_LABEL = {
  markdown: 'document',
  text: 'document',
  json: 'data file',
  spreadsheet: 'Google Sheet',
  slides: 'Google Slides deck',
  gmail_draft: 'Gmail draft',
  audio: 'audiobook (MP3)',
  drive_folder: 'Drive folder',
  browser: 'opened in browser',
};

// Build the package-summary email from THIS run's real record — NEVER a fixed
// template. Subject + body describe the actual artifacts (names + links), the
// command, and the operation log for this run. Returns { subject, body }.
function _opBuildEmailSummary() {
  const rec = operatorState.finalRecord || {};
  const active = operatorState.active || {};
  const drive = operatorState.drive || null;

  const command = (active.command || '').trim() || '(no command captured)';
  const folder = active.artifact_folder || '(unknown local folder)';
  const status = rec.status || 'unknown';
  const sourcesCount = rec.sources_count || 0;
  const artifacts = Array.isArray(rec.artifacts) ? rec.artifacts : [];
  const tools = Array.isArray(rec.tools_used) ? rec.tools_used : [];
  const errors = Array.isArray(rec.errors) ? rec.errors : [];

  const labelFor = (a) => _ARTIFACT_KIND_LABEL[a.kind] || a.kind || 'artifact';
  const isLink = (p) => /^https?:\/\//i.test(p || '');

  // Deliverables = what the run actually produced. The Drive folder and any
  // opened browser tab are locations/actions, not deliverables, so they're
  // excluded from the headline summary (still listed under "Artifacts").
  const deliverables = artifacts.filter((a) => a.kind !== 'drive_folder' && a.kind !== 'browser');

  // Count by label for the one-line summary + subject, e.g.
  // "1 document, 1 Google Slides deck, 1 Google Sheet, 3 Gmail drafts".
  const counts = {};
  for (const a of deliverables) {
    const label = labelFor(a);
    counts[label] = (counts[label] || 0) + 1;
  }
  const summaryPhrase = Object.entries(counts)
    .map(([label, n]) => (n > 1 ? `${n} ${label}s` : `1 ${label}`))
    .join(', ');

  // Subject: accurate to this run — never the old audiobook string.
  const shortCmd = command.length > 60 ? command.slice(0, 57).trim() + '…' : command;
  const subject = deliverables.length
    ? `Ridian Operator — ${shortCmd} (${summaryPhrase})`
    : `Ridian Operator — ${shortCmd} (no file artifacts produced)`;

  // Drive line — only present after a successful upload happened this session.
  let driveLine;
  if (drive && drive.drive_path) {
    driveLine = `Drive:  ${drive.drive_path}` +
      (drive.drive_folder_url ? `\n          ${drive.drive_folder_url}` : '');
  } else if (drive && drive.drive_folder_name) {
    driveLine = `Drive:  ${drive.drive_folder_name}` +
      (drive.drive_folder_url ? `\n          ${drive.drive_folder_url}` : '');
  } else {
    driveLine = 'Drive:  (not uploaded in this session — use "Upload to Google Drive")';
  }

  // Artifact list with names + links (external artifacts carry an http URL).
  const artifactLines = artifacts.length
    ? artifacts.map((a) => {
        const head = `  - ${a.name || '(unnamed)'} [${labelFor(a)}]`;
        return isLink(a.path) ? `${head}\n        ${a.path}` : head;
      }).join('\n')
    : '  (no artifacts were recorded for this run)';

  const lines = [
    'Here is the package from your latest Ridian Operator run.',
    '',
    'What I asked Ridian to do',
    `  "${command}"`,
    '',
    `What Ridian produced${summaryPhrase ? ` — ${summaryPhrase}` : ''}`,
    artifactLines,
    '',
    'Where the files live',
    `  Local:  ${folder}`,
    `  ${driveLine}`,
    '',
    'Run details',
    `  Status:     ${status}`,
    sourcesCount > 0 ? `  Sources gathered: ${sourcesCount}` : null,
    tools.length ? `  Tools used: ${tools.join(', ')}` : null,
    rec.started_at ? `  Started:    ${rec.started_at}` : null,
    rec.completed_at ? `  Completed:  ${rec.completed_at}` : null,
  ];
  if (errors.length) {
    lines.push('', 'Issues this run', ...errors.map((e) => `  - ${e}`));
  }
  lines.push('', '— Sent from Ridian Command Center.', '');

  const body = lines.filter((line) => line !== null).join('\n');
  return { subject, body };
}

async function _opEmailMe() {
  if (!operatorState.finalRecord) {
    _opSetStatus('Wait for the operation to finish first.', 'err');
    return;
  }
  const { subject, body } = _opBuildEmailSummary();

  // Name the real recipient in the confirm (not "your configured recipient").
  // Prefer the cached settings value; fall back to a fresh /settings fetch so
  // we never show a stale or blank address.
  let recipient = ((cachedSettings && cachedSettings.default_to_email) || '').trim();
  if (!recipient) {
    try {
      const s = await fetch(`${BACKEND}/settings`).then((r) => (r.ok ? r.json() : null));
      if (s) { cachedSettings = s; recipient = String(s.default_to_email || '').trim(); }
    } catch (_) { /* fall through to generic wording */ }
  }
  const who = recipient || 'your configured recipient (set a default in Settings)';

  const ok = window.confirm(`Send this run's package summary email to ${who}?`);
  if (!ok) return;
  _opSetStatus('Sending email…');
  try {
    const res = await fetch(`${BACKEND}/email/send-approved`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subject, body }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error((data && data.detail) || `HTTP ${res.status}`);
    }
    const to = data && data.to_email ? ` to ${data.to_email}` : '';
    _opSetStatus(`Email sent${to}.`, 'ok');
  } catch (err) {
    _opSetStatus(`Email failed: ${err && err.message ? err.message : err}`, 'err');
  }
}

/**
 * Rehydrate a completed Operator run from disk into the Operator panel.
 *
 * Called when the user clicks a recent run with workflow === 'operator' in
 * the sidebar. Reads /operations/load on the backend, restores
 * operatorState (active / finalRecord / drive), and re-renders the timeline
 * + artifacts + audio player using the same helpers the live SSE path uses.
 * Missing artifacts surface as warnings instead of silent no-ops.
 */
async function loadOperatorRun(run) {
  if (!run || !run.artifact_folder) return;

  // Always land on the Operator surface (welcome view).
  setWorkspaceView('welcome');
  _opResetUI();

  // Reveal panels even before the fetch resolves so the user has feedback.
  if (OPERATOR.active) OPERATOR.active.classList.remove('hidden');
  if (OPERATOR.artifactsCard) OPERATOR.artifactsCard.classList.remove('hidden');
  if (OPERATOR.folder) OPERATOR.folder.textContent = run.artifact_folder;
  _opSetStatusDot('running');
  if (OPERATOR.statusLabel) OPERATOR.statusLabel.textContent = 'Loading saved run…';

  let data = null;
  try {
    const url = `${BACKEND}/operations/load?artifact_folder=${encodeURIComponent(run.artifact_folder)}`;
    const res = await fetch(url);
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error((errBody && errBody.detail) || `HTTP ${res.status}`);
    }
    data = await res.json();
  } catch (err) {
    _opRenderRehydrateError(run.artifact_folder, err && err.message ? err.message : String(err));
    return;
  }

  const log = data.operation_log;
  const folder = data.artifact_folder || run.artifact_folder;

  // If the log itself is missing or unparseable, show a real error state with
  // the folder path + an Open folder button. Not silent.
  if (!log) {
    _opRenderRehydrateError(
      folder,
      "This operator run has no readable operation_log.json. The folder may have been partially written or hand-edited.",
    );
    return;
  }

  // Restore state so the Operator action buttons (Open folder, Upload Drive,
  // Email package) work against the reloaded run, not the live session.
  operatorState.active = {
    id: log.id || '',
    artifact_folder: folder,
    command: log.command || '',
    intent: log.intent || '',
  };
  operatorState.finalRecord = log;
  operatorState.drive = null; // unknown — user can re-upload if desired

  _opSetStatusDot(log.status || 'completed');

  // Echo the command above the timeline so the operator knows what's loaded.
  _opRenderCommandEcho(log.command || run.name || '(no command recorded)');

  // Replay the timeline using the same helper the live SSE path uses.
  (log.steps || []).forEach((step) => _opRenderStep(step));

  // Replay artifacts. For audio, check the presence flag from the backend so
  // a stale log path that no longer exists doesn't show a broken player.
  const artifacts = Array.isArray(log.artifacts) ? log.artifacts : [];
  artifacts.forEach((a) => {
    if (a.kind === 'audio' && a.name === 'audiobook.mp3' && !data.has_audio) {
      // Don't render an audio artifact if the file is no longer on disk;
      // _opRenderRehydrateWarnings will list it below.
      return;
    }
    _opRenderArtifact(a);
  });

  // Show warnings for anything expected but missing on disk.
  if (Array.isArray(data.missing) && data.missing.length) {
    _opRenderRehydrateWarnings(data.missing);
  }

  // Replay any errors logged at run time.
  (log.errors || []).forEach((m) => _opRenderError(m));

  // v1.2: replay memory proposals. Render ALL of them (proposed + committed
  // + dismissed) so the user can see what happened on prior reviews; only
  // "proposed" rows are still actionable (Confirm/Dismiss buttons stay live).
  const proposals = Array.isArray(log.proposed_memory_updates) ? log.proposed_memory_updates : [];
  proposals.forEach((p) => {
    operatorState.proposals.push(p);
    _opRenderMemoryProposal(p);
  });

  // v1.7: replay the receipt + any open questions (no speaking on reload —
  // voice is for live completions only).
  if (log.receipt) {
    operatorState.receipt = log.receipt;
    _opRenderReceipt(log.receipt);
  }
  (Array.isArray(log.needs_input) ? log.needs_input : []).forEach((n) => _opRenderNeedsInput(n, false));

  debugLog('operator.rehydrated', {
    id: log.id, status: log.status, sources: log.sources_count,
    has_audio: data.has_audio, missing: data.missing,
  });
}

function _opRenderCommandEcho(text) {
  if (!OPERATOR.active) return;
  // Reuse a single command-echo node so re-renders don't stack.
  let echo = OPERATOR.active.querySelector('.operator-command-echo');
  if (!echo) {
    echo = document.createElement('div');
    echo.className = 'operator-command-echo';
    // Insert after the active-head, before the timeline.
    const head = OPERATOR.active.querySelector('.operator-active-head');
    if (head && head.nextSibling) {
      OPERATOR.active.insertBefore(echo, head.nextSibling);
    } else {
      OPERATOR.active.appendChild(echo);
    }
  }
  echo.innerHTML = '';
  const label = document.createElement('span');
  label.className = 'operator-command-echo-label';
  label.textContent = 'You';
  const body = document.createElement('span');
  body.className = 'operator-command-echo-body';
  body.textContent = text;
  echo.appendChild(label);
  echo.appendChild(body);
}

function _opRenderRehydrateWarnings(missing) {
  if (!OPERATOR.errors || !missing || !missing.length) return;
  OPERATOR.errors.classList.remove('hidden');
  if (!OPERATOR.errors.querySelector('.operator-errors-title')) {
    OPERATOR.errors.innerHTML = `
      <div class="operator-errors-title">Missing artifacts on disk</div>
      <ul class="operator-errors-list"></ul>
    `;
  } else {
    // If the title is for live errors, change it; otherwise keep it.
    const title = OPERATOR.errors.querySelector('.operator-errors-title');
    if (title.textContent.indexOf('Missing') === -1) {
      title.textContent = 'Missing artifacts on disk';
    }
  }
  const ul = OPERATOR.errors.querySelector('.operator-errors-list');
  missing.forEach((name) => {
    const li = document.createElement('li');
    li.textContent = `${name} — expected but not found in the run folder`;
    ul.appendChild(li);
  });
}

function _opRenderRehydrateError(folder, message) {
  if (OPERATOR.statusLabel) OPERATOR.statusLabel.textContent = 'Could not load run';
  _opSetStatusDot('failed');
  if (OPERATOR.folder) OPERATOR.folder.textContent = folder || '';
  if (OPERATOR.errors) {
    OPERATOR.errors.classList.remove('hidden');
    OPERATOR.errors.innerHTML = `
      <div class="operator-errors-title">Could not rehydrate this operator run</div>
      <ul class="operator-errors-list">
        <li>${escapeHtml(message || 'Unknown error')}</li>
        <li>Folder: <code>${escapeHtml(folder || '(unknown)')}</code></li>
      </ul>
    `;
  }
  // Show the artifacts card so the "Open output folder" button is reachable.
  if (OPERATOR.artifactsCard) OPERATOR.artifactsCard.classList.remove('hidden');
  if (OPERATOR.artifactsList) {
    OPERATOR.artifactsList.innerHTML =
      '<li class="dashboard-empty">No artifacts could be loaded for this run.</li>';
  }
  // Wire activeRunFolder so Open output folder still works even without a log.
  operatorState.active = {
    id: '', artifact_folder: folder || '', command: '', intent: '',
  };
  operatorState.finalRecord = null;
  operatorState.drive = null;
}

// Wire up the operator surface (only fires if elements exist).
if (OPERATOR.form) {
  OPERATOR.form.addEventListener('submit', _opSubmit);
}
if (OPERATOR.command) {
  OPERATOR.command.addEventListener('keydown', (e) => {
    // Enter submits (same as clicking Run); Shift+Enter inserts a newline.
    // Ctrl/Cmd+Enter still submits too — plain Enter without Shift covers it.
    // !e.isComposing avoids submitting mid-IME-composition.
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); _opSubmit(); }
  });
}
if (OPERATOR.cancelBtn) {
  OPERATOR.cancelBtn.addEventListener('click', () => {
    if (operatorState.abortController) operatorState.abortController.abort();
  });
}
if (OPERATOR.openFolderBtn) OPERATOR.openFolderBtn.addEventListener('click', _opOpenArtifactFolder);
if (OPERATOR.uploadDriveBtn) OPERATOR.uploadDriveBtn.addEventListener('click', _opUploadDrive);
if (OPERATOR.emailMeBtn) OPERATOR.emailMeBtn.addEventListener('click', _opEmailMe);
if (OPERATOR.proposalsConfirmAll) {
  OPERATOR.proposalsConfirmAll.addEventListener('click', () => {
    const ids = _opPendingProposalIds();
    if (ids.length) _opCommitProposals({ confirmed: ids });
  });
}
if (OPERATOR.proposalsDismissAll) {
  OPERATOR.proposalsDismissAll.addEventListener('click', () => {
    const ids = _opPendingProposalIds();
    if (ids.length) _opCommitProposals({ dismissed: ids });
  });
}

// v1.7.1: mic (inset in composer), per-reply Read-aloud, settings auto-read
const _micBtn = document.getElementById('operator-mic-btn');
if (_micBtn) _micBtn.addEventListener('click', _opMicToggle);

// Receipt speaker: play/stop THIS reply on demand (works even when the
// auto-read preference is off — the click is the intent).
const _receiptSpeakBtn = document.getElementById('operator-receipt-speak');
if (_receiptSpeakBtn) {
  _receiptSpeakBtn.addEventListener('click', () => {
    let speaking = false;
    try { speaking = window.speechSynthesis.speaking; } catch (_) {}
    if (speaking) _opStopSpeaking();
    else if (operatorState.receipt) _opSpeak(operatorState.receipt, true);
  });
}

// Settings → "Read replies aloud automatically" (renderer-local preference;
// not part of the backend settings payload).
const _voiceChk = document.getElementById('settings-voice-replies');
if (_voiceChk) {
  _voiceChk.checked = _opVoiceEnabled();
  _voiceChk.addEventListener('change', () => _opSetVoiceEnabled(_voiceChk.checked));
}
const _needsAnswerBtn = document.getElementById('operator-needs-input-answer');
if (_needsAnswerBtn) {
  _needsAnswerBtn.addEventListener('click', () => {
    // The answer goes in the always-present composer at the bottom of the thread.
    _opScrollToBottom();
    if (OPERATOR.command) OPERATOR.command.focus();
  });
}

// v2.3: attach a PDF as the grounding source (composer button + hidden picker).
// v2.4: single "+" add-source menu. Extensible surface — to add a future input
// type (image, other file), add a menu item with a new data-add-action in the
// HTML and a matching handler here; no toolbar redesign needed.
const _OP_ADD_ACTIONS = {
  pdf: _opAttachPdfClick,
  text: _opPasteAsSource,
};

function _opCloseAddMenu() {
  const btn = document.getElementById('operator-add-btn');
  const menu = document.getElementById('operator-add-menu');
  if (menu) menu.classList.add('hidden');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

function _opOpenAddMenu() {
  const btn = document.getElementById('operator-add-btn');
  const menu = document.getElementById('operator-add-menu');
  if (!btn || !menu) return;
  menu.classList.remove('hidden');
  btn.setAttribute('aria-expanded', 'true');
  const first = menu.querySelector('.operator-add-item:not([disabled])');
  if (first) first.focus();
}

(function _opWireAddMenu() {
  const btn = document.getElementById('operator-add-btn');
  const menu = document.getElementById('operator-add-menu');
  const wrap = document.getElementById('operator-add-wrap');
  if (!btn || !menu || !wrap) return;

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menu.classList.contains('hidden')) _opOpenAddMenu(); else _opCloseAddMenu();
  });
  btn.addEventListener('keydown', (e) => {
    if ((e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') && menu.classList.contains('hidden')) {
      e.preventDefault();
      _opOpenAddMenu();
    } else if (e.key === 'Escape') {
      _opCloseAddMenu();
    }
  });

  // Selection → run the action + close.
  menu.querySelectorAll('.operator-add-item').forEach((item) => {
    item.addEventListener('click', () => {
      const fn = _OP_ADD_ACTIONS[item.getAttribute('data-add-action')];
      _opCloseAddMenu();
      btn.focus();
      if (typeof fn === 'function') fn();
    });
  });

  // Keyboard navigation within the menu.
  menu.addEventListener('keydown', (e) => {
    const items = Array.from(menu.querySelectorAll('.operator-add-item:not([disabled])'));
    if (!items.length) return;
    const idx = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); (items[idx + 1] || items[0]).focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); (items[idx - 1] || items[items.length - 1]).focus(); }
    else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
    else if (e.key === 'Escape') { e.preventDefault(); _opCloseAddMenu(); btn.focus(); }
  });

  // Close on outside-click.
  document.addEventListener('click', (e) => {
    if (!menu.classList.contains('hidden') && !wrap.contains(e.target)) _opCloseAddMenu();
  });
})();
const _pdfInput = document.getElementById('operator-pdf-input');
if (_pdfInput) {
  _pdfInput.addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = '';   // allow re-selecting the same file
    if (f) _opHandlePdfFile(f);
  });
}
const _sourceChipClear = document.getElementById('operator-source-chip-clear');
if (_sourceChipClear) _sourceChipClear.addEventListener('click', _opClearSource);

// Send arrow: greyed until the field has text (and no run in flight).
if (OPERATOR.command) OPERATOR.command.addEventListener('input', _opUpdateSendEnabled);
_opUpdateSendEnabled();

// Example chips populate the textarea.
document.querySelectorAll('[data-operator-example]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const text = btn.getAttribute('data-operator-example') || '';
    if (OPERATOR.command) {
      OPERATOR.command.value = text;
      OPERATOR.command.focus();
      _opUpdateSendEnabled();
    }
  });
});


/* ============================================================ */
/*                   v1.5 — single-pane extras                  */
/* ============================================================ */
/* Three pieces of glue, all surgical:
 *   1. Operator context strip below the command box (memory + last run)
 *   2. History slide-in panel triggered by the top-bar icon
 *   3. Command-history shell behavior (↑/↓) in the command box
 *   plus: top-bar Settings + History buttons wiring, status-pill
 *   auto-hide is purely CSS (.is-up). Backend status changes drop the
 *   .is-up class when degraded so the pill reappears.
 */

/* ----- 1. Operator context strip ----- */

// v1.7 fix: look elements up at call time instead of at module-eval time.
// setWorkspaceView('welcome') runs during INITIAL LOAD — *before* this part
// of the file has evaluated — so a module-level const here was in its
// temporal dead zone on the first call, threw, got swallowed by the
// try/catch, and the strip never appeared until after the first run.
function _ctxGetEls() {
  return {
    strip:        document.getElementById('operator-context-strip'),
    memoryBtn:    document.getElementById('operator-context-memory'),
    memoryValue:  document.getElementById('operator-context-memory-value'),
    lastBtn:      document.getElementById('operator-context-last'),
    lastValue:    document.getElementById('operator-context-last-value'),
  };
}
let _ctxLastOp = null;  // {artifact_folder, command, completed_at, ...}

function _fmtRelativeShort(iso) {
  if (!iso) return '';
  let when;
  try { when = new Date(iso); } catch (_) { return ''; }
  const diffSec = (Date.now() - when.getTime()) / 1000;
  if (!isFinite(diffSec) || diffSec < 0) return '';
  if (diffSec < 60)     return `${Math.floor(diffSec)}s ago`;
  if (diffSec < 3600)   return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400)  return `${Math.floor(diffSec / 3600)}h ago`;
  return `${Math.floor(diffSec / 86400)}d ago`;
}

async function loadOperatorContextStrip() {
  const els2 = _ctxGetEls();
  if (!els2.strip) return;
  let anyContent = false;

  // Memory chip: one fetch hits the existing /memory/summary endpoint.
  try {
    const res = await fetch(`${BACKEND}/memory/summary`);
    if (res.ok) {
      const m = await res.json();
      const parts = [];
      if (m.contacts) parts.push(`${m.contacts} contacts`);
      if (m.facts) parts.push(`${m.facts} facts`);
      if (m.open_follow_ups) parts.push(`${m.open_follow_ups} follow-ups`);
      const text = parts.length ? parts.join(' · ') : 'empty — click to add';
      if (els2.memoryValue) els2.memoryValue.textContent = text;
      anyContent = true;
    }
  } catch (_) { /* offline — leave chip blank */ }

  // Last-run chip: read the operations log, show the most recent operator run.
  try {
    const res = await fetch(`${BACKEND}/operations/recent?limit=1`);
    if (res.ok) {
      const data = await res.json();
      const ops = (data && data.operations) || [];
      if (ops.length) {
        const op = ops[0];
        _ctxLastOp = op;
        const cmd = (op.command || '').split(/\r?\n/)[0].slice(0, 60);
        const rel = _fmtRelativeShort(op.completed_at);
        if (els2.lastValue) els2.lastValue.textContent = `${cmd} · ${rel}`;
        if (els2.lastBtn) els2.lastBtn.classList.remove('hidden');
        anyContent = true;
      } else {
        if (els2.lastBtn) els2.lastBtn.classList.add('hidden');
      }
    }
  } catch (_) { /* offline */ }

  if (anyContent) els2.strip.classList.remove('hidden');
  else els2.strip.classList.add('hidden');
}

{
  const els2 = _ctxGetEls();
  if (els2.memoryBtn) {
    els2.memoryBtn.addEventListener('click', () => openMemoryModal('contacts'));
  }
  if (els2.lastBtn) {
    els2.lastBtn.addEventListener('click', () => {
      if (_ctxLastOp && _ctxLastOp.artifact_folder) {
        loadOperatorRun({ artifact_folder: _ctxLastOp.artifact_folder, name: _ctxLastOp.command || '' });
      }
    });
  }
}

// v1.7: the strip is the only doorway into Memory in single-pane mode, so it
// must be present from the first paint — not only after the first run. The
// initial setWorkspaceView('welcome') call ran before this section evaluated
// (and was no-op'd by its try/catch), so kick it once now that everything is
// defined.
loadOperatorContextStrip();

/* ----- 2. History slide-in panel ----- */

const _historyEls = {
  panel:      document.getElementById('history-panel'),
  list:       document.getElementById('history-panel-list'),
  closeBtn:   document.getElementById('history-close-btn'),
  topbarBtn:  document.getElementById('topbar-history-btn'),
};

function _historyOpen() {
  if (!_historyEls.panel) return;
  _historyEls.panel.classList.remove('hidden');
  _historyEls.panel.setAttribute('aria-hidden', 'false');
  _historyFill();
  document.addEventListener('keydown', _historyKeydown);
}
function _historyClose() {
  if (!_historyEls.panel) return;
  _historyEls.panel.classList.add('hidden');
  _historyEls.panel.setAttribute('aria-hidden', 'true');
  document.removeEventListener('keydown', _historyKeydown);
}
function _historyKeydown(e) {
  if (e.key === 'Escape') { e.preventDefault(); _historyClose(); }
}
async function _historyFill() {
  if (!_historyEls.list) return;
  _historyEls.list.innerHTML = '<li class="history-panel-empty">Loading…</li>';
  try {
    const res = await fetch(`${BACKEND}/operations/recent?limit=30`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const ops = (data && data.operations) || [];
    if (!ops.length) {
      _historyEls.list.innerHTML =
        '<li class="history-panel-empty">No operations yet. Run one from the command box.</li>';
      return;
    }
    _historyEls.list.innerHTML = '';
    ops.forEach((op) => {
      const li = document.createElement('li');
      li.className = 'history-panel-item';
      const cmd = (op.command || '(no command)').split(/\r?\n/)[0];
      const rel = _fmtRelativeShort(op.completed_at);
      const status = (op.status || 'unknown').toLowerCase();
      const sCls = status === 'completed' ? 'is-completed'
                 : status === 'partial'   ? 'is-partial'
                 : status === 'failed'    ? 'is-failed'
                 : '';
      li.innerHTML = `
        <span class="history-panel-item-cmd"></span>
        <span class="history-panel-item-meta">
          <span class="history-panel-item-status ${sCls}"></span>
          <span class="history-panel-item-when"></span>
        </span>
      `;
      li.querySelector('.history-panel-item-cmd').textContent = cmd;
      li.querySelector('.history-panel-item-status').textContent = status;
      li.querySelector('.history-panel-item-when').textContent = rel;
      li.addEventListener('click', () => {
        _historyClose();
        if (op.artifact_folder) {
          loadOperatorRun({ artifact_folder: op.artifact_folder, name: cmd });
        }
      });
      _historyEls.list.appendChild(li);
    });
  } catch (err) {
    _historyEls.list.innerHTML = `<li class="history-panel-empty">Could not load history: ${escapeHtml(err.message || String(err))}</li>`;
  }
}
if (_historyEls.topbarBtn) _historyEls.topbarBtn.addEventListener('click', _historyOpen);
if (_historyEls.closeBtn) _historyEls.closeBtn.addEventListener('click', _historyClose);

/* ----- 2b. v2.7: sidebar chat list + search + new chat + settings ----- */

let _railOps = [];   // last-fetched operations, filtered client-side by search

function _railActiveFolder() {
  return (operatorState.active && operatorState.active.artifact_folder) || '';
}

function _railRenderThreads() {
  const list = document.getElementById('rail-threads');
  if (!list) return;
  const q = ((document.getElementById('rail-search') || {}).value || '').trim().toLowerCase();
  const ops = q
    ? _railOps.filter((op) => (op.command || '').toLowerCase().includes(q))
    : _railOps;
  list.innerHTML = '';
  if (!ops.length) {
    const li = document.createElement('li');
    li.className = 'rail-threads-empty';
    li.textContent = q ? 'No chats match.' : 'No chats yet.';
    list.appendChild(li);
    return;
  }
  const activeFolder = _railActiveFolder();
  ops.forEach((op) => {
    const li = document.createElement('li');
    li.className = 'rail-thread';
    if (op.artifact_folder && op.artifact_folder === activeFolder) li.classList.add('is-active');
    const cmd = (op.command || '(no command)').split(/\r?\n/)[0];
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'rail-thread-btn';
    btn.title = cmd;
    const label = document.createElement('span');
    label.className = 'rail-thread-cmd';
    label.textContent = cmd;
    const when = document.createElement('span');
    when.className = 'rail-thread-when';
    when.textContent = _fmtRelativeShort(op.completed_at);
    btn.appendChild(label);
    btn.appendChild(when);
    btn.addEventListener('click', () => {
      if (op.artifact_folder) {
        loadOperatorRun({ artifact_folder: op.artifact_folder, name: cmd });
        list.querySelectorAll('.rail-thread.is-active').forEach((n) => n.classList.remove('is-active'));
        li.classList.add('is-active');
      }
    });
    li.appendChild(btn);
    list.appendChild(li);
  });
}

async function _railThreadsFill() {
  try {
    const res = await fetch(`${BACKEND}/operations/recent?limit=30`);
    if (!res.ok) return;
    const data = await res.json();
    _railOps = (data && data.operations) || [];
    _railRenderThreads();
  } catch (_) { /* backend not up yet — the poll below retries */ }
}

// New chat: abort any in-flight run, clear the thread, fresh composer.
function _opNewChat() {
  if (operatorState.abortController) {
    try { operatorState.abortController.abort(); } catch (_) {}
  }
  _opResetUI();
  operatorState.active = null;
  operatorState.finalRecord = null;
  if (OPERATOR.active) OPERATOR.active.classList.add('hidden');
  if (OPERATOR.command) { OPERATOR.command.value = ''; OPERATOR.command.focus(); }
  const list = document.getElementById('rail-threads');
  if (list) list.querySelectorAll('.rail-thread.is-active').forEach((n) => n.classList.remove('is-active'));
}

const _railNewChatBtn = document.getElementById('rail-new-chat');
if (_railNewChatBtn) _railNewChatBtn.addEventListener('click', _opNewChat);
const _railSearch = document.getElementById('rail-search');
if (_railSearch) _railSearch.addEventListener('input', _railRenderThreads);
const _railSettingsBtn = document.getElementById('rail-settings-btn');
if (_railSettingsBtn) _railSettingsBtn.addEventListener('click', openSettings);

_railThreadsFill();

/* ----- 3. Command-history (↑/↓) in the command box ----- */

const _CMD_HISTORY_KEY = 'ridian.cmdHistory';
const _CMD_HISTORY_MAX = 30;
let _cmdHistoryIdx = -1;  // -1 = at the live (empty) edit
let _cmdHistoryDraft = '';

function _loadCmdHistory() {
  try {
    const raw = window.localStorage.getItem(_CMD_HISTORY_KEY) || '[]';
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch (_) { return []; }
}
function _pushCmdHistory(cmd) {
  const c = (cmd || '').trim();
  if (!c) return;
  const hist = _loadCmdHistory().filter((x) => x !== c);
  hist.unshift(c);
  try { window.localStorage.setItem(_CMD_HISTORY_KEY, JSON.stringify(hist.slice(0, _CMD_HISTORY_MAX))); }
  catch (_) {}
}
function _walkCmdHistory(dir) {
  if (!OPERATOR.command) return;
  const hist = _loadCmdHistory();
  if (!hist.length) return;
  if (_cmdHistoryIdx === -1) _cmdHistoryDraft = OPERATOR.command.value;
  _cmdHistoryIdx = Math.max(-1, Math.min(hist.length - 1, _cmdHistoryIdx + dir));
  OPERATOR.command.value = _cmdHistoryIdx === -1 ? _cmdHistoryDraft : hist[_cmdHistoryIdx];
  // Move caret to end so the next ↑ keeps walking (rather than triggering line-up).
  const len = OPERATOR.command.value.length;
  try { OPERATOR.command.setSelectionRange(len, len); } catch (_) {}
  _opUpdateSendEnabled();
}

if (OPERATOR.command) {
  OPERATOR.command.addEventListener('keydown', (e) => {
    // Only walk history when caret is on the first/last line and there's no
    // selection — otherwise arrow keys do their normal text-navigation thing.
    if (e.key === 'ArrowUp' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      const ta = OPERATOR.command;
      const beforeCaret = ta.value.slice(0, ta.selectionStart);
      if (!beforeCaret.includes('\n')) {  // on the first line
        e.preventDefault();
        _walkCmdHistory(+1);
      }
    } else if (e.key === 'ArrowDown' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      const ta = OPERATOR.command;
      const afterCaret = ta.value.slice(ta.selectionEnd);
      if (!afterCaret.includes('\n')) {  // on the last line
        e.preventDefault();
        _walkCmdHistory(-1);
      }
    } else if (e.key === 'Escape') {
      // Bail out of history walk; restore the in-flight draft.
      if (_cmdHistoryIdx !== -1) {
        _cmdHistoryIdx = -1;
        OPERATOR.command.value = _cmdHistoryDraft;
        _opUpdateSendEnabled();
      }
    }
  });
}

// When a command is actually submitted, push it onto the history.
if (OPERATOR.form) {
  OPERATOR.form.addEventListener('submit', () => {
    if (OPERATOR.command) _pushCmdHistory(OPERATOR.command.value);
    _cmdHistoryIdx = -1;
    _cmdHistoryDraft = '';
  });
}

/* ----- Top-bar Settings + History wiring ----- */
// Reuse the existing settings modal opener (sidebar Settings button calls it
// too); the single-pane top-bar gear is a parallel trigger.
const _topbarSettingsBtn = document.getElementById('topbar-settings-btn');
if (_topbarSettingsBtn) _topbarSettingsBtn.addEventListener('click', openSettings);

// v2.6: the ☰ panel is now the app menu. "Legacy workflows" lives here — the
// inline <details> on the welcome view is hidden until summoned.
const _menuLegacyBtn = document.getElementById('menu-legacy-btn');
if (_menuLegacyBtn) {
  _menuLegacyBtn.addEventListener('click', () => {
    _historyClose();
    const details = document.getElementById('operator-templates');
    if (!details) return;
    details.classList.add('is-summoned');
    details.open = true;
    details.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}
const _menuSettingsBtn = document.getElementById('menu-settings-btn');
if (_menuSettingsBtn) {
  _menuSettingsBtn.addEventListener('click', () => {
    _historyClose();
    openSettings();
  });
}

// Refresh the context strip after each operation completes so "Last run"
// reflects the freshest data without requiring a full view-switch.
(function _hookContextStripRefresh() {
  // The SSE handler dispatches 'complete' events through _opHandleEvent.
  // We don't want to wrap it (too invasive); instead poll the strip when
  // operatorState.finalRecord transitions from null → something. Cheap.
  let lastSeenFinalId = null;
  setInterval(() => {
    const fid = operatorState && operatorState.finalRecord && operatorState.finalRecord.id;
    if (fid && fid !== lastSeenFinalId) {
      lastSeenFinalId = fid;
      // Wait a moment for the operation_log.json on disk + log append to settle.
      setTimeout(() => { loadOperatorContextStrip(); _railThreadsFill(); }, 500);
    }
  }, 1500);
})();
