// --- Configuration Management ---
// Manages Listeners, API Sources, modals, and the API previewer.

function initConfig(app) {
 const { ui, state, utils } = app;

 let selectedJsonPath = null;
 let previewData = null;
 let currentFieldsToCheck = [];
 const requiredMappings = ["id", "title", "url", "text", "by", "time"];
 let listenerFormValidity = { label: false, pattern: false };

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

 function setupConfigControls() {
  ui.slackWebhookUrlInput.value = localStorage.getItem('slackWebhookUrl') || '';
  ui.saveWebhookBtn.addEventListener('click', () => {
   localStorage.setItem('slackWebhookUrl', ui.slackWebhookUrlInput.value.trim());
   ui.saveWebhookBtn.textContent = 'Saved!';
   setTimeout(() => { ui.saveWebhookBtn.textContent = 'Save'; }, 2000);
   if(app.updateAllSlackButtons) app.updateAllSlackButtons();
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
  if (!patternsLoaded) await fetchData('/patterns', setPatterns, renderPatterns);
  if (!sourcesLoaded) await fetchData('/api-sources', setSources, renderSources);
 }

 function saveConfigLocally() {
  localStorage.setItem('localPatterns', JSON.stringify(state.currentPatterns));
  localStorage.setItem('localApiSources', JSON.stringify(state.apiSources));
  updateConfigStatusIndicator();
 }

 function updateConfigStatusIndicator() {
  const hasLocalConfig = !!localStorage.getItem('localPatterns') || !!localStorage.getItem('localApiSources');
  ui.configStatusContainer.classList.toggle('hidden', !hasLocalConfig);
 }
 
 async function handleResetConfig() {
  if (confirm("Are you sure you want to reset your Listeners and API Sources to the server defaults? This action cannot be undone.")) {
   localStorage.removeItem('localPatterns');
   localStorage.removeItem('localApiSources');
   window.location.reload();
  }
 }

 async function fetchData(url, setter, renderer) {
  try {
   const response = await fetch(url);
   if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
   const data = await response.json();
   setter(data);
   if (renderer) renderer();
  } catch (e) { console.error(`Failed to fetch from ${url}:`, e); }
 }

 async function updateDataOnServer(url, data) {
  try {
   const response = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
   });
   if (response.ok) saveConfigLocally();
   else console.error("Failed to save config to server");
  } catch (e) { console.error("Error updating server config:", e); }
 }

 const setPatterns = (data) => { state.currentPatterns = data; };
 const updatePatterns = () => updateDataOnServer('/patterns', state.currentPatterns);

 function renderPatterns() {
  ui.listenersList.innerHTML = state.currentPatterns.length === 0
   ? '<p class="text-sm text-gray-500 italic p-2 text-center">Add a listener to begin.</p>'
   : state.currentPatterns.map(p => `
    <div class="flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80">
     <div class="flex-1 overflow-hidden">
      <p class="font-semibold text-sm truncate" title="${p.label}">${p.label}</p>
      <p class="text-xs text-gray-400 font-mono truncate" title="${p.pattern}">${p.pattern}</p>
     </div>
     <div class="flex items-center space-x-3 ml-2">
      <button title="Edit Listener" class="edit-btn text-gray-500 hover:text-blue-400" data-type="listener" data-label="${p.label}"><i class="fa-solid fa-pencil"></i></button>
      <button title="Remove Listener" class="remove-btn text-gray-500 hover:text-red-400" data-type="listener" data-label="${p.label}"><i class="fa-solid fa-trash-can"></i></button>
     </div>
    </div>`).join('');
 }

 const setSources = (data) => { state.apiSources = data.map(source => ({ ...source, scan_enabled: source.scan_enabled !== false })); };
 const updateSources = () => updateDataOnServer('/api-sources', state.apiSources);

 function renderSources() {
  ui.sourcesList.innerHTML = state.apiSources.length === 0
   ? '<p class="text-sm text-gray-500 italic p-2 text-center">Add an API source to scan.</p>'
   : state.apiSources.map(source => {
    const isScanning = state.isGlobalScanActive && ['scanning', 'manually_paused', 'scan_paused'].includes(state.currentStatus);
    const scanIndicatorHTML = isScanning ? `<div class="w-8 text-center"><i class="fa-solid fa-spinner fa-spin text-blue-400"></i></div>` : '';
    return `
     <div class="flex items-center justify-between p-2 rounded-md bg-gray-800/50 hover:bg-gray-800/80 border ${isScanning ? 'scanning-source' : 'brand-border'}">
      <div class="flex items-center flex-1 overflow-hidden">
       <label class="relative inline-flex items-center cursor-pointer" title="Toggle scanning">
        <input type="checkbox" class="sr-only peer source-scan-toggle" data-name="${source.name}" ${source.scan_enabled ? 'checked' : ''}>
        <div class="w-9 h-5 bg-gray-600 rounded-full peer peer-checked:after:translate-x-full after:absolute after:top-px after:left-px after:bg-white after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-green-600"></div>
       </label>
       <div class="flex-1 overflow-hidden ml-3">
        <p class="font-semibold text-sm truncate font-mono" title="${source.name}">${source.name}</p>
       </div>
      </div>
      <div class="flex items-center space-x-3 ml-2">
       ${scanIndicatorHTML}
       <button title="Edit Source" class="edit-btn text-gray-500 hover:text-blue-400" data-type="source" data-name="${source.name}"><i class="fa-solid fa-pencil"></i></button>
       <button title="Remove Source" class="remove-btn text-gray-500 hover:text-red-400" data-type="source" data-name="${source.name}"><i class="fa-solid fa-trash-can"></i></button>
      </div>
     </div>`;
   }).join('');
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
   resultEl.innerHTML = highlighted !== testStr ? highlighted : '<span class="text-gray-500">No matches found.</span>';
  } catch (e) {
   resultEl.innerHTML = `<span class="text-yellow-500">Invalid JavaScript Regex: ${e.message}</span>`;
  }
 }

 const validateRegexOnServer = utils.debounce(async () => {
  const pattern = ui.listenerPatternInput.value;
  const errorEl = ui.listenerPatternError;
  const indicatorEl = ui.regexValidityIndicator;
  if (!pattern) {
   errorEl.textContent = 'Pattern cannot be empty.';
   indicatorEl.innerHTML = '<i class="fas fa-times-circle text-red-500"></i>';
   listenerFormValidity.pattern = false;
  } else {
   try {
    const response = await fetch('/validate-regex', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pattern }) });
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
  } else if (state.currentPatterns.some(p => p.label === newLabel && newLabel !== originalLabel)) {
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
  ui.presetInput.value = preset.defaultInput || '';
  ui.applyPresetBtn.dataset.presetKey = presetKey;
  ui.presetInput.focus();
 }

 function handleApplyPreset() {
  const presetKey = ui.applyPresetBtn.dataset.presetKey;
  const userInput = ui.presetInput.value.trim();
  const preset = sourcePresets[presetKey];
  if (!preset || !userInput) return;

  if (presetKey === 'reddit') {
   const parts = userInput.split('/');
   if (parts.length < 2) return alert("Invalid format. Please use 'subreddit/query'.");
   const subreddit = parts[0].trim();
   const query = parts.slice(1).join('/').trim();
   if (!subreddit || !query) return alert("Subreddit and query cannot be empty.");
   ui.sourceNameInput.value = preset.name(subreddit, query);
   ui.sourceApiUrlInput.value = preset.apiUrl(subreddit, query);
  } else {
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

 // --- START: API PREVIEWER FUNCTIONS ---

 async function handleFetchPreview() {
  const apiUrl = ui.sourceApiUrlInput.value.trim();
  if (!apiUrl) return alert("Please enter an API URL first.");
 
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
   if (!response.ok) throw new Error(data.error || 'Unknown error fetching preview.');
   
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
   ui.interactivePreviewItem.innerHTML = `<span class="text-yellow-400">Could not find an array of items at data root '${dataRoot}'. Displaying full response.</span>`;
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
    <input type="text" readonly data-key="${key}" placeholder="<not mapped>" class="flex-grow p-2 rounded-md bg-gray-900 border brand-border text-sm font-mono">
    <button type="button" data-key="${key}" title="Map selected path to '${key}'" class="mapping-target-btn px-3 py-2 text-lg rounded-md bg-gray-700 hover:bg-blue-700">
     <i class="fa-solid fa-crosshairs pointer-events-none"></i>
    </button>`;
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
    if (input) input.value = value;
   });
  } catch (e) { console.warn("Could not parse initial field mappings.", e); }
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
  ui.fieldsToCheckContainer.innerHTML = currentFieldsToCheck.length === 0 
   ? '<span class="text-xs text-gray-500 p-1">Use the previewer to select fields to check.</span>'
   : currentFieldsToCheck.map(field => `
    <div class="flex items-center gap-2 bg-gray-900/70 border brand-border rounded-full px-3 py-1 text-sm font-mono">
     <span>${field}</span>
     <button type="button" class="remove-field-btn text-gray-500 hover:text-red-400" data-field="${field}" title="Remove"><i class="fa-solid fa-times-circle"></i></button>
    </div>`).join('');
  syncFieldsToCheckToTextarea();
 }

 function syncFieldsToCheckToTextarea() {
  ui.sourceFieldsToCheckTextarea.value = currentFieldsToCheck.join('\n');
 }
 // --- END: API PREVIEWER FUNCTIONS ---
 
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
    const p = state.currentPatterns.find(p => p.label === data.label);
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
   if (presetsDiv) presetsDiv.style.display = isEdit ? 'none' : 'block';
   resetPreviewer();
   renderMappingInputs();
   ui.originalSourceNameInput.value = isEdit ? data.name : '';
   if (isEdit) {
    const s = state.apiSources.find(s => s.name === data.name);
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
   const patternData = { label: newLabel, pattern: ui.listenerPatternInput.value.trim() };
   if (originalLabel) {
    state.currentPatterns = state.currentPatterns.map(p => p.label === originalLabel ? patternData : p);
   } else {
    state.currentPatterns.push(patternData);
   }
   renderPatterns();
   updatePatterns();
  } else if (type === 'source') {
   syncMappingsToTextarea();
   syncFieldsToCheckToTextarea();
   const newName = ui.sourceNameInput.value.trim();
   const originalName = ui.originalSourceNameInput.value;
   if (state.apiSources.some(s => s.name === newName && newName !== originalName)) {
    return alert("An API source with that name already exists.");
   }
   try {
    const sourceData = {
     name: newName,
     apiUrl: ui.sourceApiUrlInput.value.trim(),
     httpMethod: "GET",
     paginationStyle: "page_number",
     dataRoot: ui.sourceDataRootInput.value.trim(),
     fieldsToCheck: currentFieldsToCheck,
     fieldMappings: JSON.parse(ui.sourceFieldMappingsTextarea.value)
    };
    if (originalName) {
     const existingSource = state.apiSources.find(s => s.name === originalName);
     const updatedSource = { ...existingSource, ...sourceData };
     state.apiSources = state.apiSources.map(s => s.name === originalName ? updatedSource : s);
    } else {
     sourceData.scan_enabled = true;
     state.apiSources.push(sourceData);
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
   state.currentPatterns = state.currentPatterns.filter(p => p.label !== data.label);
   renderPatterns(); 
   updatePatterns();
  } else if (type === 'source') {
   state.apiSources = state.apiSources.filter(s => s.name !== data.name);
   renderSources(); 
   updateSources();
  }
 }

 // --- Event Listeners ---
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

  ui.sourcesList.addEventListener('change', e => {
   if (e.target.classList.contains('source-scan-toggle')) {
    const sourceName = e.target.dataset.name;
    const source = state.apiSources.find(s => s.name === sourceName);
    if (source) {
     source.scan_enabled = e.target.checked;
     updateSources();
    }
   }
  });
  document.addEventListener('click', (e) => {
   const btn = e.target.closest('.edit-btn, .remove-btn');
   if (btn) {
    const { type, ...data } = btn.dataset;
    if (btn.classList.contains('edit-btn')) openModal(type, data);
    else handleRemove(type, data);
   }
  });

  ui.fetchPreviewBtn.addEventListener('click', handleFetchPreview);
  ui.sourceModal.addEventListener('click', e => {
   if(e.target.matches('.preview-tab-btn') && app.handleTabSwitch) app.handleTabSwitch(e.target);
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
     if (!currentFieldsToCheck.includes(path)) currentFieldsToCheck.push(path);
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
 }

 // --- Initializer ---
 loadConfiguration();
 setupConfigControls();
 setupManagementEventListeners();

 // Expose functions needed by other modules
 app.renderSources = renderSources;
 app.openModal = openModal;
 app.closeModal = closeModal;
}