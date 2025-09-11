// --- Core UI & Feed Management ---
// Manages the live feed, scanning status, user interactions, and SSE connection.

function initUI(app) {
 const { ui, state, utils } = app;

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
  const isAnyScanActive = ['scanning', 'scan_paused', 'manually_paused'].includes(state.currentStatus);
  const btn = ui.globalScanControlBtn;
  const newBtn = btn.cloneNode(true);
  btn.parentNode.replaceChild(newBtn, btn);
  ui.globalScanControlBtn = newBtn;

  if (isAnyScanActive) {
   newBtn.innerHTML = `<i class="fa-solid fa-stop mr-2"></i>Stop All Scans`;
   newBtn.className = 'w-full px-3 py-1 text-sm font-semibold rounded-md bg-red-800 hover:bg-red-700 text-white shadow-lg';
   newBtn.addEventListener('click', handleStopScan);
  } else {
   newBtn.innerHTML = `<i class="fa-solid fa-play mr-2"></i>Scan All`;
   newBtn.className = 'w-full px-3 py-1 text-sm font-semibold rounded-md bg-green-800 hover:bg-green-700 text-white shadow-lg';
   newBtn.addEventListener('click', scanAllSources);
  }
 }

 function tearDownTabs() {
  state.isGlobalScanActive = false;
  ui.scanTabsContainer.innerHTML = '';
  ui.scanTabsContainer.classList.add('hidden');
  ui.feedPanesContainer.innerHTML = '';
 }

 function handleStatusUpdate(data) {
  state.currentStatus = data.status;
  setStatus(data.status, data.reason || data.status);
  if (data.source_name) state.activeScan.sourceName = data.source_name;
  if (data.next_page) state.activeScan.nextPage = data.next_page;
  
  if (['idle', 'error'].includes(data.status)) {
   state.activeScan.sourceName = null;
   if(state.isGlobalScanActive) tearDownTabs();
  }

  ui.globalStopBtn.classList.toggle('hidden', !['scanning', 'scan_paused', 'manually_paused'].includes(data.status));
  
  if (app.renderSources) app.renderSources();
  updateControlsUI(data);
  updateGlobalScanControls();

  if (data.status === 'manually_paused') {
   document.querySelectorAll('.feed-card.summary-pending .fa-spinner').forEach(spinner => {
    const summaryContent = spinner.closest('.summary-content');
    if (summaryContent) {
     summaryContent.innerHTML = `<div class="flex items-center gap-4"><p class="text-yellow-400">Summary generation paused.</p><button class="generate-summary-btn px-2 py-1 text-xs rounded bg-green-800 hover:bg-green-700">Generate</button></div>`;
    }
   });
  }
 }

 function updateControlsUI(data = {}) {
  const controls = ui.controlsContainer;
  const status = data.status || state.currentStatus;
  switch (status) {
   case 'scanning':
    controls.innerHTML = `
     <div class="sauron-eye-container"><div class="sauron-eye"></div><div class="scan-beam"></div></div>
     <p class="text-sm text-gray-400 mt-4">${data.reason || 'Scanning...'}</p>
     ${state.isGlobalScanActive ? '<button id="global-pause-btn" class="mt-2 px-4 py-2 text-sm rounded-md bg-yellow-700 hover:bg-yellow-600 text-white"><i class="fa-solid fa-pause mr-2"></i>Pause All</button>' : ''}`;
    if(state.isGlobalScanActive) document.getElementById('global-pause-btn').addEventListener('click', handlePauseScan);
    break;
   case 'manually_paused':
     controls.innerHTML = `
     <img src="https://github.com/ranfysvalle02/the-eye-of-sauron/blob/main/d-eye.png?raw=true" style="width:20%;" alt="Scan Paused" class="mx-auto mb-4 scan-paused-animation">
     <p class="text-lg font-semibold text-yellow-400 mb-3">Scan Paused</p>
     ${state.isGlobalScanActive ? '<button id="global-resume-btn" class="px-6 py-2 rounded-md bg-green-700 hover:bg-green-600 text-white"><i class="fa-solid fa-play mr-2"></i>Resume All</button>' : ''}`;
    if(state.isGlobalScanActive) document.getElementById('global-resume-btn').addEventListener('click', handleResumeScan);
    break;
   case 'scan_paused':
    controls.innerHTML = `
     <img src="https://github.com/ranfysvalle02/the-eye-of-sauron/blob/main/d-eye.png?raw=true" style="width:20%;" alt="Scan Paused" class="mx-auto mb-4 scan-paused-animation">
     <p class="text-lg font-semibold text-yellow-400 mb-3">${data.reason}</p>
     <div class="flex items-center gap-4">
      <button id="continue-scan-btn" class="px-6 py-2 font-semibold rounded-md bg-blue-700 hover:bg-blue-600"><i class="fa-solid fa-forward mr-2"></i>Continue Scan</button>
     </div>`;
    document.getElementById('continue-scan-btn').addEventListener('click', () => startScan(state.activeScan.sourceName, state.activeScan.nextPage));
    break;
   case 'rate_limit_paused':
    controls.innerHTML = `
     <p class="text-red-400 font-semibold mb-3">${data.reason}</p>
     <button id="rate-limit-resume-btn" class="px-5 py-2.5 font-semibold rounded-md bg-green-700 hover:bg-green-600"><i class="fa-solid fa-play mr-2"></i>Resume Operations</button>`;
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
  state.clientSideStop = true;
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
 
 async function handleFindRelated(btn) {
  const card = btn.closest('.feed-card');
  const itemData = JSON.parse(card.dataset.itemData);
  const query = `${itemData.title}\n${itemData.ai_summary}`;
  
  ui.relatedModalSourceTitle.textContent = `Sourced from: "${itemData.title}"`;
  ui.relatedModalContent.innerHTML = `<div class="text-center p-8"><i class="fa-solid fa-spinner fa-spin fa-2x"></i><p class="mt-3">Searching for related items...</p></div>`;
  document.getElementById('related-pagination-controls').innerHTML = '';
  app.openModal('related');
  
  try {
   const response = await fetch('/hybrid-search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
   });
   const results = await response.json();
   if (!response.ok) throw new Error(results.error || 'An unknown error occurred.');
   
   const filteredResults = results.filter(result => String(result.id) !== String(itemData.id));
   state.allRelatedResults = filteredResults;
   state.currentRelatedPage = 1;

   if (state.allRelatedResults.length === 0) {
    ui.relatedModalContent.innerHTML = `<div class="text-center text-gray-500 p-8"><i class="fa-solid fa-box-open fa-2x"></i><p class="mt-3">No other related items were found.</p></div>`;
   } else {
    renderRelatedItemsPage(state.currentRelatedPage);
   }
  } catch (error) {
   ui.relatedModalContent.innerHTML = `<div class="text-center text-red-400 p-8"><i class="fa-solid fa-triangle-exclamation fa-2x"></i><p class="mt-3"><strong>Search Failed:</strong> ${error.message}</p></div>`;
  }
 }

 function renderRelatedItemsPage(page) {
  state.currentRelatedPage = page;
  const contentContainer = ui.relatedModalContent;
  const paginationContainer = document.getElementById('related-pagination-controls');
  contentContainer.innerHTML = '';
  paginationContainer.innerHTML = '';

  if (state.allRelatedResults.length === 0) return;

  const start = (page - 1) * state.relatedItemsPerPage;
  const end = start + state.relatedItemsPerPage;
  const paginatedItems = state.allRelatedResults.slice(start, end);

  paginatedItems.forEach(item => {
   const relatedCard = document.createElement('div');
   relatedCard.className = 'flex items-start p-3 bg-gray-900/50 border brand-border rounded-lg gap-4';
   relatedCard.innerHTML = `
    <div class="text-center shrink-0 w-20">
      <div class="font-bold text-xl text-indigo-300">${item.score.toFixed(3)}</div>
      <div class="text-xs text-gray-500">Relevance</div>
    </div>
    <div class="flex-1 overflow-hidden">
      <a href="${item.url}" target="_blank" class="font-semibold text-white hover:text-green-400 truncate block" title="${item.title}">${item.title}</a>
      <p class="text-xs text-indigo-300 mt-1 mb-2">${item.source_name}</p>
      <p class="text-sm text-gray-400 summary-snippet">${item.ai_summary}</p>
    </div>`;
   contentContainer.appendChild(relatedCard);
  });

  const totalPages = Math.ceil(state.allRelatedResults.length / state.relatedItemsPerPage);
  if (totalPages > 1) {
   const prevButton = `<button class="px-3 py-1 text-sm rounded-md bg-gray-700 hover:bg-gray-600 disabled:opacity-50" ${page === 1 ? 'disabled' : ''} onclick="SauronApp.renderRelatedPage(${page - 1})"><i class="fa-solid fa-arrow-left mr-2"></i> Prev</button>`;
   const pageIndicator = `<span class="text-sm text-gray-400">Page ${page} of ${totalPages}</span>`;
   const nextButton = `<button class="px-3 py-1 text-sm rounded-md bg-gray-700 hover:bg-gray-600 disabled:opacity-50" ${page === totalPages ? 'disabled' : ''} onclick="SauronApp.renderRelatedPage(${page + 1})">Next <i class="fa-solid fa-arrow-right ml-2"></i></button>`;
   paginationContainer.innerHTML = `${prevButton}${pageIndicator}${nextButton}`;
  }
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
   summaryContent.innerHTML = `<div class="markdown-content text-gray-300">${marked.parse(result.ai_summary)}</div>`;
   card.classList.remove('summary-pending');
   card.dataset.itemData = JSON.stringify({ ...itemData, ai_summary: result.ai_summary });
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
   if (!response.ok) throw new Error((await response.json()).error || 'Unknown error');
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

 const applyFiltersAndSort = utils.debounce(() => {
  const searchTerm = ui.feedSearchInput.value.toLowerCase();
  document.querySelectorAll('.feed-card').forEach(card => {
   const itemData = JSON.parse(card.dataset.itemData);
   const searchText = [itemData.title, itemData.by, itemData.ai_summary].join(' ').toLowerCase();
   const searchMatch = !searchTerm || searchText.includes(searchTerm);
   const sourceMatch = state.activeFilters.sources.size === 0 || state.activeFilters.sources.has(itemData.source_name);
   const labelMatch = state.activeFilters.labels.size === 0 || state.activeFilters.labels.has(itemData.matched_label);
   card.style.display = searchMatch && sourceMatch && labelMatch ? '' : 'none';
  });

  document.querySelectorAll('.feed-pane').forEach(pane => {
   const cards = Array.from(pane.querySelectorAll('.feed-card'));
   cards.sort((a, b) => {
    const timeA = JSON.parse(a.dataset.itemData).time || 0;
    const timeB = JSON.parse(b.dataset.itemData).time || 0;
    return state.currentSort === 'newest' ? timeB - timeA : timeA - timeB;
   });
   cards.forEach(card => pane.appendChild(card));
  });
 }, 200);

 function handleClearFeed() {
  tearDownTabs();
  ui.feedPanesContainer.innerHTML = '';
  ui.placeholder.classList.remove('hidden');
  ui.feedControls.classList.add('hidden');
  state.activeFilters = { sources: new Set(), labels: new Set() };
  state.availableFilters = { sources: new Set(), labels: new Set() };
  ui.feedSearchInput.value = '';
  state.currentSort = 'newest';
  renderFilterOptions();
  updateFilterBadge();
 }
 
 function renderFilterOptions() {
  const createFilterCheckbox = (type, value) => {
      const container = document.createElement('label');
      container.className = 'flex items-center space-x-2 cursor-pointer hover:bg-gray-800 p-1 rounded';
      container.innerHTML = `
        <input type="checkbox" class="filter-checkbox form-checkbox" data-type="${type}" data-value="${value}" ${state.activeFilters[type].has(value) ? 'checked' : ''}>
        <span class="truncate" title="${value}">${value}</span>
      `;
      return container;
    };
    ui.sourceFiltersContainer.innerHTML = state.availableFilters.sources.size === 0 ? '<p class="text-gray-500 italic text-xs">No sources yet.</p>' : [...state.availableFilters.sources].sort().map(s => createFilterCheckbox('sources', s).outerHTML).join('');
    ui.labelFiltersContainer.innerHTML = state.availableFilters.labels.size === 0 ? '<p class="text-gray-500 italic text-xs">No labels yet.</p>' : [...state.availableFilters.labels].sort().map(l => createFilterCheckbox('labels', l).outerHTML).join('');
 }
 
 function updateFilterBadge() {
  const count = state.activeFilters.sources.size + state.activeFilters.labels.size;
  ui.filterCountBadge.textContent = count;
  ui.filterCountBadge.classList.toggle('hidden', count === 0);
 }
 
 function setupFilterAndSortControls() {
  ui.feedSearchInput.addEventListener('input', applyFiltersAndSort);
  ui.filterBtn.addEventListener('click', () => ui.filterPopover.classList.toggle('hidden'));
  ui.clearFiltersBtn.addEventListener('click', () => {
   state.activeFilters = { sources: new Set(), labels: new Set() };
   renderFilterOptions();
   updateFilterBadge();
   applyFiltersAndSort();
  });
  ui.filterPopover.addEventListener('change', (e) => {
   if (e.target.classList.contains('filter-checkbox')) {
    const { type, value } = e.target.dataset;
    if (e.target.checked) state.activeFilters[type].add(value);
    else state.activeFilters[type].delete(value);
    updateFilterBadge();
    applyFiltersAndSort();
   }
  });
 }

 function connectToStream() {
  if (eventSource) return;
  eventSource = new EventSource('/stream');
  let cardAnimationDelay = 0;

  eventSource.onmessage = (event) => {
   if (!isLeader) { shutdownApp(); return; }
   const data = JSON.parse(event.data);
   
   const isDashboardActive = !ui.dashboardView.classList.contains('hidden');
   const today = new Date().toISOString().split('T')[0];
   if (state.isMongoDbEnabled && isDashboardActive && state.currentAnalyticsDate === today) {
    if (['api_item', 'summary_update', 'status'].includes(data.type)) {
     app.fetchAndRenderDashboard(state.currentAnalyticsDate);
    }
   }

   switch (data.type) {
    case 'status':
     if (state.clientSideStop && !['idle', 'error'].includes(data.status)) return;
     if (['idle', 'error'].includes(data.status)) state.clientSideStop = false;
     handleStatusUpdate(data);
     if (data.status !== 'scanning') cardAnimationDelay = 0;
     break;
    case 'api_item':
     ui.placeholder.classList.add('hidden');
     ui.feedControls.classList.remove('hidden');
     
     let needsFilterRender = false;
     if (!state.availableFilters.sources.has(data.source_name)) {
      state.availableFilters.sources.add(data.source_name);
      needsFilterRender = true;
     }
     if (!state.availableFilters.labels.has(data.matched_label)) {
      state.availableFilters.labels.add(data.matched_label);
      needsFilterRender = true;
     }
     if (needsFilterRender) renderFilterOptions();
     
     if (!document.querySelector(`[data-item-id="${data.id}"]`)) {
      const card = app.createFeedCard(data, cardAnimationDelay);
      
      let isVisible = true;
      const searchTerm = ui.feedSearchInput.value.toLowerCase();
      if (searchTerm) {
       const searchText = [data.title, data.by, data.ai_summary].join(' ').toLowerCase();
       if (!searchText.includes(searchTerm)) isVisible = false;
      }
      if (state.activeFilters.sources.size > 0 && !state.activeFilters.sources.has(data.source_name)) isVisible = false;
      if (state.activeFilters.labels.size > 0 && !state.activeFilters.labels.has(data.matched_label)) isVisible = false;
      card.style.display = isVisible ? '' : 'none';

      const container = state.isGlobalScanActive
       ? document.getElementById(`feed-pane-${data.source_name}`)
       : ui.feedPanesContainer;
      if (container) {
        if (state.currentSort === 'newest') container.prepend(card);
        else container.appendChild(card);
      }
      cardAnimationDelay += 100;
     }
     break;
    case 'summary_update':
     const card = document.querySelector(`[data-item-id="${data.id}"]`);
     if (card) {
      const summaryContent = card.querySelector('.summary-content');
      summaryContent.innerHTML = `<div class="markdown-content text-gray-300">${marked.parse(data.ai_summary)}</div>`;
      card.classList.remove('summary-pending');
      const relatedBtn = card.querySelector('.find-related-btn');
      if(relatedBtn) relatedBtn.disabled = false;
      const currentData = JSON.parse(card.dataset.itemData);
      card.dataset.itemData = JSON.stringify({ ...currentData, ai_summary: data.ai_summary });
      updateAllSlackButtons();
     }
     break;
   }
  };
  eventSource.onerror = () => {
   handleStatusUpdate({ status: 'error', reason: 'Connection lost. Reconnecting...' });
   eventSource.close();
   eventSource = null;
   setTimeout(connectToStream, 5000);
  };
 }
 
 async function startScan(sourceName, startPage = 1) {
  state.clientSideStop = false;
  await fetch('/scan-source', {
   method: 'POST',
   headers: { 'Content-Type': 'application/json' },
   body: JSON.stringify({ source_name: sourceName, start_page: startPage })
  });
 }

 function scanAllSources() {
  const activeSources = state.apiSources.filter(source => source.scan_enabled);
  if (activeSources.length === 0) {
   return alert('No sources are enabled for scanning.');
  }
  state.clientSideStop = false;
  state.isGlobalScanActive = true;
  tearDownTabs();
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
   pane.id = `feed-pane-${source.name}`;
   pane.className = 'feed-pane space-y-6';
   if (index > 0) pane.classList.add('hidden');
   ui.feedPanesContainer.appendChild(pane);
  });

  fetch('/scan-all-sources', {
   method: 'POST',
   headers: { 'Content-Type': 'application/json' },
   body: JSON.stringify({ source_names: activeSources.map(s => s.name) })
  }).catch(err => {
   console.error("Error starting all scans:", err);
   handleStatusUpdate({ status: 'error', reason: 'Failed to start all scans.' });
  });
 }

 // --- Initializer ---
 setupFilterAndSortControls();
 updateGlobalScanControls();
 updateControlsUI();
 connectToStream();

 ui.globalStopBtn.addEventListener('click', handleStopScan);
 ui.feedContainer.addEventListener('click', (e) => {
  if (e.target.closest('.scan-tab')) app.handleTabSwitch(e.target.closest('.scan-tab'));
  else handleFeedActions(e);
 });
 ui.feedControls.addEventListener('click', (e) => {
  if (e.target.closest('#clear-feed-btn')) handleClearFeed();
 });
 document.addEventListener('click', (e) => {
  if (ui.filterPopover && !ui.filterBtn.contains(e.target) && !ui.filterPopover.contains(e.target)) {
   ui.filterPopover.classList.add('hidden');
  }
  if (ui.configPopover && !ui.configStatusContainer.contains(e.target)) {
   ui.configPopover.classList.add('hidden');
  }
 });
 
 // Expose functions needed by other modules
 SauronApp.renderRelatedPage = renderRelatedItemsPage;
 app.updateAllSlackButtons = updateAllSlackButtons;
}