// --- Leader Election for Single Tab Experience ---
const myTabId = `sauron-tab-${Date.now()}-${Math.random()}`;
let isLeader = false;
let isAppInitialized = false;
let leaderCheckInterval = null;
let eventSource = null; // Hoisted to be accessible by shutdown logic

function showMultiTabOverlay() {
    let overlay = document.getElementById('multi-tab-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'multi-tab-overlay';
        overlay.style.position = 'fixed';
        overlay.style.inset = '0';
        overlay.style.background = '#121921';
        overlay.style.color = '#F9FAFB';
        overlay.style.display = 'grid';
        overlay.style.placeContent = 'center';
        overlay.style.textAlign = 'center';
        overlay.style.fontFamily = 'sans-serif';
        overlay.style.zIndex = '9999';
        overlay.innerHTML = `<style>@keyframes pulse_sauron { 0%, 100% { transform: scale(1); opacity: 0.8; } 50% { transform: scale(1.1); opacity: 1; } }</style>
            <img src="https://github.com/ranfysvalle02/the-eye-of-sauron/blob/main/d-eye.png?raw=true" alt="Pulsating Eye" style="width:150px;height:auto;margin:auto;margin-bottom:20px;animation:pulse_sauron 2s infinite ease-in-out;">
            <h1 style="color:#00ED64;font-size:1.8rem;margin:0;">One Tab to Rule Them All</h1>
            <p style="font-size:1.1rem;margin-top:8px;">This application is already open. Please close this tab to continue.</p>`;
        document.body.appendChild(overlay);
    }
    overlay.style.display = 'grid';
}

function hideMultiTabOverlay() {
    const overlay = document.getElementById('multi-tab-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
}

function shutdownApp() {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
        console.log(`Tab ${myTabId} (follower): SSE connection closed.`);
    }
}

function disableApp() {
    if (isLeader || isAppInitialized) {
        shutdownApp();
        isLeader = false;
        isAppInitialized = false;
    }
    showMultiTabOverlay();
}

function becomeLeader() {
    if (!isLeader) {
        console.log(`Tab ${myTabId} is now the LEADER.`);
        isLeader = true;
        localStorage.setItem('sauron_leader_heartbeat', Date.now());
        hideMultiTabOverlay();
        if (!isAppInitialized) {
            initializeApp();
            isAppInitialized = true;
        }
    }
    localStorage.setItem('sauron_leader_heartbeat', Date.now());
}

function checkLeader() {
    const leaderId = localStorage.getItem('sauron_leader_tab_id');
    const lastHeartbeat = parseInt(localStorage.getItem('sauron_leader_heartbeat') || '0', 10);
    const isHeartbeatStale = (Date.now() - lastHeartbeat) > 4000;

    if (!leaderId || isHeartbeatStale) {
        localStorage.setItem('sauron_leader_tab_id', myTabId);
        setTimeout(() => {
            if (localStorage.getItem('sauron_leader_tab_id') === myTabId) {
                becomeLeader();
            } else {
                disableApp();
            }
        }, 50);
    } else if (leaderId === myTabId) {
        becomeLeader();
    } else {
        disableApp();
    }
}

window.addEventListener('storage', (e) => {
    if (e.key === 'sauron_leader_tab_id' || e.key === 'sauron_leader_heartbeat') {
        checkLeader();
    }
});

window.addEventListener('beforeunload', () => {
    if (isLeader) {
        localStorage.removeItem('sauron_leader_tab_id');
        localStorage.removeItem('sauron_leader_heartbeat');
    }
});

