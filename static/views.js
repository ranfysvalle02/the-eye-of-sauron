// --- View Management ---
// Manages the Dashboard and Matches Database views.

function initViews(app) {
  const { ui, state, utils } = app;

  // --- Dashboard / Analytics Logic ---

  function initDashboard() {
    flatpickr(ui.analyticsDatePicker, {
      dateFormat: "Y-m-d",
      defaultDate: "today",
      theme: "dark",
      onChange: function(selectedDates, dateStr) {
        state.currentAnalyticsDate = dateStr;
        fetchAndRenderDashboard(dateStr);
      },
    });
    fetchAndRenderDashboard(state.currentAnalyticsDate);
    ui.clearLocalDashboardBtn.addEventListener('click', handleClearLocalDashboard);
  }

  function getStatsFromLocalStorage(dateStr) {
    const storedStats = localStorage.getItem(`analytics_${dateStr}`);
    const defaultStats = {
      '_id': dateStr, 'date': dateStr, 'totalScansStarted': 0, 'totalItemsMatched': 0,
      'totalSummariesGenerated': 0, 'scansBySource': {}, 'matchesByLabel': {},
      'matchesBySourceLabel': {}, 'hourlyActivity': Object.fromEntries(Array.from({ length: 24 }, (_, i) => [i.toString(), 0]))
    };
    return storedStats ? JSON.parse(storedStats) : defaultStats;
  }

  function handleClearLocalDashboard() {
    if (confirm(`Are you sure you want to clear all locally stored analytics for ${state.currentAnalyticsDate}?`)) {
      localStorage.removeItem(`analytics_${state.currentAnalyticsDate}`);
      fetchAndRenderDashboard(state.currentAnalyticsDate);
    }
  }

  async function fetchAndRenderDashboard(dateStr) {
    try {
      const response = await fetch(`/analytics/daily-stats?date=${dateStr}`);
      if (!response.ok) throw new Error('Failed to load stats');
      const data = await response.json();

      if (data.use_local_storage) {
        state.isMongoDbEnabled = false;
        ui.localStorageControls.classList.remove('hidden');
        const localStats = getStatsFromLocalStorage(dateStr);
        renderDashboard(localStats);
      } else {
        state.isMongoDbEnabled = true;
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
    
    renderInteractiveTable('source-label-table', 'source-label-table-search', sourceLabelData, [
      { key: 'source', title: 'Source' },
      { key: 'label', title: 'Label' },
      { key: 'count', title: 'Count' }
    ]);
  }

  function renderKpiCards(stats) {
    ui.kpiScansStarted.textContent = stats.totalScansStarted || 0;
    ui.kpiItemsMatched.textContent = stats.totalItemsMatched || 0;
    ui.kpiSummariesGenerated.textContent = stats.totalSummariesGenerated || 0;
  }
  
  function renderHourlyChart(hourlyData) {
    const chartContainer = document.getElementById('hourly-activity-chart-container');
    if (state.dashboardCharts.hourly) state.dashboardCharts.hourly.destroy();
    chartContainer.innerHTML = '';

    const canvas = document.createElement('canvas');
    chartContainer.appendChild(canvas);
    const labels = Array.from({ length: 24 }, (_, i) => i.toString().padStart(2, '0') + ":00");
    const data = labels.map((_, i) => (hourlyData || {})[i.toString()] || 0);

    state.dashboardCharts.hourly = new Chart(canvas, {
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
    if (state.dashboardCharts.labels) state.dashboardCharts.labels.destroy();
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

    state.dashboardCharts.labels = new Chart(canvas, {
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
    
    let tableSortState = {};

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

    searchInput.addEventListener('keyup', utils.debounce(render, 200));
    render();
  }

  // --- Matches Database View Logic ---
  function initializeMatchesView() {
    if (!state.isMongoDbEnabled) {
      ui.matchesPlaceholder.innerHTML = `
        <i class="fas fa-database fa-3x text-red-500"></i>
        <p class="mt-4 text-lg">Database Not Connected</p>
        <p class="text-sm">The Matches Database requires a connection to MongoDB.</p>
      `;
      return;
    }
    
    state.matchesState.isInitialized = true;
    renderMatchesSourceFilters();
    setupMatchesViewEventListeners();
    setupMatchesInfiniteScroll();
    fetchMatches(true);
  }

  const fetchMatches = utils.debounce(async (isReset = false) => {
    if (state.matchesState.isLoading) return;
    if (isReset) {
      state.matchesState.currentPage = 1;
      state.matchesState.totalPages = 1;
      ui.matchesResultsContainer.innerHTML = '';
    }
    if (state.matchesState.currentPage > state.matchesState.totalPages) return;

    state.matchesState.isLoading = true;
    if (isReset) {
      ui.matchesPlaceholder.innerHTML = `<i class="fa-solid fa-spinner fa-spin fa-2x"></i>`;
    }

    const params = new URLSearchParams({
      page: state.matchesState.currentPage,
      per_page: 20,
      sort_order: state.matchesState.sortOrder,
      sort_by: 'time',
      query: state.matchesState.query
    });
    state.matchesState.sources.forEach(source => params.append('source_name', source));

    try {
      const response = await fetch(`/matches?${params.toString()}`);
      if (!response.ok) throw new Error(`Server responded with status ${response.status}`);
      
      const { data, pagination } = await response.json();
      state.matchesState.totalPages = pagination.total_pages;

      if (pagination.total_items === 0) {
        ui.matchesPlaceholder.innerHTML = `<i class="fas fa-box-open fa-3x"></i><p class="mt-4 text-lg">No Matches Found</p>`;
      } else if (isReset) {
        ui.matchesPlaceholder.innerHTML = '';
      }

      renderMatches(data);
      state.matchesState.currentPage++;
    } catch (error) {
      console.error("Failed to fetch matches:", error);
      ui.matchesPlaceholder.innerHTML = `<i class="fas fa-triangle-exclamation fa-3x text-red-500"></i><p class="mt-4 text-lg">Error Loading Matches</p>`;
    } finally {
      state.matchesState.isLoading = false;
    }
  }, 300);

  function renderMatches(matches) {
    matches.forEach(item => {
      const card = app.createFeedCard(item);
      ui.matchesResultsContainer.appendChild(card);
    });

    const loader = document.getElementById('matches-loader');
    if (loader) loader.remove();

    if (state.matchesState.currentPage < state.matchesState.totalPages) {
      const newLoader = document.createElement('div');
      newLoader.id = 'matches-loader';
      newLoader.className = 'text-center py-8';
      newLoader.innerHTML = `<i class="fa-solid fa-spinner fa-spin fa-2x"></i>`;
      ui.matchesResultsContainer.appendChild(newLoader);
    }
  }

  function renderMatchesSourceFilters() {
    ui.matchesSourceFiltersContainer.innerHTML = '';
    if (state.apiSources.length === 0) {
      ui.matchesSourceFiltersContainer.innerHTML = '<p class="text-gray-500 italic text-xs">No sources configured.</p>';
      return;
    }
    state.apiSources.forEach(source => {
      const container = document.createElement('label');
      container.className = 'flex items-center space-x-2 cursor-pointer hover:bg-gray-800 p-1 rounded';
      container.innerHTML = `
        <input type="checkbox" class="matches-filter-checkbox form-checkbox" data-value="${source.name}" ${state.matchesState.sources.has(source.name) ? 'checked' : ''}>
        <span class="truncate">${source.name}</span>
      `;
      ui.matchesSourceFiltersContainer.appendChild(container);
    });
  }

  function updateMatchesFilterBadge() {
    const count = state.matchesState.sources.size;
    ui.matchesFilterCountBadge.textContent = count;
    ui.matchesFilterCountBadge.classList.toggle('hidden', count === 0);
  }

  function setupMatchesInfiniteScroll() {
    const options = {
      root: ui.matchesResultsContainer.parentElement,
      rootMargin: '0px',
      threshold: 1.0
    };
    state.matchesScrollObserver = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && !state.matchesState.isLoading) {
        fetchMatches();
      }
    }, options);

    const mutationObserver = new MutationObserver(() => {
      const newLoader = document.getElementById('matches-loader');
      if (newLoader) state.matchesScrollObserver.observe(newLoader);
    });
    mutationObserver.observe(ui.matchesResultsContainer, { childList: true });
  }

  function setupMatchesViewEventListeners() {
    ui.matchesSearchInput.addEventListener('input', () => {
      state.matchesState.query = ui.matchesSearchInput.value;
      fetchMatches(true);
    });
    ui.matchesSortSelect.addEventListener('change', () => {
      state.matchesState.sortOrder = ui.matchesSortSelect.value;
      fetchMatches(true);
    });
    ui.matchesFilterBtn.addEventListener('click', () => {
      ui.matchesFilterPopover.classList.toggle('hidden');
    });
    ui.matchesClearFiltersBtn.addEventListener('click', () => {
      state.matchesState.sources.clear();
      renderMatchesSourceFilters();
      updateMatchesFilterBadge();
      fetchMatches(true);
    });
    ui.matchesFilterPopover.addEventListener('change', (e) => {
      if (e.target.classList.contains('matches-filter-checkbox')) {
        const { value } = e.target.dataset;
        if (e.target.checked) state.matchesState.sources.add(value);
        else state.matchesState.sources.delete(value);
        updateMatchesFilterBadge();
        fetchMatches(true);
      }
    });
  }

  // --- View Switching Logic ---
  function handleViewSwitch(viewId) {
    document.querySelectorAll('.view-content').forEach(v => v.classList.add('hidden'));
    document.getElementById(viewId).classList.remove('hidden');

    document.querySelectorAll('.view-tab-btn').forEach(b => b.classList.remove('active-view-tab'));
    document.querySelector(`.view-tab-btn[data-view="${viewId}"]`).classList.add('active-view-tab');

    if (viewId === 'dashboard-view') {
      fetchAndRenderDashboard(state.currentAnalyticsDate);
    } else if (viewId === 'matches-view') {
      if (!state.matchesState.isInitialized) {
        initializeMatchesView();
      } else {
        fetchMatches(true);
      }
    }
  }
  
  // --- Initializer ---
  initDashboard();
  document.addEventListener('click', (e) => {
    const tabBtn = e.target.closest('.view-tab-btn');
    if (tabBtn) handleViewSwitch(tabBtn.dataset.view);

    if (ui.matchesFilterPopover && !ui.matchesFilterBtn.contains(e.target) && !ui.matchesFilterPopover.contains(e.target)) {
      ui.matchesFilterPopover.classList.add('hidden');
    }
  });

  // Expose functions needed by other modules
  app.fetchAndRenderDashboard = fetchAndRenderDashboard;
}