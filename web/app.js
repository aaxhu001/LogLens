/**
 * LogLens — Frontend Controller
 * Pure vanilla JavaScript with Bootstrap 5
 */

document.addEventListener("DOMContentLoaded", () => {
  // ==========================================
  // STATE MANAGEMENT
  // ==========================================
  const state = {
    query: "",
    level: "",
    service: "",
    from: "",
    to: "",
    page: 1,
    pageSize: 50,
    totalPages: 1,
    totalMatches: 0,
    matchedTokens: [],
    indexReady: false,
    currentView: "searchView",
  };

  // ==========================================
  // DOM ELEMENT REFERENCES
  // ==========================================
  const elements = {
    // Navigation
    tabSearch: document.getElementById("tabSearch"),
    tabAnalytics: document.getElementById("tabAnalytics"),
    tabStats: document.getElementById("tabStats"),
    navButtons: document.querySelectorAll(".nav-tab-btn"),
    views: document.querySelectorAll(".app-view"),
    liveAnnouncer: document.getElementById("liveAnnouncer"),

    // Search Form & Inputs
    searchForm: document.getElementById("searchForm"),
    queryInput: document.getElementById("queryInput"),
    clearQueryBtn: document.getElementById("clearQueryBtn"),
    levelSelect: document.getElementById("levelSelect"),
    serviceSelect: document.getElementById("serviceSelect"),
    fromTimeInput: document.getElementById("fromTimeInput"),
    toTimeInput: document.getElementById("toTimeInput"),
    searchSubmitBtn: document.getElementById("searchSubmitBtn"),

    // Results & Status Strip
    statusStrip: document.getElementById("statusStrip"),
    statMatches: document.getElementById("statMatches"),
    statLatency: document.getElementById("statLatency"),
    countError: document.getElementById("countError"),
    countWarn: document.getElementById("countWarn"),
    countInfo: document.getElementById("countInfo"),
    pageIndicator: document.getElementById("pageIndicator"),
    prevPageBtn: document.getElementById("prevPageBtn"),
    nextPageBtn: document.getElementById("nextPageBtn"),

    // UI States
    stateLoading: document.getElementById("stateLoading"),
    stateIndexNotBuilt: document.getElementById("stateIndexNotBuilt"),
    retryAfterIngestBtn: document.getElementById("retryAfterIngestBtn"),
    stateInvalidQuery: document.getElementById("stateInvalidQuery"),
    invalidQueryMessage: document.getElementById("invalidQueryMessage"),
    invalidQueryExample: document.getElementById("invalidQueryExample"),
    stateNoMatches: document.getElementById("stateNoMatches"),
    broadenSearchBtn: document.getElementById("broadenSearchBtn"),
    stateApiError: document.getElementById("stateApiError"),
    apiErrorMessage: document.getElementById("apiErrorMessage"),
    retrySearchBtn: document.getElementById("retrySearchBtn"),

    // Results Containers
    resultsContainer: document.getElementById("resultsContainer"),
    resultsTableBody: document.getElementById("resultsTableBody"),
    resultsMobileCards: document.getElementById("resultsMobileCards"),

    // Analytics View
    analyticsUnavailable: document.getElementById("analyticsUnavailable"),
    analyticsContent: document.getElementById("analyticsContent"),
    spikeChartImg: document.getElementById("spikeChartImg"),
    spikeTableBody: document.getElementById("spikeTableBody"),
    interpretationText: document.getElementById("interpretationText"),
    refreshAnalyticsBtn: document.getElementById("refreshAnalyticsBtn"),
    checkAnalyticsAgainBtn: document.getElementById("checkAnalyticsAgainBtn"),

    // Stats View
    statCardTotalLines: document.getElementById("statCardTotalLines"),
    statCardVocab: document.getElementById("statCardVocab"),
    statCardPostings: document.getElementById("statCardPostings"),
    statCardMemory: document.getElementById("statCardMemory"),
    statsServicesBadges: document.getElementById("statsServicesBadges"),
    statsManifestJson: document.getElementById("statsManifestJson"),

    // Modal
    contextModalEl: document.getElementById("contextModal"),
    contextModalTitle: document.getElementById("contextModalTitle"),
    contextLinesList: document.getElementById("contextLinesList"),
  };

  const contextModal = new bootstrap.Modal(elements.contextModalEl);

  // ==========================================
  // UTILITIES: ESCAPING & HIGHLIGHTING (RULE 8)
  // ==========================================

  /**
   * Escape HTML entities to prevent XSS.
   */
  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function escapeRegex(string) {
    return string.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  /**
   * Highlight matched search tokens in pre-escaped HTML text.
   */
  function highlightText(escapedText, tokens) {
    if (!tokens || tokens.length === 0) return escapedText;

    // Filter and sort tokens longest-first
    const validTokens = tokens
      .map(t => t.trim().toLowerCase())
      .filter(t => t.length > 0)
      .sort((a, b) => b.length - a.length);

    if (validTokens.length === 0) return escapedText;

    const regexPattern = `\\b(${validTokens.map(escapeRegex).join("|")})\\b`;
    const regex = new RegExp(regexPattern, "gi");

    return escapedText.replace(regex, '<mark class="query-highlight">$1</mark>');
  }

  function announce(message) {
    elements.liveAnnouncer.textContent = message;
  }

  // ==========================================
  // UI STATE TRANSITIONS
  // ==========================================

  function hideAllStates() {
    elements.stateLoading.classList.add("d-none");
    elements.stateIndexNotBuilt.classList.add("d-none");
    elements.stateInvalidQuery.classList.add("d-none");
    elements.stateNoMatches.classList.add("d-none");
    elements.stateApiError.classList.add("d-none");
    elements.resultsContainer.classList.add("d-none");
    elements.statusStrip.classList.add("d-none");
  }

  function showLoading() {
    hideAllStates();
    elements.stateLoading.classList.remove("d-none");
    announce("Executing search query...");
  }

  function showIndexNotBuilt() {
    hideAllStates();
    elements.stateIndexNotBuilt.classList.remove("d-none");
    announce("Search index not built yet.");
  }

  function showInvalidQuery(message, example) {
    hideAllStates();
    elements.invalidQueryMessage.textContent = message || "Syntax error in search expression.";
    elements.invalidQueryExample.textContent = example || "level:ERROR service:payments timeout";
    elements.stateInvalidQuery.classList.remove("d-none");
    announce("Invalid query syntax error.");
  }

  function showNoMatches() {
    hideAllStates();
    elements.statusStrip.classList.remove("d-none");
    elements.statMatches.textContent = "0";
    elements.statLatency.textContent = "0.00 ms";
    elements.countError.textContent = "0";
    elements.countWarn.textContent = "0";
    elements.countInfo.textContent = "0";
    elements.stateNoMatches.classList.remove("d-none");
    announce("No matching log lines found.");
  }

  function showApiError(msg) {
    hideAllStates();
    elements.apiErrorMessage.textContent = msg || "Could not connect to LogLens server.";
    elements.stateApiError.classList.remove("d-none");
    announce("API server connection error.");
  }

  // ==========================================
  // TAB NAVIGATION
  // ==========================================

  elements.navButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const targetViewId = btn.getAttribute("data-view");
      switchView(targetViewId);
    });
  });

  function switchView(targetViewId) {
    state.currentView = targetViewId;
    elements.views.forEach(view => {
      if (view.id === targetViewId) {
        view.classList.remove("d-none");
      } else {
        view.classList.add("d-none");
      }
    });

    elements.navButtons.forEach(btn => {
      if (btn.getAttribute("data-view") === targetViewId) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });

    if (targetViewId === "analyticsView") {
      loadAnalytics();
    } else if (targetViewId === "statsView") {
      loadStats();
    }
  }

  // ==========================================
  // SEARCH EXECUTION & API CALL
  // ==========================================

  async function executeSearch(page = 1) {
    state.page = page;
    state.query = elements.queryInput.value.trim();
    state.level = elements.levelSelect.value;
    state.service = elements.serviceSelect.value;
    state.from = elements.fromTimeInput.value.trim();
    state.to = elements.toTimeInput.value.trim();

    showLoading();

    const params = new URLSearchParams();
    if (state.query) params.append("query", state.query);
    if (state.level) params.append("level", state.level);
    if (state.service) params.append("service", state.service);
    if (state.from) params.append("from", state.from);
    if (state.to) params.append("to", state.to);
    params.append("page", state.page);
    params.append("pageSize", state.pageSize);

    try {
      const res = await fetch(`/search?${params.toString()}`);

      if (res.status === 503) {
        // Index not built
        showIndexNotBuilt();
        return;
      }

      if (res.status === 400) {
        // Syntax error
        const errJson = await res.json();
        showInvalidQuery(errJson.message, errJson.example);
        return;
      }

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        showApiError(errJson.message || `Server returned error status ${res.status}`);
        return;
      }

      const data = await res.json();
      renderSearchResults(data);

    } catch (err) {
      showApiError("Network error: Could not reach LogLens API server.");
    }
  }

  function renderSearchResults(data) {
    state.totalMatches = data.totalMatches;
    state.totalPages = data.totalPages || 1;
    state.matchedTokens = data.matchedTokens || [];

    if (data.totalMatches === 0) {
      showNoMatches();
      return;
    }

    hideAllStates();
    elements.statusStrip.classList.remove("d-none");
    elements.resultsContainer.classList.remove("d-none");

    // Update Status Strip metrics
    elements.statMatches.textContent = data.totalMatches.toLocaleString();
    elements.statLatency.textContent = `${data.queryMs.toFixed(2)} ms`;
    elements.countError.textContent = (data.levelCounts.ERROR || 0).toLocaleString();
    elements.countWarn.textContent = (data.levelCounts.WARN || 0).toLocaleString();
    elements.countInfo.textContent = (data.levelCounts.INFO || 0).toLocaleString();

    // Pagination buttons
    elements.pageIndicator.textContent = `Page ${data.page} of ${Math.max(1, data.totalPages)}`;
    elements.prevPageBtn.disabled = data.page <= 1;
    elements.nextPageBtn.disabled = data.page >= data.totalPages;

    // Render Table Rows (Desktop)
    elements.resultsTableBody.innerHTML = "";
    elements.resultsMobileCards.innerHTML = "";

    data.lines.forEach(item => {
      const escapedLvl = escapeHtml(item.level);
      const escapedSvc = escapeHtml(item.service);
      const escapedTs = escapeHtml(item.timestamp);
      const escapedMsg = escapeHtml(item.message || item.raw_text);

      const highlightedMsg = highlightText(escapedMsg, state.matchedTokens);

      // Desktop row
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="text-secondary">${item.line_id}</td>
        <td>${escapedTs}</td>
        <td><span class="level-badge level-badge-${escapedLvl}">${escapedLvl}</span></td>
        <td class="text-info">${escapedSvc}</td>
        <td class="log-msg-cell">${highlightedMsg}</td>
        <td class="text-end">
          <button class="btn btn-outline-secondary btn-sm min-touch-target btn-context" data-line-id="${item.line_id}">
            Context
          </button>
        </td>
      `;
      elements.resultsTableBody.appendChild(tr);

      // Mobile card
      const card = document.createElement("div");
      card.className = "mobile-log-card";
      card.innerHTML = `
        <div class="mobile-log-card-header">
          <div>
            <span class="badge bg-dark border border-secondary text-secondary me-1">#${item.line_id}</span>
            <span class="level-badge level-badge-${escapedLvl}">${escapedLvl}</span>
          </div>
          <span class="text-info fs-8">${escapedSvc}</span>
        </div>
        <div class="text-secondary fs-8 mb-2">${escapedTs}</div>
        <div class="font-monospace fs-8 log-msg-cell mb-2">${highlightedMsg}</div>
        <div class="text-end">
          <button class="btn btn-outline-secondary btn-sm min-touch-target btn-context w-100" data-line-id="${item.line_id}">
            Inspect Context
          </button>
        </div>
      `;
      elements.resultsMobileCards.appendChild(card);
    });

    announce(`Found ${data.totalMatches} matches. Displaying page ${data.page}.`);
  }

  // ==========================================
  // CONTEXT INSPECTION MODAL
  // ==========================================

  async function openLineContext(lineId) {
    elements.contextModalTitle.textContent = `Line Context: ID #${lineId}`;
    elements.contextLinesList.innerHTML = `
      <div class="text-center py-4">
        <div class="spinner-border text-info spinner-border-sm" role="status"></div>
        <span class="ms-2 text-secondary">Fetching disk context...</span>
      </div>
    `;
    contextModal.show();

    try {
      const res = await fetch(`/line/${lineId}?window=4`);
      if (!res.ok) {
        elements.contextLinesList.innerHTML = `<div class="alert alert-danger p-2 mb-0">Failed to load line context.</div>`;
        return;
      }
      const data = await res.json();
      elements.contextLinesList.innerHTML = "";

      data.lines.forEach(lineItem => {
        const row = document.createElement("div");
        row.className = `context-line-row ${lineItem.is_target ? "target-line" : ""}`;

        const escapedLvl = escapeHtml(lineItem.level);
        const escapedMsg = escapeHtml(lineItem.message || lineItem.raw_text);
        const highlightedMsg = highlightText(escapedMsg, state.matchedTokens);

        row.innerHTML = `
          <span class="context-line-num">[${lineItem.line_id}]</span>
          <span class="level-badge level-badge-${escapedLvl} me-1">${escapedLvl}</span>
          <span class="text-info me-2">${escapeHtml(lineItem.service)}</span>
          <span class="flex-grow-1">${highlightedMsg}</span>
        `;
        elements.contextLinesList.appendChild(row);
      });
    } catch (err) {
      elements.contextLinesList.innerHTML = `<div class="alert alert-danger p-2 mb-0">Error fetching context.</div>`;
    }
  }

  // Event Delegation for Context buttons
  document.addEventListener("click", e => {
    if (e.target && e.target.classList.contains("btn-context")) {
      const lid = e.target.getAttribute("data-line-id");
      if (lid) openLineContext(parseInt(lid, 10));
    }
  });

  // ==========================================
  // ERROR SPIKE ANALYTICS
  // ==========================================

  async function loadAnalytics() {
    try {
      const res = await fetch("/analytics");
      if (!res.ok) {
        elements.analyticsUnavailable.classList.remove("d-none");
        elements.analyticsContent.classList.add("d-none");
        return;
      }
      const data = await res.json();

      if (!data.available) {
        elements.analyticsUnavailable.classList.remove("d-none");
        elements.analyticsContent.classList.add("d-none");
        return;
      }

      elements.analyticsUnavailable.classList.add("d-none");
      elements.analyticsContent.classList.remove("d-none");

      // Update Chart image with cache-busting timestamp
      elements.spikeChartImg.src = `${data.chartUrl}?t=${Date.now()}`;

      // Populate Spikes Table
      elements.spikeTableBody.innerHTML = "";
      if (data.spikes && data.spikes.length > 0) {
        data.spikes.forEach(s => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td class="text-info fw-bold">${s.date || ""}</td>
            <td class="text-danger fw-bold">${s.error_count || s.errors || ""}</td>
            <td>${s.rolling_mean ? parseFloat(s.rolling_mean).toFixed(1) : ""}</td>
            <td>${s.threshold ? parseFloat(s.threshold).toFixed(1) : ""}</td>
            <td><span class="badge bg-danger">SPIKE (>3σ)</span></td>
          `;
          elements.spikeTableBody.appendChild(tr);
        });
      } else {
        elements.spikeTableBody.innerHTML = `<tr><td colspan="5" class="text-center text-secondary">No 3-sigma error anomalies detected.</td></tr>`;
      }

      // Populate Interpretation Text
      elements.interpretationText.textContent = data.interpretation || "No interpretation summary recorded.";

    } catch (err) {
      elements.analyticsUnavailable.classList.remove("d-none");
      elements.analyticsContent.classList.add("d-none");
    }
  }

  elements.refreshAnalyticsBtn.addEventListener("click", loadAnalytics);
  elements.checkAnalyticsAgainBtn.addEventListener("click", loadAnalytics);

  // ==========================================
  // INDEX DIAGNOSTICS & STATS
  // ==========================================

  async function loadStats() {
    try {
      const res = await fetch("/stats");
      if (!res.ok) return;
      const data = await res.json();

      if (data.status === "not_built") {
        elements.statCardTotalLines.textContent = "0";
        elements.statCardVocab.textContent = "0";
        elements.statCardPostings.textContent = "0";
        elements.statCardMemory.textContent = "0 MB";
        elements.statsManifestJson.textContent = "Index has not been built yet.";
        return;
      }

      elements.statCardTotalLines.textContent = (data.totalLines || 0).toLocaleString();
      elements.statCardVocab.textContent = (data.vocabularySize || 0).toLocaleString();
      elements.statCardPostings.textContent = (data.totalPostings || 0).toLocaleString();
      elements.statCardMemory.textContent = `${data.memoryMb || 0} MB`;

      // Update Service badges in Stats View and Service Dropdown in Search View
      elements.statsServicesBadges.innerHTML = "";
      const currentSelectedService = elements.serviceSelect.value;
      elements.serviceSelect.innerHTML = `<option value="">All Services</option>`;

      if (data.services) {
        data.services.forEach(svc => {
          // Add badge
          const badge = document.createElement("span");
          badge.className = "badge bg-dark border border-secondary text-info p-2 font-monospace fs-8";
          badge.textContent = svc;
          elements.statsServicesBadges.appendChild(badge);

          // Add dropdown option
          const opt = document.createElement("option");
          opt.value = svc;
          opt.textContent = svc;
          if (svc === currentSelectedService) opt.selected = true;
          elements.serviceSelect.appendChild(opt);
        });
      }

      elements.statsManifestJson.textContent = JSON.stringify(data.manifest || {}, null, 2);

    } catch (err) {
      console.error("Could not fetch stats:", err);
    }
  }

  // ==========================================
  // EVENT LISTENERS
  // ==========================================

  elements.searchForm.addEventListener("submit", e => {
    e.preventDefault();
    executeSearch(1);
  });

  elements.clearQueryBtn.addEventListener("click", () => {
    elements.queryInput.value = "";
    elements.queryInput.focus();
  });

  elements.broadenSearchBtn.addEventListener("click", () => {
    elements.levelSelect.value = "";
    elements.serviceSelect.value = "";
    elements.fromTimeInput.value = "";
    elements.toTimeInput.value = "";
    executeSearch(1);
  });

  elements.retrySearchBtn.addEventListener("click", () => {
    executeSearch(state.page);
  });

  elements.retryAfterIngestBtn.addEventListener("click", () => {
    loadStats();
    executeSearch(1);
  });

  elements.prevPageBtn.addEventListener("click", () => {
    if (state.page > 1) executeSearch(state.page - 1);
  });

  elements.nextPageBtn.addEventListener("click", () => {
    if (state.page < state.totalPages) executeSearch(state.page + 1);
  });

  // Initial load
  loadStats().then(() => {
    executeSearch(1);
  });
});