// --- Main Application Logic ---
function initializeApp() {
    console.log(`Tab ${myTabId}: Initializing application...`);
    const ui = {
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
        // New UI elements for filtering and sorting
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
   
    let currentPatterns = [];
    let apiSources = [];
    let activeScan = { sourceName: null, nextPage: 1 };
    let currentStatus = 'idle';
    let isGlobalScanActive = false;
    let selectedJsonPath = null;
    let previewData = null;
    let currentFieldsToCheck = [];
    const requiredMappings = ["id", "title", "url", "text", "by", "time"];
    let listenerFormValidity = { label: false, pattern: false };
    let clientSideStop = false; 
   
   // State for filtering and sorting 
   let currentSort = 'newest'; 
   let activeFilters = { sources: new Set(), labels: new Set() }; 
   let availableFilters = { sources: new Set(), labels: new Set() }; 

    let matchesState = {
        query: '',
        sources: new Set(),
        sortOrder: 'desc',
        currentPage: 1,
        totalPages: 1,
        isLoading: false,
        isInitialized: false
    };
    let matchesScrollObserver = null;
    let allRelatedResults = [];
    let currentRelatedPage = 1;
    const relatedItemsPerPage = 5;

    let dashboardCharts = { hourly: null, labels: null };
    let currentAnalyticsDate = new Date().toISOString().split('T')[0];
    let tableSortState = {};
    let isMongoDbEnabled = true;

    const sourcePresets = {
        github: {
            promptLabel: "GitHub Repository",
            promptPlaceholder: "owner/repo",
            defaultInput: "langchain-ai/langchain",
            name: (repo) => `${repo} GitHub Issues`,
            apiUrl: (repo) => `https://api.github.com/repos/${repo}/issues?state=all&per_page=100&page={PAGE}`,
            dataRoot: "",
            fieldMappings: { "id": "id", "title": "title", "url": "html_url", "text": "body", "by": "user.login", "time": "created_at" },
            fieldsToCheck: ["title", "body"]
        },
        hn: {
            promptLabel: "Hacker News Story Query",
            promptPlaceholder: "e.g., AI",
            defaultInput: "AI",
            name: (query) => `Hacker News '${query}' Stories`,
            apiUrl: (query) => `http://hn.algolia.com/api/v1/search_by_date?query=${encodeURIComponent(query)}&tags=story&page={PAGE}`,
            dataRoot: "hits",
            fieldMappings: { "id": "objectID", "title": "title", "url": "url", "text": "story_text", "by": "author", "time": "created_at" },
            fieldsToCheck: ["title", "story_text"]
        },
        'hn-comments': {
            promptLabel: "Hacker News Comment Query",
            promptPlaceholder: "e.g., mongodb",
            defaultInput: "mongodb",
            name: (query) => `Hacker News '${query}' Comments`,
            apiUrl: (query) => `http://hn.algolia.com/api/v1/search_by_date?query=${encodeURIComponent(query)}&tags=comment&page={PAGE}`,
            dataRoot: "hits",
            fieldMappings: { "id": "objectID", "title": "story_title", "url": "story_url", "text": "comment_text", "by": "author", "time": "created_at" },
            fieldsToCheck: ["comment_text"]
        },
        'reddit': {
            promptLabel: "Subreddit & Query",
            promptPlaceholder: "e.g., programming/rust",
            defaultInput: "programming/rust",
            name: (subreddit, query) => `Reddit r/${subreddit} '${query}'`,
            apiUrl: (subreddit, query) => `https://www.reddit.com/r/${subreddit}/search.json?q=${encodeURIComponent(query)}&sort=new&restrict_sr=on&limit=100`,
            dataRoot: "data.children",
            fieldMappings: { "id": "data.id", "title": "data.title", "url": "data.permalink", "text": "data.selftext", "by": "data.author", "time": "data.created_utc" },
            fieldsToCheck: ["data.title", "data.selftext"]
        },
        'medium': {
            promptLabel: "Medium Tag",
            promptPlaceholder: "e.g., programming",
            defaultInput: "programming",
            name: (tag) => `Medium Tag '${tag}'`,
            apiUrl: (tag) => `https://api.rss2json.com/v1/api.json?rss_url=https%3A%2F%2Fmedium.com%2Ffeed%2Ftag%2F${encodeURIComponent(tag)}`,
            dataRoot: "items",
            fieldMappings: { "id": "guid", "title": "title", "url": "link", "text": "description", "by": "author", "time": "pubDate" },
            fieldsToCheck: ["title", "description"]
        }
    };

    const debounce = (func, delay) => {
        let timeoutId;
        return (...args) => {
            clearTimeout(timeoutId);
            timeoutId = setTimeout(() => {
                func.apply(this, args);
            }, delay);
        };
    };

    function setupConfigControls() {
        ui.slackWebhookUrlInput.value = localStorage.getItem('slackWebhookUrl') || '';
        ui.saveWebhookBtn.addEventListener('click', () => {
            localStorage.setItem('slackWebhookUrl', ui.slackWebhookUrlInput.value.trim());
            ui.saveWebhookBtn.textContent = 'Saved!';
            setTimeout(() => { ui.saveWebhookBtn.textContent = 'Save'; }, 2000);
            updateAllSlackButtons();
        });
        ui.configStatusBtn.addEventListener('click', () => {
            ui.configPopover.classList.toggle('hidden');
            ui.configStatusBtn.classList.toggle('open');
        });
        ui.resetConfigBtn.addEventListener('click', handleResetConfig);
    }

    async function loadConfiguration() {
        const localPatterns = localStorage.getItem('localPatterns');
        const localApiSources = localStorage.getItem('localApiSources');
       
        let patternsLoaded = false;
        let sourcesLoaded = false;

        if (localPatterns) {
            try {
                setPatterns(JSON.parse(localPatterns));
                renderPatterns();
                patternsLoaded = true;
            } catch (e) { console.error("Could not parse local patterns", e); }
        }
        if (localApiSources) {
            try {
                setSources(JSON.parse(localApiSources));
                renderSources();
                sourcesLoaded = true;
            } catch (e) { console.error("Could not parse local sources", e); }
        }
       
        updateConfigStatusIndicator();

        if (!patternsLoaded) {
            await fetchData('/patterns', setPatterns, renderPatterns);
        }
        if (!sourcesLoaded) {
            await fetchData('/api-sources', setSources, renderSources);
        }
    }

    function saveConfigLocally() {
        localStorage.setItem('localPatterns', JSON.stringify(currentPatterns));
        localStorage.setItem('localApiSources', JSON.stringify(apiSources));
        updateConfigStatusIndicator();
    }

    function updateConfigStatusIndicator() {
        const hasLocalConfig = !!localStorage.getItem('localPatterns') || !!localStorage.getItem('localApiSources');
        if (hasLocalConfig) {
            ui.configStatusContainer.classList.remove('hidden');
        } else {
            ui.configStatusContainer.classList.add('hidden');
        }
    }
   
    async function handleResetConfig() {
        if (confirm("Are you sure you want to reset your Listeners and API Sources to the server defaults? This action cannot be undone.")) {
            localStorage.removeItem('localPatterns');
            localStorage.removeItem('localApiSources');
            window.location.reload();
        }
    }

    async function fetchData(url, setter, renderer, postRender) {
        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
            const data = await response.json();
            setter(data);
            if (renderer) renderer();
            if (postRender) postRender();
        } catch (e) {
            console.error(`Failed to fetch from ${url}:`, e);
        }
    }
    async function updateDataOnServer(url, data) {
        try {
                const response = await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            });
            if (response.ok) {
                saveConfigLocally();
            } else {
                console.error("Failed to save config to server");
            }
        } catch (e) {
            console.error("Error updating server config:", e);
        }
    }
    const setPatterns = (data) => { currentPatterns = data; };
    const updatePatterns = () => updateDataOnServer('/patterns', currentPatterns);
    function renderPatterns() {
        ui.listenersList.innerHTML = currentPatterns.length === 0
            ? '<p class="text-sm text-gray-500 italic p-2 text-center">Add a listener to begin.</p>'
            : '';
        currentPatterns.forEach(p => {
            const div = document.createElement('div');
            div.className = 'flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80 transition-all shadow-md';
            div.innerHTML = `
                <div class="flex-1 overflow-hidden">
                    <p class="font-semibold text-sm truncate" title="${p.label}">${p.label}</p>
                    <p class="text-xs text-gray-400 font-mono truncate" title="${p.pattern}">${p.pattern}</p>
                </div>
                <div class="flex items-center space-x-3 ml-2">
                    <button title="Edit Listener" class="edit-btn text-gray-500 hover:text-blue-400 transition-transform transform hover:scale-125" data-type="listener" data-label="${p.label}">
                        <i class="fa-solid fa-pencil"></i>
                    </button>
                    <button title="Remove Listener" class="remove-btn text-gray-500 hover:text-red-400 transition-transform transform hover:scale-125" data-type="listener" data-label="${p.label}">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            `;
            ui.listenersList.appendChild(div);
        });
    }
    const setSources = (data) => {
        apiSources = data.map(source => ({
            ...source,
            // Default scan_enabled to true if it's missing (for backward compatibility)
            scan_enabled: source.scan_enabled !== false 
        }));
    };
    const updateSources = () => updateDataOnServer('/api-sources', apiSources);
   
    function renderSources() {
        ui.sourcesList.innerHTML = apiSources.length === 0
            ? '<p class="text-sm text-gray-500 italic p-2 text-center">Add an API source to scan.</p>'
            : '';
        apiSources.forEach(source => {
            const div = document.createElement('div');
            const isScanning = isGlobalScanActive && ['scanning', 'manually_paused', 'scan_paused'].includes(currentStatus);
            div.className = `flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80 transition-all shadow-md border ${isScanning ? 'scanning-source' : 'brand-border'}`;
           
            const scanIndicatorHTML = isScanning
                ? `<div class="scan-indicator w-8 text-center"><i class="fa-solid fa-spinner fa-spin text-blue-400 text-lg" title="Scanning..."></i></div>`
                : '';

            div.innerHTML = `
                <div class="flex items-center flex-1 overflow-hidden">
                    <label class="relative inline-flex items-center cursor-pointer" title="Toggle scanning for this source">
                        <input type="checkbox" class="sr-only peer source-scan-toggle" data-name="${source.name}" ${source.scan_enabled ? 'checked' : ''}>
                        <div class="w-9 h-5 bg-gray-600 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-green-600"></div>
                    </label>
                    <div class="flex-1 overflow-hidden ml-3">
                        <p class="font-semibold text-sm truncate font-mono" title="${source.name}">${source.name}</p>
                    </div>
                </div>
                <div class="flex items-center space-x-3 ml-2">
                    ${scanIndicatorHTML}
                    <button title="Edit API Source" class="edit-btn text-gray-500 hover:text-blue-400 transition-transform transform hover:scale-125" data-type="source" data-name="${source.name}">
                        <i class="fa-solid fa-pencil"></i>
                    </button>
                    <button title="Remove API Source" class="remove-btn text-gray-500 hover:text-red-400 transition-transform transform hover:scale-125" data-type="source" data-name="${source.name}">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            `;
            ui.sourcesList.appendChild(div);
        });
    }

    function testRegexLocally() {
        const patternStr = ui.listenerPatternInput.value;
        const testStr = ui.regexTestString.value;
        const resultEl = ui.regexTestResult;

        if (!patternStr || !testStr) {
            resultEl.innerHTML = '<span class="text-gray-500">Enter a pattern and test string.</span>';
            return;
        }

        try {
            let cleanPattern = patternStr;
            let flags = 'g';

            if (patternStr.startsWith('(?i)')) {
                cleanPattern = patternStr.substring(4);
                flags += 'i';
            }
           
            const regex = new RegExp(cleanPattern, flags);
            const highlighted = testStr.replace(regex, (match) => `<span class="regex-match">${match}</span>`);
           
            if (highlighted !== testStr) {
                resultEl.innerHTML = highlighted;
            } else {
                resultEl.innerHTML = '<span class="text-gray-500">No matches found.</span>';
            }
        } catch (e) {
            resultEl.innerHTML = `<span class="text-yellow-500">Invalid JavaScript Regex: ${e.message}</span>`;
        }
    }

    const validateRegexOnServer = debounce(async () => {
        const pattern = ui.listenerPatternInput.value;
        const errorEl = ui.listenerPatternError;
        const indicatorEl = ui.regexValidityIndicator;

        if (!pattern) {
            errorEl.textContent = 'Pattern cannot be empty.';
            indicatorEl.innerHTML = '<i class="fas fa-times-circle text-red-500"></i>';
            listenerFormValidity.pattern = false;
            updateSaveButtonState();
            return;
        }

        try {
            const response = await fetch('/validate-regex', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pattern })
            });
            const result = await response.json();
            if (result.valid) {
                errorEl.textContent = '';
                indicatorEl.innerHTML = '<i class="fas fa-check-circle text-green-500"></i>';
                listenerFormValidity.pattern = true;
            } else {
                errorEl.textContent = result.error;
                indicatorEl.innerHTML = '<i class="fas fa-times-circle text-red-500"></i>';
                listenerFormValidity.pattern = false;
            }
        } catch (e) {
            errorEl.textContent = 'Could not reach validation server.';
            indicatorEl.innerHTML = '<i class="fas fa-exclamation-triangle text-yellow-500"></i>';
            listenerFormValidity.pattern = false;
        }
        updateSaveButtonState();
    }, 300);

    function validateLabel() {
        const newLabel = ui.listenerLabelInput.value.trim();
        const originalLabel = ui.originalListenerLabelInput.value;
        const errorEl = ui.listenerLabelError;
       
        if (!newLabel) {
            errorEl.textContent = 'Label cannot be empty.';
            listenerFormValidity.label = false;
            updateSaveButtonState();
            return;
        }

        const isEditing = !!originalLabel;
        const isDuplicate = currentPatterns.some(p => p.label === newLabel) && (!isEditing || newLabel !== originalLabel);

        if (isDuplicate) {
            errorEl.textContent = 'This label is already in use.';
            listenerFormValidity.label = false;
        } else {
            errorEl.textContent = '';
            listenerFormValidity.label = true;
        }
        updateSaveButtonState();
    }

    function updateSaveButtonState() {
        ui.saveListenerBtn.disabled = !(listenerFormValidity.label && listenerFormValidity.pattern);
    }
   
    function showPresetInput(presetKey) {
        const preset = sourcePresets[presetKey];
        if (!preset) return;
   
        ui.presetInputContainer.classList.remove('hidden');
        ui.presetInputLabel.textContent = preset.promptLabel;
        ui.presetInput.placeholder = preset.promptPlaceholder;
        ui.presetInput.value = preset.defaultInput;
        ui.applyPresetBtn.dataset.presetKey = presetKey;
        ui.presetInput.focus();
    }
   
    function handleApplyPreset() {
        const presetKey = ui.applyPresetBtn.dataset.presetKey;
        const userInput = ui.presetInput.value.trim();
        const preset = sourcePresets[presetKey];

        if (!preset || !userInput) return;

        // Special handling for Reddit's "subreddit/query" format
        if (presetKey === 'reddit') {
            const parts = userInput.split('/');
            if (parts.length < 2) {
                alert("Invalid format. Please use 'subreddit/query'.");
                return;
            }
            const subreddit = parts[0].trim();
            const query = parts.slice(1).join('/').trim();
           
            if (!subreddit || !query) {
                alert("Invalid format. Subreddit and query cannot be empty.");
                return;
            }

            ui.sourceNameInput.value = preset.name(subreddit, query);
            ui.sourceApiUrlInput.value = preset.apiUrl(subreddit, query);
        } else {
            // Standard handling for other presets
            ui.sourceNameInput.value = preset.name(userInput);
            ui.sourceApiUrlInput.value = preset.apiUrl(userInput);
        }
   
        ui.sourceDataRootInput.value = preset.dataRoot;
       
        currentFieldsToCheck = [...preset.fieldsToCheck];
        renderFieldsToCheck();
   
        ui.sourceFieldMappingsTextarea.value = JSON.stringify(preset.fieldMappings, null, 2);
        populateMappingsFromTextarea();
       
        ui.presetInputContainer.classList.add('hidden');
    }

    function setupManagementEventListeners() {
        ui.showAddListenerModalBtn.addEventListener('click', () => openModal('listener'));
        ui.showAddSourceModalBtn.addEventListener('click', () => openModal('source'));
        ui.cancelListenerBtn.addEventListener('click', () => closeModal('listener'));
        ui.cancelSourceBtn.addEventListener('click', () => closeModal('source'));
        ui.cancelRelatedBtn.addEventListener('click', () => closeModal('related'));
        ui.listenerModal.addEventListener('click', (e) => { if (e.target === ui.listenerModal) closeModal('listener'); });
        ui.sourceModal.addEventListener('click', (e) => { if (e.target === ui.sourceModal) closeModal('source'); });
        ui.relatedModal.addEventListener('click', (e) => { if (e.target === ui.relatedModal) closeModal('related'); });
       
        ui.listenerForm.addEventListener('submit', handleSave);
        ui.sourceForm.addEventListener('submit', handleSave);
       
        ui.globalStopBtn.addEventListener('click', handleStopScan);

        // Listener for source scan toggles
        ui.sourcesList.addEventListener('change', e => {
            if (e.target.classList.contains('source-scan-toggle')) {
                const sourceName = e.target.dataset.name;
                const source = apiSources.find(s => s.name === sourceName);
                if (source) {
                    source.scan_enabled = e.target.checked;
                    updateSources(); // This saves to localStorage and server
                }
            }
        });

        document.addEventListener('click', (e) => {
            if (!ui.configStatusContainer.contains(e.target)) {
                ui.configPopover.classList.add('hidden');
                ui.configStatusBtn.classList.remove('open');
            }
            if (ui.filterPopover && !ui.filterBtn.contains(e.target) && !ui.filterPopover.contains(e.target)) {
                ui.filterPopover.classList.add('hidden');
            }
           
            const btn = e.target.closest('.edit-btn, .remove-btn');
            if (btn) {
                const { type, ...data } = btn.dataset;
                if (btn.classList.contains('edit-btn')) openModal(type, data);
                else handleRemove(type, data);
                return;
            }
            const tabBtn = e.target.closest('.view-tab-btn');
            if(tabBtn) {
                handleViewSwitch(tabBtn.dataset.view);
            }
        });

        ui.feedContainer.addEventListener('click', (e) => {
            if(e.target.closest('.scan-tab')) handleTabSwitch(e.target.closest('.scan-tab'));
        });

        ui.feedControls.addEventListener('click', (e) => {
            if(e.target.closest('#clear-feed-btn')) handleClearFeed();
        });

        ui.feedContainer.addEventListener('click', handleFeedActions);
        ui.fetchPreviewBtn.addEventListener('click', handleFetchPreview);
        ui.sourceModal.addEventListener('click', e => {
            if(e.target.matches('.preview-tab-btn')) handleTabSwitch(e.target);
            if(e.target.closest('.json-value')) handleJsonItemClick(e.target.closest('.json-value'));
            if(e.target.closest('.mapping-target-btn')) handleMappingTargetClick(e.target.closest('.mapping-target-btn'));
           
            const presetBtn = e.target.closest('.preset-btn');
            if (presetBtn && !presetBtn.disabled) {
                showPresetInput(presetBtn.dataset.preset);
            }
        });
        ui.applyPresetBtn.addEventListener('click', handleApplyPreset);

        ui.sourceDataRootInput.addEventListener('input', updateInteractivePreview);
        ui.fieldsToCheckContainer.addEventListener('click', e => {
            if (e.target.closest('.remove-field-btn')) {
                const field = e.target.closest('.remove-field-btn').dataset.field;
                currentFieldsToCheck = currentFieldsToCheck.filter(f => f !== field);
                renderFieldsToCheck();
                updateInteractivePreview();
            }
        });

        ui.interactivePreviewItem.addEventListener('change', e => {
            if (e.target.matches('.json-checkbox')) {
                const path = e.target.dataset.path;
                if (e.target.checked) {
                    if (!currentFieldsToCheck.includes(path)) {
                        currentFieldsToCheck.push(path);
                    }
                } else {
                    currentFieldsToCheck = currentFieldsToCheck.filter(p => p !== path);
                }
                renderFieldsToCheck();
            }
        });

        ui.listenerLabelInput.addEventListener('input', validateLabel);
        ui.listenerPatternInput.addEventListener('input', () => {
            validateRegexOnServer();
            testRegexLocally();
        });
        ui.regexTestString.addEventListener('input', testRegexLocally);
       
        ui.clearLocalDashboardBtn.addEventListener('click', handleClearLocalDashboard);
    }

    function openModal(type, data = null) {
        const isEdit = data !== null;
        const modal = ui[`${type}Modal`];
        const modalContent = modal.querySelector('.modal-content');
       
        if (type !== 'related') {
            const form = ui[`${type}Form`];
            form.reset();
            ui[`${type}ModalTitle`].textContent = `${isEdit ? 'Edit' : 'Add New'} ${type === 'listener' ? 'Listener' : 'API Source'}`;
        }

        if (type === 'listener') {
            ui.listenerLabelError.textContent = '';
            ui.listenerPatternError.textContent = '';
            ui.regexValidityIndicator.innerHTML = '';
            ui.regexTestString.value = '';
            ui.regexTestResult.innerHTML = '<span class="text-gray-500">Enter a pattern and test string.</span>';
           
            ui.originalListenerLabelInput.value = isEdit ? data.label : '';
            if (isEdit) {
                const p = currentPatterns.find(p => p.label === data.label);
                ui.listenerLabelInput.value = p.label;
                ui.listenerPatternInput.value = p.pattern;
            }
            validateLabel();
            validateRegexOnServer.flush ? validateRegexOnServer.flush() : validateRegexOnServer();
            testRegexLocally();
        } else if (type === 'source') {
            ui.presetInputContainer.classList.add('hidden');
            ui.presetInput.value = '';
            const presetsDiv = modal.querySelector('#source-presets');
            if (presetsDiv) {
                presetsDiv.style.display = isEdit ? 'none' : 'block';
            }
            resetPreviewer();
            renderMappingInputs();
            ui.originalSourceNameInput.value = isEdit ? data.name : '';
            if (isEdit) {
                const s = apiSources.find(s => s.name === data.name);
                ui.sourceNameInput.value = s.name;
                ui.sourceApiUrlInput.value = s.apiUrl;
                ui.sourceDataRootInput.value = s.dataRoot || '';
                currentFieldsToCheck = s.fieldsToCheck || [];
                ui.sourceFieldMappingsTextarea.value = JSON.stringify(s.fieldMappings || {}, null, 2);
            } else {
                const defaultMappings = {};
                requiredMappings.forEach(k => defaultMappings[k] = "");
                ui.sourceFieldMappingsTextarea.value = JSON.stringify(defaultMappings, null, 2);
                currentFieldsToCheck = [];
            }
            renderFieldsToCheck();
            populateMappingsFromTextarea();
        }
        modal.classList.remove('hidden');
        setTimeout(() => {
            modal.classList.remove('opacity-0');
            modalContent.classList.remove('scale-95');
        }, 10);
    }
    function closeModal(type) {
        const modal = ui[`${type}Modal`];
        const modalContent = modal.querySelector('.modal-content');
        modal.classList.add('opacity-0');
        modalContent.classList.add('scale-95');
        setTimeout(() => modal.classList.add('hidden'), 300);
    }
    function handleSave(e) {
        e.preventDefault();
        const type = e.target.id.split('-')[0];
        if (type === 'listener') {
            const newLabel = ui.listenerLabelInput.value.trim();
            const originalLabel = ui.originalListenerLabelInput.value;
            const isEditing = !!originalLabel;

            const patternData = { label: newLabel, pattern: ui.listenerPatternInput.value.trim() };
            if (isEditing) {
                currentPatterns = currentPatterns.map(p => p.label === originalLabel ? patternData : p);
            } else {
                currentPatterns.push(patternData);
            }
            renderPatterns();
            updatePatterns();
        } else if (type === 'source') {
            syncMappingsToTextarea();
            syncFieldsToCheckToTextarea();
            const newName = ui.sourceNameInput.value.trim();
            const originalName = ui.originalSourceNameInput.value;
            const isEditing = !!originalName;
            if (apiSources.some(s => s.name === newName) && (!isEditing || newName !== originalName)) {
                return alert("An API source with that name already exists.");
            }
            try {
                const sourceData = {
                    name: newName,
                    apiUrl: ui.sourceApiUrlInput.value.trim(),
                    httpMethod: "GET",
                    paginationStyle: "page_number",
                    dataRoot: ui.sourceDataRootInput.value.trim(),
                    fieldsToCheck: ui.sourceFieldsToCheckTextarea.value.split('\n').map(f => f.trim()).filter(Boolean),
                    fieldMappings: JSON.parse(ui.sourceFieldMappingsTextarea.value)
                };
                if (isEditing) {
                    const existingSource = apiSources.find(s => s.name === originalName);
                    const updatedSource = { ...existingSource, ...sourceData }; // Preserves existing properties like scan_enabled
                    apiSources = apiSources.map(s => s.name === originalName ? updatedSource : s);
                } else {
                    sourceData.scan_enabled = true; // Default for new sources
                    apiSources.push(sourceData);
                }
                renderSources();
                updateSources();
            } catch (err) {
                return alert("Invalid JSON in Field Mappings. Please check the format.");
            }
        }
        closeModal(type);
    }
    function handleRemove(type, data) {
        if (type === 'listener') {
            currentPatterns = currentPatterns.filter(p => p.label !== data.label);
            renderPatterns(); updatePatterns();
        } else if (type === 'source') {
            apiSources = apiSources.filter(s => s.name !== data.name);
            renderSources(); updateSources();
        }
    }
    function createFeedCard(item, delay = 0) { 
     const card = document.createElement('div'); 
     const isPending = item.summary_status === 'pending'; 
     card.className = 'feed-card brand-dark-bg border brand-border rounded-lg p-5 shadow-lg card-enter-animation opacity-0'; 
     card.style.animationDelay = `${delay}ms`; 
     if (isPending) card.classList.add('summary-pending'); 
     card.dataset.itemId = item.id; 
     card.dataset.itemData = JSON.stringify(item);
        
        // MODIFICATION START: Handle both ISO string and Unix timestamp for time
        const postTimeValue = typeof item.time === 'string' 
            ? new Date(item.time) 
            : new Date(item.time * 1000);
     const postTime = postTimeValue.toLocaleString('en-US', { timeZone: 'UTC' });
        // MODIFICATION END

     const webhookUrl = localStorage.getItem('slackWebhookUrl') || ''; 
     const titleLink = item.url 
       ? `<a href="${item.url}" target="_blank" class="text-xl font-bold text-white hover:text-green-400 transition-colors mb-2 sm:mb-0 break-all">${item.title}</a>` 
       : `<span class="text-xl font-bold text-white mb-2 sm:mb-0 break-all">${item.title}</span>`; 
     const viewSourceLink = item.url 
       ? `<a href="${item.url}" target="_blank" class="hover:text-green-400 transition-colors"><i class="fa-solid fa-arrow-up-right-from-square mr-1"></i> View Source</a>` 
       : `<span><i class="fa-solid fa-database mr-1"></i> ${item.source_name}</span>`; 
     const summaryHTML = isPending 
       ? `<div class="summary-content text-gray-400 text-sm"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Generating AI Summary...</div>` 
       : `<div class="summary-content markdown-content text-gray-300 text-sm">${marked.parse(item.ai_summary || '')}</div>`; 
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
         <button class="send-to-slack-btn text-gray-400 hover:text-white text-xs disabled:opacity-50 disabled:cursor-not-allowed transition-colors" ${!webhookUrl || isPending ? 'disabled' : ''} title="${!webhookUrl ? 'Enter a Slack Webhook URL' : isPending ? 'Summary not ready' : 'Send to Slack'}"> 
           <i class="fa-brands fa-slack mr-1"></i> Send to Slack 
         </button> 
         <button class="find-related-btn text-gray-400 hover:text-white text-xs disabled:opacity-50 disabled:cursor-not-allowed transition-colors" ${isPending ? 'disabled' : ''} title="${isPending ? 'Summary not ready' : 'Find related items'}"> 
           <i class="fa-solid fa-brain mr-1"></i> Find Related 
         </button> 
       </div> 
       <div class="summary-container"> 
         <h3 class="text-sm font-semibold text-green-400 mb-2">AI Summary</h3> 
         ${summaryHTML} 
       </div> 
     `; 
     return card; 
   }
    function updateAllSlackButtons() {
        const url = localStorage.getItem('slackWebhookUrl') || '';
        document.querySelectorAll('.feed-card .send-to-slack-btn').forEach(btn => {
            const card = btn.closest('.feed-card');
            const isPending = card.classList.contains('summary-pending');
            btn.disabled = !url || isPending;
            btn.title = !url ? 'Enter a Slack Webhook URL' : isPending ? 'Summary not ready' : 'Send to Slack';
        });
    }
    function setStatus(status, text) {
        ui.statusText.textContent = text;
        ui.statusDot.className = 'w-3 h-3 rounded-full transition-all';
        const statusClasses = {
            scanning: 'bg-yellow-500 animate-pulse',
            scan_paused: 'bg-yellow-500',
            manually_paused: 'bg-yellow-500',
            rate_limit_paused: 'bg-red-500 animate-pulse',
            error: 'bg-red-500',
            idle: 'bg-gray-500'
        };
        ui.statusDot.classList.add(...(statusClasses[status] || statusClasses.idle).split(' '));
    }

    function updateGlobalScanControls() {
        const isAnyScanActive = ['scanning', 'scan_paused', 'manually_paused'].includes(currentStatus);
        const btn = ui.globalScanControlBtn;

        // Clone to safely remove old listeners
        const newBtn = btn.cloneNode(true);
        btn.parentNode.replaceChild(newBtn, btn);
        ui.globalScanControlBtn = newBtn;

        if (isAnyScanActive) {
            newBtn.innerHTML = `<i class="fa-solid fa-stop mr-2"></i>Stop All Scans`;
            newBtn.className = 'w-full px-3 py-1 text-sm font-semibold rounded-md bg-red-800 hover:bg-red-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5 text-white';
            newBtn.addEventListener('click', handleStopScan);
        } else {
            newBtn.innerHTML = `<i class="fa-solid fa-play mr-2"></i>Scan All`;
            newBtn.className = 'w-full px-3 py-1 text-sm font-semibold rounded-md bg-green-800 hover:bg-green-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5 text-white';
            newBtn.addEventListener('click', scanAllSources);
        }
    }

    function tearDownTabs() {
        isGlobalScanActive = false;
        ui.scanTabsContainer.innerHTML = '';
        ui.scanTabsContainer.classList.add('hidden');
        ui.feedPanesContainer.innerHTML = ''; // Clear all panes
    }

    function handleStatusUpdate(data) {
        currentStatus = data.status;
        setStatus(data.status, data.reason || data.status);
        if (data.source_name) activeScan.sourceName = data.source_name;
        if (data.next_page) activeScan.nextPage = data.next_page;
       
        const isScanInProgress = ['scanning', 'scan_paused', 'manually_paused'].includes(data.status);

        if (['idle', 'error'].includes(data.status)) {
            activeScan.sourceName = null;
            if(isGlobalScanActive) tearDownTabs();
        }

        if (isScanInProgress) {
            ui.globalStopBtn.classList.remove('hidden');
        } else {
            ui.globalStopBtn.classList.add('hidden');
        }

        renderSources();
        updateControlsUI(data);
        updateGlobalScanControls();

        if (data.status === 'manually_paused') {
            document.querySelectorAll('.feed-card.summary-pending').forEach(card => {
                const summaryContent = card.querySelector('.summary-content');
                if (summaryContent && summaryContent.innerHTML.includes('fa-spinner')) {
                    summaryContent.innerHTML = `
                        <div class="flex items-center gap-4">
                            <p class="text-yellow-400">Summary generation paused.</p>
                            <button class="generate-summary-btn px-2 py-1 text-xs font-semibold rounded bg-green-800 hover:bg-green-700 transition-all shadow hover:shadow-lg transform hover:-translate-y-0.5">Generate</button>
                        </div>`;
                }
            });
        }
    }
    function updateControlsUI(data = {}) {
        const controls = ui.controlsContainer;
        const status = data.status || currentStatus;
        switch (status) {
            case 'scanning':
                controls.innerHTML = `
                    <div class="sauron-eye-container">
                        <div class="sauron-eye"></div>
                        <div class="scan-beam"></div>
                    </div>
                    <p class="text-sm text-gray-400 mt-4">${data.reason || 'Scanning...'}</p>
                    ${isGlobalScanActive ? '<button id="global-pause-btn" class="mt-2 px-4 py-2 text-sm font-semibold rounded-md bg-yellow-700 hover:bg-yellow-600 transition-all shadow text-white"><i class="fa-solid fa-pause mr-2"></i>Pause All</button>' : ''}
                `;
                if(isGlobalScanActive) document.getElementById('global-pause-btn').addEventListener('click', handlePauseScan);
                break;
             case 'manually_paused':
                controls.innerHTML = `
                    <img src="https://github.com/ranfysvalle02/the-eye-of-sauron/blob/main/d-eye.png?raw=true" style="width:20%;" alt="Scan Paused" class="mx-auto mb-4 scan-paused-animation">
                    <p class="text-lg font-semibold text-yellow-400 mb-3">Scan Paused</p>
                    ${isGlobalScanActive ? '<button id="global-resume-btn" class="px-6 py-2 font-semibold rounded-md bg-green-700 hover:bg-green-600 transition-all shadow-lg text-white"><i class="fa-solid fa-play mr-2"></i>Resume All</button>' : ''}
                `;
                if(isGlobalScanActive) document.getElementById('global-resume-btn').addEventListener('click', handleResumeScan);
                break;
            case 'scan_paused':
                 controls.innerHTML = `
                    <img src="https://github.com/ranfysvalle02/the-eye-of-sauron/blob/main/d-eye.png?raw=true" style="width:20%;" alt="Scan Paused" class="mx-auto mb-4 scan-paused-animation">
                    <p class="text-lg font-semibold text-yellow-400 mb-3">${data.reason}</p>
                    <div class="flex items-center gap-4">
                        <button id="continue-scan-btn" class="px-6 py-2 font-semibold rounded-md bg-blue-700 hover:bg-blue-600 transition-all shadow-lg hover:shadow-xl transform hover:-translate-y-0.5"><i class="fa-solid fa-forward mr-2"></i>Continue Scan</button>
                    </div>`;
                document.getElementById('continue-scan-btn').addEventListener('click', () => startScan(activeScan.sourceName, activeScan.nextPage));
                break;
            case 'rate_limit_paused':
                controls.innerHTML = `
                    <p class="text-red-400 font-semibold mb-3">${data.reason}</p>
                    <button id="rate-limit-resume-btn" class="px-5 py-2.5 font-semibold rounded-md bg-green-700 hover:bg-green-600 transition-all shadow-lg hover:shadow-xl transform hover:-translate-y-0.5"><i class="fa-solid fa-play mr-2"></i>Resume Operations</button>`;
                document.getElementById('rate-limit-resume-btn').addEventListener('click', handleResumeOperations);
                break;
            default:
                controls.innerHTML = '';
                break;
        }
    }
    async function handleControlAction(endpoint) {
        await fetch(endpoint, { method: 'POST' });
    }
    const handlePauseScan = () => handleControlAction('/pause-scan');
    const handleResumeScan = () => handleControlAction('/resume-scan');
    const handleResumeOperations = () => handleControlAction('/resume-operations');
    async function handleStopScan() {
        clientSideStop = true;
        handleStatusUpdate({status: 'idle', reason: 'Scan cancelled by user.'});
        await handleControlAction('/cancel-scan');
    }
    async function handleFeedActions(e) {
        const sendBtn = e.target.closest('.send-to-slack-btn');
        const generateBtn = e.target.closest('.generate-summary-btn');
        const relatedBtn = e.target.closest('.find-related-btn');
        if (sendBtn) await handleSendToSlack(sendBtn);
        if (generateBtn) await handleGenerateSummary(generateBtn);
        if (relatedBtn) await handleFindRelated(relatedBtn);
    }
    async function handleGenerateSummary(btn) {
        const card = btn.closest('.feed-card');
        const itemData = JSON.parse(card.dataset.itemData);
        const summaryContent = card.querySelector('.summary-content');
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
        try {
            const response = await fetch('/generate-summary', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(itemData)
            });
            if (!response.ok) throw new Error('Failed to generate summary.');
            const result = await response.json();
            summaryContent.innerHTML = `<div class="summary-content markdown-content text-gray-300 text-sm">${marked.parse(result.ai_summary)}</div>`;
            card.classList.remove('summary-pending');
            const updatedItemData = { ...itemData, ai_summary: result.ai_summary };
            card.dataset.itemData = JSON.stringify(updatedItemData);
            updateAllSlackButtons();
        } catch (error) {
            summaryContent.innerHTML = `<p class="text-red-400">Error: ${error.message}</p>`;
            btn.innerHTML = 'Retry';
            btn.disabled = false;
        }
    }
    async function handleSendToSlack(btn) {
        const itemData = JSON.parse(btn.closest('.feed-card').dataset.itemData);
        const webhookUrl = localStorage.getItem('slackWebhookUrl') || '';
        if (!webhookUrl) return;
        btn.disabled = true;
        btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1"></i> Sending...';
        try {
            const response = await fetch('/send-to-slack', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item: itemData, webhookUrl })
            });
            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.error || 'Unknown error');
            }
            btn.innerHTML = '<i class="fa-solid fa-check mr-1"></i> Sent!';
            btn.classList.add('text-green-400');
        } catch (error) {
            btn.innerHTML = '<i class="fa-solid fa-xmark mr-1"></i> Failed';
            btn.classList.add('text-red-400');
            setTimeout(() => {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-brands fa-slack mr-1"></i> Send to Slack';
                btn.classList.remove('text-red-400');
            }, 3000);
        }
    }
   
    async function handleFindRelated(btn) {
        const card = btn.closest('.feed-card');
        const itemData = JSON.parse(card.dataset.itemData);
        const query = `${itemData.title}\n${itemData.ai_summary}`;
       
        ui.relatedModalSourceTitle.textContent = `Sourced from: "${itemData.title}"`;
        ui.relatedModalContent.innerHTML = `<div class="text-center text-gray-400 p-8"><i class="fa-solid fa-spinner fa-spin fa-2x"></i><p class="mt-3">Searching for related items...</p></div>`;
        document.getElementById('related-pagination-controls').innerHTML = '';
        openModal('related');
       
        try {
            const response = await fetch('/hybrid-search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query })
            });
            const results = await response.json();
           
            if (!response.ok) {
                throw new Error(results.error || 'An unknown error occurred.');
            }
           
            const filteredResults = results.filter(result => String(result.id) !== String(itemData.id));

            allRelatedResults = filteredResults;
            currentRelatedPage = 1;

            if (allRelatedResults.length === 0) {
                ui.relatedModalContent.innerHTML = `<div class="text-center text-gray-500 p-8"><i class="fa-solid fa-box-open fa-2x"></i><p class="mt-3">No other related items were found in the database.</p></div>`;
            } else {
                renderRelatedItemsPage(currentRelatedPage);
            }
           
        } catch (error) {
            ui.relatedModalContent.innerHTML = `<div class="text-center text-red-400 p-8"><i class="fa-solid fa-triangle-exclamation fa-2x"></i><p class="mt-3"><strong>Search Failed:</strong> ${error.message}</p></div>`;
        }
    }

    function renderRelatedItemsPage(page) {
        currentRelatedPage = page;
        const contentContainer = ui.relatedModalContent;
        const paginationContainer = document.getElementById('related-pagination-controls');
        contentContainer.innerHTML = '';
        paginationContainer.innerHTML = '';

        if (allRelatedResults.length === 0) return;

        const start = (page - 1) * relatedItemsPerPage;
        const end = start + relatedItemsPerPage;
        const paginatedItems = allRelatedResults.slice(start, end);

        paginatedItems.forEach(item => {
            const relatedCard = renderRelatedItemCard(item);
            contentContainer.appendChild(relatedCard);
        });

        const totalPages = Math.ceil(allRelatedResults.length / relatedItemsPerPage);
        if (totalPages <= 1) return;

        const prevButton = document.createElement('button');
        prevButton.innerHTML = `<i class="fa-solid fa-arrow-left mr-2"></i> Previous`;
        prevButton.className = 'px-3 py-1 text-sm font-semibold rounded-md bg-gray-700 hover:bg-gray-600 transition-all disabled:opacity-50 disabled:cursor-not-allowed';
        prevButton.disabled = page === 1;
        prevButton.onclick = () => renderRelatedItemsPage(page - 1);

        const pageIndicator = document.createElement('span');
        pageIndicator.textContent = `Page ${page} of ${totalPages}`;
        pageIndicator.className = 'text-sm text-gray-400';

        const nextButton = document.createElement('button');
        nextButton.innerHTML = `Next <i class="fa-solid fa-arrow-right ml-2"></i>`;
        nextButton.className = 'px-3 py-1 text-sm font-semibold rounded-md bg-gray-700 hover:bg-gray-600 transition-all disabled:opacity-50 disabled:cursor-not-allowed';
        nextButton.disabled = page === totalPages;
        nextButton.onclick = () => renderRelatedItemsPage(page + 1);

        paginationContainer.appendChild(prevButton);
        paginationContainer.appendChild(pageIndicator);
        paginationContainer.appendChild(nextButton);
    }

    function renderRelatedItemCard(item) {
        const card = document.createElement('div');
        card.className = 'flex items-start p-3 bg-gray-900/50 border brand-border rounded-lg gap-4';
       
        card.innerHTML = `
            <div class="text-center shrink-0 w-20">
                <div class="font-bold text-xl text-indigo-300">${item.score.toFixed(3)}</div>
                <div class="text-xs text-gray-500">Relevance Score</div>
            </div>
            <div class="flex-1 overflow-hidden">
                <a href="${item.url}" target="_blank" class="font-semibold text-white hover:text-green-400 transition-colors truncate block" title="${item.title}">${item.title}</a>
                <p class="text-xs text-indigo-300 mt-1 mb-2">${item.source_name}</p>
                <p class="text-sm text-gray-400 summary-snippet">${item.ai_summary}</p>
            </div>
        `;
        return card;
    }

    function resetPreviewer() {
        previewData = null;
        selectedJsonPath = null;
        document.body.classList.remove('path-selected');
        ui.sourcePreviewContainer.classList.add('hidden');
        ui.previewContent.classList.add('hidden');
        ui.previewStatus.classList.remove('hidden');
        ui.previewStatus.innerHTML = 'Click "Preview" to load sample data from your API.';
        ui.selectedPathDisplay.textContent = '';
    }

    async function handleFetchPreview() {
        const apiUrl = ui.sourceApiUrlInput.value.trim();
        if (!apiUrl) {
            alert("Please enter an API URL first.");
            return;
        }
        resetPreviewer();
        ui.sourcePreviewContainer.classList.remove('hidden');
        ui.previewStatus.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i>Fetching data...';
        ui.fetchPreviewBtn.disabled = true;

        try {
            const response = await fetch('/preview-api-source', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ apiUrl })
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Unknown error fetching preview.');
            }
            previewData = data;
            ui.previewStatus.classList.add('hidden');
            ui.previewContent.classList.remove('hidden');
            ui.rawJsonPreview.textContent = JSON.stringify(previewData, null, 2);
            updateInteractivePreview();
        } catch (e) {
            ui.previewStatus.innerHTML = `<span class="text-red-400"><i class="fa-solid fa-triangle-exclamation mr-2"></i>Error: ${e.message}</span>`;
        } finally {
            ui.fetchPreviewBtn.disabled = false;
        }
    }

    function getNestedValue(obj, path) {
        if (!path) return obj;
        return path.split('.').reduce((acc, part) => acc && acc[part], obj);
    }

    function updateInteractivePreview() {
        if (!previewData) return;
        const dataRoot = ui.sourceDataRootInput.value.trim();
        const items = getNestedValue(previewData, dataRoot);
       
        if (Array.isArray(items) && items.length > 0) {
            renderInteractiveJson(items[0], ui.interactivePreviewItem, dataRoot ? `${dataRoot}.0` : '0');
            ui.sourceDataRootInput.classList.remove('border-red-500');
            ui.sourceDataRootInput.classList.add('border-green-500');
        } else {
            ui.interactivePreviewItem.innerHTML = `<span class="text-yellow-400">Could not find an array of items at data root '${dataRoot}'. Displaying the full response.</span>`;
            renderInteractiveJson(previewData, ui.interactivePreviewItem, '');
            if (dataRoot) {
                ui.sourceDataRootInput.classList.add('border-red-500');
                ui.sourceDataRootInput.classList.remove('border-green-500');
            }
        }
    }

    function renderInteractiveJson(obj, container, pathPrefix = '') {
        container.innerHTML = '';
       
        const getRelativePath = (fullPath) => {
            const dataRoot = ui.sourceDataRootInput.value.trim();
            let relativePath = fullPath;
            if (dataRoot) {
                const prefix = dataRoot + '.0.';
                if (fullPath.startsWith(prefix)) {
                    relativePath = fullPath.substring(prefix.length);
                }
            } else if (fullPath.startsWith('0.')) {
                    relativePath = fullPath.substring(2);
            }
            return relativePath;
        };

        const createNode = (key, value, path) => {
            const isObject = typeof value === 'object' && value !== null;
            const entry = document.createElement('div');
            const pathParts = pathPrefix ? path.replace(pathPrefix, '').split('.') : path.split('.');
            const paddingDepth = Math.max(0, pathParts.length - 1);
            entry.style.paddingLeft = `${paddingDepth}rem`;
           
            const keySpan = `<span class="json-key">"${key}": </span>`;
           
            if (isObject) {
                entry.innerHTML = keySpan + (Array.isArray(value) ? '[' : '{');
                container.appendChild(entry);
                Object.entries(value).forEach(([k, v]) => createNode(k, v, path ? `${path}.${k}` : k));
                const closingEntry = document.createElement('div');
                closingEntry.style.paddingLeft = entry.style.paddingLeft;
                closingEntry.innerHTML = Array.isArray(value) ? ']' : '}';
                container.appendChild(closingEntry);
            } else {
                const relativePath = getRelativePath(path);
                const isChecked = currentFieldsToCheck.includes(relativePath);
                const type = typeof value === 'string' ? 'string' : typeof value === 'number' ? 'number' : 'boolean';
                const displayValue = typeof value === 'string' ? `"${value}"` : value;
                entry.innerHTML = `
                    <label class="json-entry-label">
                        <input type="checkbox" class="json-checkbox" data-path="${relativePath}" ${isChecked ? 'checked' : ''}>
                        ${keySpan}<span class="json-value json-${type}" data-path="${path}">${displayValue}</span>
                    </label>
                `;
                container.appendChild(entry);
            }
        };
        Object.entries(obj).forEach(([key, value]) => createNode(key, value, pathPrefix ? `${pathPrefix}.${key}` : key));
    }

    function handleTabSwitch(target) {
        // Handle view tabs (Scanner vs Dashboard)
        if (target.classList.contains('view-tab-btn')) {
             document.querySelectorAll('.view-content').forEach(v => v.classList.add('hidden'));
             document.getElementById(target.dataset.view).classList.remove('hidden');

             document.querySelectorAll('.view-tab-btn').forEach(b => b.classList.remove('active-view-tab'));
             target.classList.add('active-view-tab');

             if (target.dataset.view === 'dashboard-view') {
                 fetchAndRenderDashboard(currentAnalyticsDate);
             }
        // Handle scan source tabs in the feed
        } else if (target.classList.contains('scan-tab')) {
            document.querySelectorAll('.scan-tab').forEach(btn => btn.classList.remove('active-scan-tab'));
            target.classList.add('active-scan-tab');
            document.querySelectorAll('.feed-pane').forEach(content => content.classList.add('hidden'));
            const pane = document.getElementById(`feed-pane-${target.dataset.source}`);
            if (pane) pane.classList.remove('hidden');
        // Handle tabs in the source modal (Previewer)
        } else if (target.classList.contains('preview-tab-btn')) {
             document.querySelectorAll('.preview-tab-btn').forEach(btn => btn.classList.remove('active-tab'));
             target.classList.add('active-tab');
             document.querySelectorAll('.preview-tab-content').forEach(content => content.classList.add('hidden'));
             document.getElementById(`preview-${target.dataset.tab}-tab`).classList.remove('hidden');
        }
    }

    function handleJsonItemClick(item) {
        document.querySelectorAll('.selected-json-path').forEach(el => el.classList.remove('selected-json-path'));
        item.classList.add('selected-json-path');
        const fullPath = item.dataset.path;
        const dataRoot = ui.sourceDataRootInput.value.trim();
        let relativePath = fullPath;
        if (dataRoot) {
            const prefix = dataRoot + '.0.';
            if (fullPath.startsWith(prefix)) {
                relativePath = fullPath.substring(prefix.length);
            }
        } else if (fullPath.startsWith('0.')) {
            relativePath = fullPath.substring(2);
        }
        selectedJsonPath = relativePath;
        ui.selectedPathDisplay.textContent = `Selected: ${selectedJsonPath}`;
        document.body.classList.add('path-selected');
    }

    function renderMappingInputs() {
        ui.mappingInputsContainer.innerHTML = '';
        requiredMappings.forEach(key => {
            const div = document.createElement('div');
            div.className = 'flex items-center gap-2';
            div.innerHTML = `
                <label class="w-16 text-sm text-gray-400 font-mono shrink-0">${key}:</label>
                <input type="text" readonly data-key="${key}" placeholder="<not mapped>" class="flex-grow p-2 rounded-md bg-gray-900 border brand-border focus:outline-none text-sm font-mono text-gray-300 transition-all duration-300">
                <button type="button" data-key="${key}" title="Map selected path to '${key}'" class="mapping-target-btn px-3 py-2 text-lg rounded-md bg-gray-700 hover:bg-blue-700 text-blue-400 hover:text-white transition-all">
                    <i class="fa-solid fa-crosshairs pointer-events-none"></i>
                </button>
            `;
            ui.mappingInputsContainer.appendChild(div);
        });
    }
   
    function syncMappingsToTextarea() {
        const mappings = {};
        ui.mappingInputsContainer.querySelectorAll('input[data-key]').forEach(input => {
            mappings[input.dataset.key] = input.value.trim();
        });
        ui.sourceFieldMappingsTextarea.value = JSON.stringify(mappings, null, 2);
    }
   
    function populateMappingsFromTextarea() {
        try {
            const mappings = JSON.parse(ui.sourceFieldMappingsTextarea.value);
            Object.entries(mappings).forEach(([key, value]) => {
                const input = ui.mappingInputsContainer.querySelector(`input[data-key="${key}"]`);
                if (input) {
                    input.value = value;
                }
            });
        } catch (e) {
            console.warn("Could not parse initial field mappings.", e);
        }
    }
   
    function handleMappingTargetClick(target) {
        if (!selectedJsonPath) {
            ui.selectedPathDisplay.textContent = 'Select a value from the preview first!';
            setTimeout(() => { ui.selectedPathDisplay.textContent = selectedJsonPath || '' }, 2000);
            return;
        }
        const key = target.dataset.key;
        const input = ui.mappingInputsContainer.querySelector(`input[data-key="${key}"]`);
        if (input) {
            input.value = selectedJsonPath;
            syncMappingsToTextarea();
           
            ui.selectedPathDisplay.textContent = `Mapped '${key}'!`;
            document.querySelectorAll('.selected-json-path').forEach(el => el.classList.remove('selected-json-path'));
            document.body.classList.remove('path-selected');
           
            input.classList.add('bg-green-900/50', 'border-green-500');
            setTimeout(() => {
                input.classList.remove('bg-green-900/50', 'border-green-500');
                ui.selectedPathDisplay.textContent = '';
            }, 1500);

            selectedJsonPath = null;
        }
    }

    function renderFieldsToCheck() {
        ui.fieldsToCheckContainer.innerHTML = '';
        if (currentFieldsToCheck.length === 0) {
            ui.fieldsToCheckContainer.innerHTML = '<span class="text-xs text-gray-500 p-1">Use the previewer to select fields to check.</span>';
        } else {
            currentFieldsToCheck.forEach(field => {
                const pill = document.createElement('div');
                pill.className = 'flex items-center gap-2 bg-gray-900/70 border brand-border rounded-full px-3 py-1 text-sm font-mono';
                pill.innerHTML = `
                    <span>${field}</span>
                    <button type="button" class="remove-field-btn text-gray-500 hover:text-red-400" data-field="${field}" title="Remove field">
                        <i class="fa-solid fa-times-circle"></i>
                    </button>
                `;
                ui.fieldsToCheckContainer.appendChild(pill);
            });
        }
        syncFieldsToCheckToTextarea();
    }

    function syncFieldsToCheckToTextarea() {
        ui.sourceFieldsToCheckTextarea.value = currentFieldsToCheck.join('\n');
    }

    function handleViewSwitch(viewId) {
  document.querySelectorAll('.view-content').forEach(v => v.classList.add('hidden'));
  document.getElementById(viewId).classList.remove('hidden');

  document.querySelectorAll('.view-tab-btn').forEach(b => b.classList.remove('active-view-tab'));
  document.querySelector(`.view-tab-btn[data-view="${viewId}"]`).classList.add('active-view-tab');

  if (viewId === 'dashboard-view') {
   fetchAndRenderDashboard(currentAnalyticsDate);
  } else if (viewId === 'matches-view') {
        // --- FIX START ---
        // If the view hasn't been set up yet (event listeners, etc.), initialize it.
        // The initialize function already performs the first data fetch.
    if (!matchesState.isInitialized) {
      initializeMatchesView();
    } else {
            // For every subsequent visit to the tab, force a fresh pull from the database.
            // The `true` argument resets pagination and clears the current list.
            fetchMatches(true);
        }
        // --- FIX END ---
  }
 }
   
    function handleClearFeed() {
        tearDownTabs();
        // FIX: The original code replaced the container's content with a new div, breaking the structure.
        // The correct approach is to simply clear the inner HTML, leaving the container itself intact.
        ui.feedPanesContainer.innerHTML = '';
        ui.placeholder.classList.remove('hidden');
        ui.feedControls.classList.add('hidden');
        
        // Reset filters
        activeFilters = { sources: new Set(), labels: new Set() };
        availableFilters = { sources: new Set(), labels: new Set() };
        ui.feedSearchInput.value = '';
        currentSort = 'newest';
        renderFilterOptions();
        updateFilterBadge();
    }

    // --- NEW / REFACTORED: Filter and Sort Logic ---
    const applyFiltersAndSort = debounce(() => {
        const searchTerm = ui.feedSearchInput.value.toLowerCase();
   
        // 1. First pass: filter all cards across all panes by setting their display property
        document.querySelectorAll('.feed-card').forEach(card => {
            const itemData = JSON.parse(card.dataset.itemData);
            let isVisible = true;
       
            // Search filter
            if (searchTerm) {
                const searchText = [itemData.title, itemData.by, itemData.ai_summary].join(' ').toLowerCase();
                if (!searchText.includes(searchTerm)) {
                    isVisible = false;
                }
            }
       
            // Source tag filter
            if (isVisible && activeFilters.sources.size > 0 && !activeFilters.sources.has(itemData.source_name)) {
                isVisible = false;
            }
       
            // Label tag filter
            if (isVisible && activeFilters.labels.size > 0 && !activeFilters.labels.has(itemData.matched_label)) {
                isVisible = false;
            }
       
            card.style.display = isVisible ? '' : 'none';
        });
   
        // 2. Second pass: sort ALL cards within each pane (hidden and visible)
        document.querySelectorAll('.feed-pane').forEach(pane => {
            const allCardsInPane = Array.from(pane.querySelectorAll('.feed-card'));
       
            allCardsInPane.sort((a, b) => {
                const timeA = JSON.parse(a.dataset.itemData).time || 0;
                const timeB = JSON.parse(b.dataset.itemData).time || 0;
                return currentSort === 'newest' ? timeB - timeA : timeA - timeB;
            });
       
            // 3. Re-append ALL sorted cards to the DOM. Their visibility was already set.
            allCardsInPane.forEach(card => pane.appendChild(card));
        });
    }, 200);


    // --- START: NEW Matches Database View Logic ---
    function initializeMatchesView() {
        if (!isMongoDbEnabled) {
            ui.matchesPlaceholder.innerHTML = `
                <i class="fas fa-database fa-3x text-red-500"></i>
                <p class="mt-4 text-lg">Database Not Connected</p>
                <p class="text-sm">The Matches Database requires a connection to MongoDB. Please configure it in your backend.</p>
            `;
            return;
        }
        
        matchesState.isInitialized = true;
        renderMatchesSourceFilters();
        setupMatchesViewEventListeners();
        setupMatchesInfiniteScroll();
        fetchMatches(true); // Initial fetch
    }

    const fetchMatches = debounce(async (isReset = false) => {
     if (matchesState.isLoading) return;

     if (isReset) {
       matchesState.currentPage = 1;
       matchesState.totalPages = 1;
       ui.matchesResultsContainer.innerHTML = '';
     }

     if (matchesState.currentPage > matchesState.totalPages) {
       return; // No more pages
     }

     matchesState.isLoading = true;
        // --- FIX IS HERE ---
     // Only show the main placeholder spinner on a full reset/new search.
     if (isReset) {
       ui.matchesPlaceholder.innerHTML = `<i class="fa-solid fa-spinner fa-spin fa-2x"></i>`;
     }

     const params = new URLSearchParams({
       page: matchesState.currentPage,
       per_page: 20,
       sort_order: matchesState.sortOrder,
       sort_by: 'time',
       query: matchesState.query
     });
     matchesState.sources.forEach(source => params.append('source_name', source));

     try {
       const response = await fetch(`/matches?${params.toString()}`);
       if (!response.ok) throw new Error(`Server responded with status ${response.status}`);
      
       const { data, pagination } = await response.json();
       matchesState.totalPages = pagination.total_pages;

       if (pagination.total_items === 0) {
         ui.matchesPlaceholder.innerHTML = `
           <i class="fas fa-box-open fa-3x"></i>
           <p class="mt-4 text-lg">No Matches Found</p>
           <p class="text-sm">Try adjusting your search or filter criteria.</p>`;
       } else if (isReset) {
         ui.matchesPlaceholder.innerHTML = '';
       }

       renderMatches(data);
       matchesState.currentPage++;

     } catch (error) {
       console.error("Failed to fetch matches:", error);
       ui.matchesPlaceholder.innerHTML = `
         <i class="fas fa-triangle-exclamation fa-3x text-red-500"></i>
         <p class="mt-4 text-lg">Error Loading Matches</p>
         <p class="text-sm">${error.message}</p>`;
     } finally {
       matchesState.isLoading = false;
     }
   }, 300);

    function renderMatches(matches) {

        matches.forEach(item => {
            // The item from /matches won't have a matched_label, so createFeedCard will show 'DB'
            const card = createFeedCard(item);
            ui.matchesResultsContainer.appendChild(card);
        });

        if (matchesState.currentPage <= matchesState.totalPages) {
            const newLoader = document.createElement('div');
            newLoader.id = 'matches-loader';
            newLoader.className = 'text-center py-8';
            newLoader.innerHTML = `<i class="fa-solid fa-spinner fa-spin fa-2x"></i>`;
            ui.matchesResultsContainer.appendChild(newLoader);
        }

        const loader = document.getElementById('matches-loader');
        if (loader) loader.remove();
    }

    function renderMatchesSourceFilters() {
        ui.matchesSourceFiltersContainer.innerHTML = '';
        if (apiSources.length === 0) {
            ui.matchesSourceFiltersContainer.innerHTML = '<p class="text-gray-500 italic text-xs">No sources configured.</p>';
            return;
        }
        apiSources.forEach(source => {
            const container = document.createElement('label');
            container.className = 'flex items-center space-x-2 cursor-pointer hover:bg-gray-800 p-1 rounded';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'matches-filter-checkbox form-checkbox';
            checkbox.dataset.value = source.name;
            checkbox.checked = matchesState.sources.has(source.name);
            const text = document.createElement('span');
            text.textContent = source.name;
            text.className = 'truncate';
            container.append(checkbox, text);
            ui.matchesSourceFiltersContainer.appendChild(container);
        });
    }

    function updateMatchesFilterBadge() {
        const count = matchesState.sources.size;
        if (count > 0) {
            ui.matchesFilterCountBadge.textContent = count;
            ui.matchesFilterCountBadge.classList.remove('hidden');
        } else {
            ui.matchesFilterCountBadge.classList.add('hidden');
        }
    }

    function setupMatchesInfiniteScroll() {
        const options = {
            root: ui.matchesResultsContainer.parentElement,
            rootMargin: '0px',
            threshold: 1.0
        };

        matchesScrollObserver = new IntersectionObserver((entries) => {
            if (entries[0].isIntersecting && !matchesState.isLoading) {
                fetchMatches();
            }
        }, options);

        const loader = document.getElementById('matches-loader');
        if (loader) matchesScrollObserver.observe(loader);

        // Re-observe whenever content changes
        const config = { childList: true };
        const observerCallback = (mutationsList) => {
            for (const mutation of mutationsList) {
                if (mutation.type === 'childList') {
                    const newLoader = document.getElementById('matches-loader');
                    if (newLoader) {
                        matchesScrollObserver.observe(newLoader);
                    }
                }
            }
        };
        const mutationObserver = new MutationObserver(observerCallback);
        mutationObserver.observe(ui.matchesResultsContainer, config);
    }

    function setupMatchesViewEventListeners() {
        ui.matchesSearchInput.addEventListener('input', () => {
            matchesState.query = ui.matchesSearchInput.value;
            fetchMatches(true);
        });

        ui.matchesSortSelect.addEventListener('change', () => {
            matchesState.sortOrder = ui.matchesSortSelect.value;
            fetchMatches(true);
        });

        ui.matchesFilterBtn.addEventListener('click', () => {
            ui.matchesFilterPopover.classList.toggle('hidden');
        });

        ui.matchesClearFiltersBtn.addEventListener('click', () => {
            matchesState.sources.clear();
            renderMatchesSourceFilters();
            updateMatchesFilterBadge();
            fetchMatches(true);
        });
        
        ui.matchesFilterPopover.addEventListener('change', (e) => {
            if (e.target.classList.contains('matches-filter-checkbox')) {
                const { value } = e.target.dataset;
                if (e.target.checked) {
                    matchesState.sources.add(value);
                } else {
                    matchesState.sources.delete(value);
                }
                updateMatchesFilterBadge();
                fetchMatches(true);
            }
        });
        
        // Hide popover on outside click
        document.addEventListener('click', (e) => {
             if (ui.matchesFilterPopover && !ui.matchesFilterBtn.contains(e.target) && !ui.matchesFilterPopover.contains(e.target)) {
                ui.matchesFilterPopover.classList.add('hidden');
            }
        });
    }
    // --- END: NEW Matches Database View Logic ---

    function renderFilterOptions() {
        const createFilterCheckbox = (type, value) => {
            const container = document.createElement('label');
            container.className = 'flex items-center space-x-2 cursor-pointer hover:bg-gray-800 p-1 rounded';
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.className = 'filter-checkbox form-checkbox';
            checkbox.dataset.type = type;
            checkbox.dataset.value = value;
            checkbox.checked = activeFilters[type].has(value);
            const text = document.createElement('span');
            text.textContent = value;
            text.className = 'truncate';
            text.title = value;
            container.append(checkbox, text);
            return container;
        };

        // Render Source Filters
        if (availableFilters.sources.size === 0) {
            ui.sourceFiltersContainer.innerHTML = '<p class="text-gray-500 italic text-xs">No sources in feed yet.</p>';
        } else {
            ui.sourceFiltersContainer.innerHTML = '';
            [...availableFilters.sources].sort().forEach(source => {
                ui.sourceFiltersContainer.appendChild(createFilterCheckbox('sources', source));
            });
        }
       
        // Render Label Filters
        if (availableFilters.labels.size === 0) {
            ui.labelFiltersContainer.innerHTML = '<p class="text-gray-500 italic text-xs">No labels in feed yet.</p>';
        } else {
            ui.labelFiltersContainer.innerHTML = '';
            [...availableFilters.labels].sort().forEach(label => {
                ui.labelFiltersContainer.appendChild(createFilterCheckbox('labels', label));
            });
        }
    }
   
    function updateFilterBadge() {
        const count = activeFilters.sources.size + activeFilters.labels.size;
        if (count > 0) {
            ui.filterCountBadge.textContent = count;
            ui.filterCountBadge.classList.remove('hidden');
        } else {
            ui.filterCountBadge.classList.add('hidden');
        }
    }
   
    function setupFilterAndSortControls() {
        ui.feedSearchInput.addEventListener('input', applyFiltersAndSort);
        ui.filterBtn.addEventListener('click', () => {
            ui.filterPopover.classList.toggle('hidden');
        });
        ui.clearFiltersBtn.addEventListener('click', () => {
            activeFilters = { sources: new Set(), labels: new Set() };
            renderFilterOptions();
            updateFilterBadge();
            applyFiltersAndSort();
        });
        ui.filterPopover.addEventListener('change', (e) => {
            if (e.target.classList.contains('filter-checkbox')) {
                const { type, value } = e.target.dataset;
                if (e.target.checked) {
                    activeFilters[type].add(value);
                } else {
                    activeFilters[type].delete(value);
                }
                updateFilterBadge();
                applyFiltersAndSort();
            }
        });
    }

    function getDefaultStatsObject(dateStr) {
        return {
            '_id': dateStr,
            'date': dateStr,
            'totalScansStarted': 0,
            'totalItemsMatched': 0,
            'totalSummariesGenerated': 0,
            'scansBySource': {},
            'matchesByLabel': {},
            'matchesBySourceLabel': {},
            'hourlyActivity': Object.fromEntries(Array.from({ length: 24 }, (_, i) => [i.toString(), 0]))
        };
    }

    function getStatsFromLocalStorage(dateStr) {
        const storedStats = localStorage.getItem(`analytics_${dateStr}`);
        return storedStats ? JSON.parse(storedStats) : getDefaultStatsObject(dateStr);
    }

    function updateStatsWithEvent(stats, eventType, details) {
        const newStats = JSON.parse(JSON.stringify(stats));
        const now = new Date();
        const hourStr = now.getUTCHours().toString();

        const sanitizeKey = (key) => String(key).replace(/\./g, '_').replace(/\$/g, '_');

        if (eventType === 'scan_started') {
            const sourceName = details.sourceName || 'Unknown';
            const sourceNameSafe = sanitizeKey(sourceName);
            newStats.totalScansStarted += 1;
            newStats.scansBySource[sourceNameSafe] = (newStats.scansBySource[sourceNameSafe] || 0) + 1;
        } else if (eventType === 'item_matched') {
            const sourceName = details.sourceName || 'Unknown';
            const label = details.matchedLabel || 'Unknown';
            const sourceNameSafe = sanitizeKey(sourceName);
            const labelSafe = sanitizeKey(label);

            newStats.totalItemsMatched += 1;
            newStats.hourlyActivity[hourStr] = (newStats.hourlyActivity[hourStr] || 0) + 1;
            newStats.matchesByLabel[labelSafe] = (newStats.matchesByLabel[labelSafe] || 0) + 1;
            if (!newStats.matchesBySourceLabel[sourceNameSafe]) {
                newStats.matchesBySourceLabel[sourceNameSafe] = {};
            }
            newStats.matchesBySourceLabel[sourceNameSafe][labelSafe] = (newStats.matchesBySourceLabel[sourceNameSafe][labelSafe] || 0) + 1;
        } else if (eventType === 'summary_generated' && details.success) {
            newStats.totalSummariesGenerated += 1;
        }
        return newStats;
    }

    function handleLocalAnalyticsUpdate(data) {
        const dateStr = new Date().toISOString().split('T')[0];
        const currentStats = getStatsFromLocalStorage(dateStr);
        const updatedStats = updateStatsWithEvent(currentStats, data.eventType, data.details);
        localStorage.setItem(`analytics_${dateStr}`, JSON.stringify(updatedStats));

        const isDashboardActive = !ui.dashboardView.classList.contains('hidden');
        if (isDashboardActive && currentAnalyticsDate === dateStr) {
            renderDashboard(updatedStats);
        }
    }

    function initDashboard() {
        flatpickr(ui.analyticsDatePicker, {
            dateFormat: "Y-m-d",
            defaultDate: "today",
            theme: "dark",
            onChange: function(selectedDates, dateStr, instance) {
                currentAnalyticsDate = dateStr;
                fetchAndRenderDashboard(dateStr);
            },
        });
        fetchAndRenderDashboard(currentAnalyticsDate);
    }

    function handleClearLocalDashboard() {
        if (confirm(`Are you sure you want to clear all locally stored analytics for ${currentAnalyticsDate}?`)) {
            localStorage.removeItem(`analytics_${currentAnalyticsDate}`);
            fetchAndRenderDashboard(currentAnalyticsDate);
        }
    }

    function renderDashboard(stats) {
        renderKpiCards(stats);
        renderHourlyChart(stats.hourlyActivity);
        renderLabelChart(stats.matchesByLabel);
       
        const sourceLabelData = [];
        for (const [source, labels] of Object.entries(stats.matchesBySourceLabel || {})) {
            for (const [label, count] of Object.entries(labels)) {
                sourceLabelData.push({ source: source.replace(/_/g,'.'), label: label.replace(/_/g,'.'), count });
            }
        }
       
        renderInteractiveTable(
            'source-label-table', 
            'source-label-table-search',
            sourceLabelData, 
            [
                { key: 'source', title: 'Source' },
                { key: 'label', title: 'Label' },
                { key: 'count', title: 'Count' }
            ]
        );
    }

    async function fetchAndRenderDashboard(dateStr) {
        try {
            const response = await fetch(`/analytics/daily-stats?date=${dateStr}`);
            if (!response.ok) throw new Error('Failed to load stats');
            const data = await response.json();

            if (data.use_local_storage) {
                isMongoDbEnabled = false;
                ui.localStorageControls.classList.remove('hidden');
                console.info("MongoDB is disabled. Using localStorage for analytics.");
                const localStats = getStatsFromLocalStorage(dateStr);
                renderDashboard(localStats);
            } else {
                isMongoDbEnabled = true;
                ui.localStorageControls.classList.add('hidden');
                renderDashboard(data);
            }

        } catch (error) {
            console.error("Error updating dashboard:", error);
            ui.localStorageControls.classList.remove('hidden');
            const localStats = getStatsFromLocalStorage(dateStr);
            renderDashboard(localStats);
        }
    }

    function renderKpiCards(stats) {
        ui.kpiScansStarted.textContent = stats.totalScansStarted || 0;
        ui.kpiItemsMatched.textContent = stats.totalItemsMatched || 0;
        ui.kpiSummariesGenerated.textContent = stats.totalSummariesGenerated || 0;
    }
   
    function renderHourlyChart(hourlyData) {
        const chartContainer = document.getElementById('hourly-activity-chart-container');
        if (dashboardCharts.hourly) dashboardCharts.hourly.destroy();
        chartContainer.innerHTML = ''; 

        const canvas = document.createElement('canvas');
        chartContainer.appendChild(canvas);

        const labels = Array.from({ length: 24 }, (_, i) => i.toString().padStart(2, '0') + ":00");
        const data = labels.map((_, i) => (hourlyData || {})[i.toString()] || 0);

        dashboardCharts.hourly = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Matches per Hour',
                    data: data,
                    backgroundColor: 'rgba(0, 237, 100, 0.5)',
                    borderColor: 'rgba(0, 237, 100, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: {
                    y: { beginAtZero: true, ticks: { color: '#9ca3af' }, grid: { color: '#4A5568' } },
                    x: { ticks: { color: '#9ca3af' }, grid: { color: 'transparent' } }
                },
                plugins: { legend: { display: false } }
            }
        });
    }
   
    function renderLabelChart(labelData) {
        const chartContainer = document.getElementById('matches-by-label-chart-container');
        if (dashboardCharts.labels) dashboardCharts.labels.destroy();
        chartContainer.innerHTML = '';

        if (!labelData || Object.keys(labelData).length === 0) {
            chartContainer.innerHTML = '<p class="text-gray-500 text-center self-center">No data for this day.</p>';
            return;
        }

        const canvas = document.createElement('canvas');
        chartContainer.appendChild(canvas);
       
        const labels = Object.keys(labelData).map(l => l.replace(/_/g, '.'));
        const data = Object.values(labelData);
        const colors = ['#00ED64', '#3b82f6', '#f59e0b', '#ef4444', '#818cf8', '#ec4899', '#14b8a6'];

        dashboardCharts.labels = new Chart(canvas, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: data,
                    backgroundColor: colors,
                    borderColor: '#212934',
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { position: 'bottom', labels: { color: '#9ca3af' } } }
            }
        });
    }

    function renderInteractiveTable(tableId, searchInputId, data, columns) {
        const table = document.getElementById(tableId);
        const searchInput = document.getElementById(searchInputId);
        if (!table) return;

        const render = () => {
            const searchTerm = searchInput.value.toLowerCase();
            let filteredData = [...data];

            if (searchTerm) {
                filteredData = filteredData.filter(row => 
                    columns.some(col => String(row[col.key]).toLowerCase().includes(searchTerm))
                );
            }
           
            const sort = tableSortState[tableId] || {};
            if (sort.key) {
                filteredData.sort((a, b) => {
                    const valA = a[sort.key];
                    const valB = b[sort.key];
                    const order = sort.dir === 'asc' ? 1 : -1;
                    if (typeof valA === 'number' && typeof valB === 'number') return (valA - valB) * order;
                    return String(valA).localeCompare(String(valB)) * order;
                });
            }

            table.innerHTML = `<thead><tr>${columns.map(c => {
                const sortIcon = sort.key === c.key 
                    ? (sort.dir === 'asc' ? '<i class="fa-solid fa-sort-up ml-2"></i>' : '<i class="fa-solid fa-sort-down ml-2"></i>')
                    : '<i class="fa-solid fa-sort text-gray-600 ml-2"></i>';
                return `<th class="sortable" data-key="${c.key}">${c.title} ${sortIcon}</th>`;
            }).join('')}</tr></thead>`;

            const tbody = document.createElement('tbody');
            if (filteredData.length === 0) {
                tbody.innerHTML = `<tr><td colspan="${columns.length}" class="text-center text-gray-500 py-4">No matching data</td></tr>`;
            } else {
                filteredData.forEach(row => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = columns.map(col => `<td>${row[col.key]}</td>`).join('');
                    tbody.appendChild(tr);
                });
            }
            table.appendChild(tbody);
        };

        table.addEventListener('click', e => {
            const header = e.target.closest('th.sortable');
            if (header) {
                const key = header.dataset.key;
                const currentSort = tableSortState[tableId] || {};
                const dir = (currentSort.key === key && currentSort.dir === 'asc') ? 'desc' : 'asc';
                tableSortState[tableId] = { key, dir };
                render();
            }
        });

        searchInput.addEventListener('keyup', debounce(render, 200));
        render();
    }
   
    // --- Stream Connection ---
    function connectToStream() {
        if (eventSource) return;
        eventSource = new EventSource('/stream');
        let cardAnimationDelay = 0;
        eventSource.onmessage = (event) => {
            if (!isLeader) {
                shutdownApp();
                return;
            }
            const data = JSON.parse(event.data);
           
            const isDashboardActive = !ui.dashboardView.classList.contains('hidden');
            const today = new Date().toISOString().split('T')[0];
            const shouldRefreshDashboard = isDashboardActive && currentAnalyticsDate === today;
           
            if (isMongoDbEnabled && shouldRefreshDashboard && (data.type === 'api_item' || data.type === 'summary_update' || (data.type === 'status' && data.status === 'scanning'))) {
                fetchAndRenderDashboard(currentAnalyticsDate);
            }

            switch(data.type) {
                case 'local_analytics_update':
                    handleLocalAnalyticsUpdate(data);
                    break;
                case 'status':
                    if (clientSideStop && data.status !== 'idle' && data.status !== 'error') return; 
                    if (data.status === 'idle' || data.status === 'error') clientSideStop = false;
                   
                    handleStatusUpdate(data);
                    if (data.status !== 'scanning') cardAnimationDelay = 0;
                    break;
                case 'api_item':
                    ui.placeholder.classList.add('hidden');
                    ui.feedControls.classList.remove('hidden');

                    let needsFilterRender = false;
                    if (!availableFilters.sources.has(data.source_name)) {
                        availableFilters.sources.add(data.source_name);
                        needsFilterRender = true;
                    }
                    if (!availableFilters.labels.has(data.matched_label)) {
                        availableFilters.labels.add(data.matched_label);
                        needsFilterRender = true;
                    }
                    if (needsFilterRender) renderFilterOptions();

                    if (!document.querySelector(`[data-item-id="${data.id}"]`)) {
                        const card = createFeedCard(data, cardAnimationDelay);
                       
                        // Check visibility against current filters before appending
                        let isVisible = true;
                        const searchTerm = ui.feedSearchInput.value.toLowerCase();
                        if (searchTerm) {
                            const searchText = [data.title, data.by, data.ai_summary].join(' ').toLowerCase();
                            if (!searchText.includes(searchTerm)) isVisible = false;
                        }
                        if (activeFilters.sources.size > 0 && !activeFilters.sources.has(data.source_name)) isVisible = false;
                        if (activeFilters.labels.size > 0 && !activeFilters.labels.has(data.matched_label)) isVisible = false;
                        card.style.display = isVisible ? '' : 'none';

                        const container = isGlobalScanActive 
                            ? document.getElementById(`feed-pane-${data.source_name}`)
                            : ui.feedPanesContainer;
                        if(container) {
                             if (currentSort === 'newest') {
                                 container.insertBefore(card, container.firstChild);
                             } else {
                                 container.appendChild(card);
                             }
                        }
                        cardAnimationDelay += 100;
                    }
                    break;
                case 'summary_update':
                    const card = document.querySelector(`[data-item-id="${data.id}"]`);
                    if (card) {
                        const summaryContent = card.querySelector('.summary-content');
                        summaryContent.innerHTML = `<div class="markdown-content text-gray-300 text-sm">${marked.parse(data.ai_summary)}</div>`;
                        card.classList.remove('summary-pending');
                        
                        const relatedBtn = card.querySelector('.find-related-btn');
                        if (relatedBtn) {
                            relatedBtn.disabled = false;
                            relatedBtn.title = 'Find related items';
                        }
                        
                        const currentData = JSON.parse(card.dataset.itemData);
                        card.dataset.itemData = JSON.stringify({ ...currentData, ai_summary: data.ai_summary });
                        updateAllSlackButtons();
                    }
                    break;
            }
        };
        eventSource.onerror = () => {
            handleStatusUpdate({status: 'error', reason: 'Connection to server lost. Reconnecting...'});
            eventSource.close();
            eventSource = null;
            setTimeout(connectToStream, 5000);
        };
    }
    async function startScan(sourceName, startPage = 1) {
        // This function is now only for resuming a specific part of a global scan.
        // It should not tear down the global UI.
        clientSideStop = false;
        await fetch('/scan-source', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_name: sourceName, start_page: startPage })
        });
    }
    function scanAllSources() {
        const activeSources = apiSources.filter(source => source.scan_enabled);

        if (activeSources.length === 0) {
            alert('No sources are enabled for scanning. Please enable at least one source using the toggle switch.');
            return;
        }
        clientSideStop = false;
        isGlobalScanActive = true;
        tearDownTabs(); // Clear previous state before starting a new scan
        ui.scanTabsContainer.classList.remove('hidden');
        ui.placeholder.classList.add('hidden');
        ui.feedControls.classList.remove('hidden');

        activeSources.forEach((source, index) => {
            const tab = document.createElement('button');
            tab.className = 'scan-tab';
            if (index === 0) tab.classList.add('active-scan-tab');
            tab.textContent = source.name;
            tab.dataset.source = source.name;
            ui.scanTabsContainer.appendChild(tab);

            const pane = document.createElement('div');
            pane.className = 'feed-pane space-y-6';
            if (index > 0) pane.classList.add('hidden');
            pane.id = `feed-pane-${source.name}`;
            ui.feedPanesContainer.appendChild(pane);
        });

        // Send only the names of the active sources to the backend
        fetch('/scan-all-sources', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_names: activeSources.map(s => s.name) })
        }).catch(err => {
            console.error("Error starting all scans:", err);
            handleStatusUpdate({status: 'error', reason: 'Failed to start all scans.'});
        });
    }
    
    setupConfigControls(); 
   setupManagementEventListeners(); 
   setupFilterAndSortControls(); 
   initDashboard(); 
   updateGlobalScanControls(); 
   loadConfiguration().then(() => { 
     connectToStream(); 
     updateControlsUI(); 
   }); 
}

// --- Start the leader election process on page load ---
document.addEventListener('DOMContentLoaded', () => {
    leaderCheckInterval = setInterval(checkLeader, 2000);
    checkLeader();
});