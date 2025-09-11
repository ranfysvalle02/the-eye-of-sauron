// --- Main Application Orchestrator ---
// Defines the shared app structure and initializes all modules.

// Global namespace for the app to share UI elements, state, and utilities
const SauronApp = {
 ui: {},
 state: {},
 utils: {},
};

// This function is called by leader.js once a tab is elected leader.
function initializeApp() {
 console.log(`Tab ${myTabId}: Initializing application...`);

 // 1. Cache all UI elements into the shared object
 SauronApp.ui = {
  scannerView: document.getElementById('scanner-view'),
  feedContainer: document.getElementById('feed-container'),
  feedControls: document.getElementById('feed-controls'),
  feedSearchInput: document.getElementById('feed-search-input'),
  clearFeedBtn: document.getElementById('clear-feed-btn'),
  scanTabsContainer: document.getElementById('scan-tabs-container'),
  feedPanesContainer: document.getElementById('feed-panes-container'),
  statusText: document.getElementById('status-text'),
  statusDot: document.getElementById('status-dot'),
  placeholder: document.getElementById('placeholder'),
  controlsContainer: document.getElementById('controls-container'),
  globalStopBtn: document.getElementById('global-stop-btn'),
  slackWebhookUrlInput: document.getElementById('slack-webhook-url'),
  saveWebhookBtn: document.getElementById('save-webhook-btn'),
  configStatusContainer: document.getElementById('config-status-container'),
  configStatusBtn: document.getElementById('config-status-btn'),
  configPopover: document.getElementById('config-popover'),
  resetConfigBtn: document.getElementById('reset-config-btn'),
  listenersList: document.getElementById('listeners-list'),
  listenerModal: document.getElementById('listener-modal'),
  listenerForm: document.getElementById('listener-form'),
  listenerModalTitle: document.getElementById('listener-modal-title'),
  listenerLabelInput: document.getElementById('listener-label'),
  listenerPatternInput: document.getElementById('listener-pattern'),
  originalListenerLabelInput: document.getElementById('original-listener-label'),
  showAddListenerModalBtn: document.getElementById('show-add-listener-modal-btn'),
  cancelListenerBtn: document.getElementById('cancel-listener-btn'),
  saveListenerBtn: document.getElementById('save-listener-btn'),
  listenerLabelError: document.getElementById('listener-label-error'),
  listenerPatternError: document.getElementById('listener-pattern-error'),
  regexValidityIndicator: document.getElementById('regex-validity-indicator'),
  regexTestString: document.getElementById('regex-test-string'),
  regexTestResult: document.getElementById('regex-test-result'),
  sourcesList: document.getElementById('sources-list'),
  sourceModal: document.getElementById('source-modal'),
  sourceForm: document.getElementById('source-form'),
  sourceModalTitle: document.getElementById('source-modal-title'),
  originalSourceNameInput: document.getElementById('original-source-name'),
  sourceNameInput: document.getElementById('source-name'),
  sourceApiUrlInput: document.getElementById('source-api-url'),
  sourceDataRootInput: document.getElementById('source-data-root'),
  sourceFieldsToCheckTextarea: document.getElementById('source-fields-to-check'),
  fieldsToCheckContainer: document.getElementById('fields-to-check-container'),
  sourceFieldMappingsTextarea: document.getElementById('source-field-mappings'),
  showAddSourceModalBtn: document.getElementById('show-add-source-modal-btn'),
  cancelSourceBtn: document.getElementById('cancel-source-btn'),
  saveSourceBtn: document.getElementById('save-source-btn'),
  globalScanControlBtn: document.getElementById('global-scan-control-btn'),
  fetchPreviewBtn: document.getElementById('fetch-preview-btn'),
  sourcePreviewContainer: document.getElementById('source-preview-container'),
  previewStatus: document.getElementById('preview-status'),
  previewContent: document.getElementById('preview-content'),
  selectedPathDisplay: document.getElementById('selected-path-display'),
  interactivePreviewItem: document.getElementById('interactive-preview-item'),
  rawJsonPreview: document.getElementById('raw-json-preview'),
  mappingInputsContainer: document.getElementById('mapping-inputs-container'),
  presetInputContainer: document.getElementById('preset-input-container'),
  presetInputLabel: document.getElementById('preset-input-label'),
  presetInput: document.getElementById('preset-input'),
  applyPresetBtn: document.getElementById('apply-preset-btn'),
  dashboardView: document.getElementById('dashboard-view'),
  analyticsDatePicker: document.getElementById('analytics-date-picker'),
  localStorageControls: document.getElementById('local-storage-controls'),
  clearLocalDashboardBtn: document.getElementById('clear-local-dashboard-btn'),
  kpiScansStarted: document.getElementById('kpi-scans-started'),
  kpiItemsMatched: document.getElementById('kpi-items-matched'),
  kpiSummariesGenerated: document.getElementById('kpi-summaries-generated'),
  relatedModal: document.getElementById('related-modal'),
  relatedModalSourceTitle: document.getElementById('related-modal-source-title'),
  relatedModalContent: document.getElementById('related-modal-content'),
  cancelRelatedBtn: document.getElementById('cancel-related-btn'),
  filterBtn: document.getElementById('filter-btn'),
  filterPopover: document.getElementById('filter-popover'),
  filterCountBadge: document.getElementById('filter-count-badge'),
  sourceFiltersContainer: document.getElementById('source-filters-container'),
  labelFiltersContainer: document.getElementById('label-filters-container'),
  clearFiltersBtn: document.getElementById('clear-filters-btn'),
  matchesView: document.getElementById('matches-view'),
  matchesSearchInput: document.getElementById('matches-search-input'),
  matchesFilterBtn: document.getElementById('matches-filter-btn'),
  matchesFilterPopover: document.getElementById('matches-filter-popover'),
  matchesFilterCountBadge: document.getElementById('matches-filter-count-badge'),
  matchesSourceFiltersContainer: document.getElementById('matches-source-filters-container'),
  matchesClearFiltersBtn: document.getElementById('matches-clear-filters-btn'),
  matchesSortSelect: document.getElementById('matches-sort-select'),
  matchesResultsContainer: document.getElementById('matches-results-container'),
  matchesPlaceholder: document.getElementById('matches-placeholder'),
 };

 // 2. Initialize shared state
 SauronApp.state = {
  currentPatterns: [],
  apiSources: [],
  activeScan: { sourceName: null, nextPage: 1 },
  currentStatus: 'idle',
  isGlobalScanActive: false,
  clientSideStop: false,
  currentSort: 'newest',
  activeFilters: { sources: new Set(), labels: new Set() },
  availableFilters: { sources: new Set(), labels: new Set() },
  matchesState: { query: '', sources: new Set(), sortOrder: 'desc', currentPage: 1, totalPages: 1, isLoading: false, isInitialized: false },
  matchesScrollObserver: null,
  allRelatedResults: [],
  currentRelatedPage: 1,
  relatedItemsPerPage: 5,
  dashboardCharts: { hourly: null, labels: null },
  currentAnalyticsDate: new Date().toISOString().split('T')[0],
  isMongoDbEnabled: true,
 };

 // 3. Define shared utilities and rendering functions
 SauronApp.utils.debounce = (func, delay) => {
  let timeoutId;
  return (...args) => {
   clearTimeout(timeoutId);
   timeoutId = setTimeout(() => {
    func.apply(this, args);
   }, delay);
  };
 };

 // MOVED HERE: This function is used by both ui.js and views.js
 SauronApp.createFeedCard = (item, delay = 0) => {
  const card = document.createElement('div');
  const isPending = item.summary_status === 'pending';
  card.className = 'feed-card brand-dark-bg border brand-border rounded-lg p-5 shadow-lg card-enter-animation opacity-0';
  card.style.animationDelay = `${delay}ms`;
  if (isPending) card.classList.add('summary-pending');
  card.dataset.itemId = item.id;
  card.dataset.itemData = JSON.stringify(item);
  
  const postTimeValue = typeof item.time === 'string' ? new Date(item.time) : new Date(item.time * 1000);
  const postTime = postTimeValue.toLocaleString('en-US', { timeZone: 'UTC' });

  const webhookUrl = localStorage.getItem('slackWebhookUrl') || '';
  const titleLink = item.url ? `<a href="${item.url}" target="_blank" class="text-xl font-bold text-white hover:text-green-400 transition-colors mb-2 sm:mb-0 break-all">${item.title}</a>` : `<span class="text-xl font-bold text-white mb-2 sm:mb-0 break-all">${item.title}</span>`;
  const viewSourceLink = item.url ? `<a href="${item.url}" target="_blank" class="hover:text-green-400 transition-colors"><i class="fa-solid fa-arrow-up-right-from-square mr-1"></i> View Source</a>` : `<span><i class="fa-solid fa-database mr-1"></i> ${item.source_name}</span>`;
  const summaryHTML = isPending ? `<div class="summary-content text-gray-400 text-sm"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Generating AI Summary...</div>` : `<div class="summary-content markdown-content text-gray-300 text-sm">${marked.parse(item.ai_summary || '')}</div>`;
  
  card.innerHTML = `
   <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-2">
    ${titleLink}
    <span class="text-xs font-mono text-white bg-blue-900/70 border border-blue-700 px-2 py-1 rounded-full w-max shrink-0 shadow-md">
     <i class="fa-solid fa-tag mr-1"></i> ${item.matched_label || 'DB'}
    </span>
   </div>
   <div class="flex items-center flex-wrap gap-x-4 gap-y-2 text-xs text-gray-400 mt-2 border-b border-gray-700 pb-3 mb-3">
    <span class="inline-flex items-center text-xs font-medium text-purple-300 bg-purple-900/50 border border-purple-700 px-2 py-0.5 rounded-full shadow">
     <i class="fa-solid fa-satellite-dish mr-1.5"></i> ${item.source_name}
    </span>
    <span><i class="fa-solid fa-user mr-1"></i> ${item.by || 'N/A'}</span>
    <span><i class="fa-solid fa-clock mr-1"></i> ${postTime} UTC</span>
    ${viewSourceLink}
    <button class="send-to-slack-btn text-gray-400 hover:text-white text-xs disabled:opacity-50 disabled:cursor-not-allowed" ${!webhookUrl || isPending ? 'disabled' : ''} title="${!webhookUrl ? 'Enter a Slack Webhook URL' : 'Send to Slack'}">
     <i class="fa-brands fa-slack mr-1"></i> Send to Slack
    </button>
    <button class="find-related-btn text-gray-400 hover:text-white text-xs disabled:opacity-50 disabled:cursor-not-allowed" ${isPending ? 'disabled' : ''} title="Find related items">
     <i class="fa-solid fa-brain mr-1"></i> Find Related
    </button>
   </div>
   <div class="summary-container">
    <h3 class="text-sm font-semibold text-green-400 mb-2">AI Summary</h3>
    ${summaryHTML}
   </div>`;
  return card;
 };

 // 4. Initialize all modules. This order is now safe.
 initConfig(SauronApp);
 initViews(SauronApp);
 initUI(SauronApp);
}