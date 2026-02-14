/**
 * Proxeen Assistant - Overlay UI Logic
 * Connects to backend via Server-Sent Events (SSE)
 */

const BACKEND_URL = 'http://localhost:8001';
const RECONNECT_INTERVAL = 3000; // ms

let eventSource = null;
let reconnectTimer = null;
let isConnected = false;

// DOM elements
const elements = {
  connectionStatus: document.getElementById('connectionStatus'),
  transcriptSection: document.getElementById('transcriptSection'),
  transcript: document.getElementById('transcript'),
  analysis: document.getElementById('analysis'),
  statsText: document.getElementById('statsText'),
  timestamp: document.getElementById('timestamp'),
  modeSelect: document.getElementById('modeSelect'),
  ocrEngineSelect: document.getElementById('ocrEngineSelect'),
  btnBenchmark: document.getElementById('btnBenchmark'),
  benchmarkResults: document.getElementById('benchmarkResults'),
  ocrInfo: document.getElementById('ocrInfo'),
  ocrInfoText: document.getElementById('ocrInfoText'),
  windowContext: document.getElementById('windowContext'),
  windowEmoji: document.getElementById('windowEmoji'),
  windowAppName: document.getElementById('windowAppName'),
  windowCategory: document.getElementById('windowCategory'),
  windowDetail: document.getElementById('windowDetail'),
  agentActions: document.getElementById('agentActions'),
  agentActionsList: document.getElementById('agentActionsList'),
  screenSummary: document.getElementById('screenSummary'),
  screenSummaryText: document.getElementById('screenSummaryText'),
  screenSummaryCount: document.getElementById('screenSummaryCount'),
  // Selection panel
  selectionPanel: document.getElementById('selectionPanel'),
  selectionInput: document.getElementById('selectionInput'),
  selectionResponse: document.getElementById('selectionResponse'),
  selectionClipboard: document.getElementById('selectionClipboard'),
  btnAnalyze: document.getElementById('btnAnalyze'),
  btnCloseSelection: document.getElementById('btnCloseSelection'),
  btnCopySelection: document.getElementById('btnCopySelection'),
  btnMoveScreen: document.getElementById('btnMoveScreen'),
};

// State for selection analysis
let lastSelectionClipboardText = '';
let lastSelectionText = '';

/**
 * Initialize SSE connection
 */
function connect() {
  if (eventSource) {
    eventSource.close();
  }

  console.log('Connecting to backend...');
  updateConnectionStatus('connecting');

  eventSource = new EventSource(`${BACKEND_URL}/stream`);

  eventSource.addEventListener('connected', (e) => {
    console.log('Connected to backend');
    isConnected = true;
    updateConnectionStatus('connected');
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  });

  eventSource.addEventListener('analysis', (e) => {
    const data = JSON.parse(e.data);
    handleAnalysis(data);
  });

  eventSource.addEventListener('transcript', (e) => {
    const data = JSON.parse(e.data);
    handleTranscript(data);
  });

  eventSource.addEventListener('error', (e) => {
    const data = JSON.parse(e.data);
    handleError(data);
  });

  eventSource.addEventListener('ocr_engine_changed', (e) => {
    const data = JSON.parse(e.data);
    handleOCREngineChanged(data);
  });

  eventSource.addEventListener('mode_changed', (e) => {
    const data = JSON.parse(e.data);
    handleModeChanged(data);
  });

  eventSource.addEventListener('window', (e) => {
    const data = JSON.parse(e.data);
    handleWindowUpdate(data);
  });

  eventSource.addEventListener('agent_actions', (e) => {
    const data = JSON.parse(e.data);
    handleAgentActions(data);
  });

  eventSource.addEventListener('agent_result', (e) => {
    const data = JSON.parse(e.data);
    handleAgentResult(data);
  });

  eventSource.addEventListener('windows_layout', (e) => {
    const data = JSON.parse(e.data);
    handleWindowsLayout(data);
  });

  eventSource.addEventListener('organized_screen', (e) => {
    const data = JSON.parse(e.data);
    handleOrganizedScreen(data);
  });

  eventSource.addEventListener('ocr_benchmark', (e) => {
    const data = JSON.parse(e.data);
    handleBenchmarkResult(data);
  });

  eventSource.addEventListener('diagnostics', (e) => {
    const data = JSON.parse(e.data);
    handleDiagnostics(data);
  });

  eventSource.addEventListener('selection_analysis', (e) => {
    const data = JSON.parse(e.data);
    handleSelectionAnalysisResult(data);
  });

  eventSource.addEventListener('skill_result', (e) => {
    const data = JSON.parse(e.data);
    handleSkillResult(data);
  });

  eventSource.addEventListener('clipboard_suggestions', (e) => {
    const data = JSON.parse(e.data);
    console.log('Clipboard suggestions:', data);
  });

  eventSource.addEventListener('heartbeat', (e) => {
    // Just keep connection alive
    console.log('Heartbeat received');
  });

  eventSource.onerror = (error) => {
    console.error('SSE error:', error);
    isConnected = false;
    updateConnectionStatus('error');
    eventSource.close();

    // Attempt reconnection
    if (!reconnectTimer) {
      console.log(`Reconnecting in ${RECONNECT_INTERVAL / 1000}s...`);
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, RECONNECT_INTERVAL);
    }
  };
}

/**
 * Update connection status indicator
 */
function updateConnectionStatus(status) {
  elements.connectionStatus.className = 'status-indicator';
  
  switch (status) {
    case 'connected':
      elements.connectionStatus.classList.add('connected');
      elements.connectionStatus.title = 'Połączono';
      break;
    case 'connecting':
      elements.connectionStatus.title = 'Łączenie...';
      break;
    case 'error':
      elements.connectionStatus.classList.add('error');
      elements.connectionStatus.title = 'Błąd połączenia';
      break;
  }
}

/**
 * Handle screen analysis event
 */
function handleAnalysis(data) {
  const { text, timestamp, size_kb, tokens, cost, provider, mode, ocr, window: windowData, agent_actions, organized_screen } = data;

  // Update window context bar if window data is present
  if (windowData) {
    handleWindowUpdate(windowData);
  }

  // Update organized screen summary if present
  if (organized_screen) {
    handleOrganizedScreen(organized_screen);
  }

  // Update agent actions if present
  if (agent_actions && agent_actions.length > 0) {
    handleAgentActions({ actions: agent_actions });
  }

  // Update analysis content with fade effect
  elements.analysis.classList.add('updating');
  
  setTimeout(() => {
    // Remove loading state
    elements.analysis.innerHTML = formatAnalysisText(text);
    elements.analysis.classList.remove('updating');

    // Update stats with mode info and window category
    const modeLabel = mode || 'vision';
    const catLabel = windowData ? ` • ${windowData.category}` : '';
    const statsText = `${provider} • ${modeLabel}${catLabel} • ${Math.round(tokens)} tok • $${cost.toFixed(6)}`;
    elements.statsText.textContent = statsText;

    // Show OCR info if available
    if (ocr && ocr.engine !== 'disabled') {
      elements.ocrInfo.style.display = 'block';
      elements.ocrInfoText.textContent = 
        `OCR: ${ocr.engine} • ${ocr.latency_ms.toFixed(0)}ms • ${(ocr.confidence * 100).toFixed(0)}% • ${ocr.boxes_count} box`;
    } else {
      elements.ocrInfo.style.display = 'none';
    }

    // Update timestamp
    updateTimestamp(timestamp);
  }, 150);
}

/**
 * Handle transcript event
 */
function handleTranscript(data) {
  const { text, is_final } = data;

  // Show transcript section
  elements.transcriptSection.style.display = 'block';
  
  // Update text
  elements.transcript.textContent = text;

  // Add interim styling
  if (!is_final) {
    elements.transcript.style.opacity = '0.7';
  } else {
    elements.transcript.style.opacity = '1';
    
    // Hide after 5 seconds if final
    setTimeout(() => {
      elements.transcriptSection.style.display = 'none';
    }, 5000);
  }
}

/**
 * Handle error event
 */
function handleError(data) {
  const { message } = data;
  
  const errorHtml = `
    <div class="error-message">
      ⚠️ ${escapeHtml(message)}
    </div>
  `;
  
  elements.analysis.innerHTML = errorHtml;
}

/**
 * Format analysis text (parse JSON if possible, otherwise plain text)
 */
function formatAnalysisText(text) {
  try {
    // Try to parse as JSON
    const json = JSON.parse(text);
    
    let html = '';
    
    if (json.app) {
      html += `<div style="font-weight: 600; margin-bottom: 8px;">📱 ${escapeHtml(json.app)}</div>`;
    }
    
    if (json.task) {
      html += `<div style="margin-bottom: 10px; color: #a3a3a3;">${escapeHtml(json.task)}</div>`;
    }
    
    if (json.suggestions && json.suggestions.length > 0) {
      html += '<div style="margin-top: 10px;">';
      json.suggestions.forEach((suggestion, i) => {
        html += `<div style="margin-bottom: 6px;">💡 ${escapeHtml(suggestion)}</div>`;
      });
      html += '</div>';
    }
    
    if (json.summary) {
      html += `<div style="margin-top: 10px; font-style: italic; color: #888;">${escapeHtml(json.summary)}</div>`;
    }

    // Priority indicator
    if (json.priority) {
      const priorityClass = `priority-${json.priority}`;
      html = `<div class="${priorityClass}">${html}</div>`;
    }
    
    return html || escapeHtml(text);
    
  } catch (e) {
    // Not JSON, return as plain text with line breaks
    return escapeHtml(text).replace(/\n/g, '<br>');
  }
}

/**
 * Update timestamp display
 */
function updateTimestamp(unixTimestamp) {
  const date = new Date(unixTimestamp * 1000);
  const timeStr = date.toLocaleTimeString('pl-PL', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
  elements.timestamp.textContent = timeStr;
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

/**
 * Check backend health
 */
async function checkHealth() {
  try {
    const response = await fetch(`${BACKEND_URL}/health`);
    const data = await response.json();
    console.log('Backend health:', data);
    return data.status === 'healthy';
  } catch (error) {
    console.error('Health check failed:', error);
    return false;
  }
}

/**
 * Handle OCR engine change event
 */
function handleOCREngineChanged(data) {
  const { engine } = data;
  if (elements.ocrEngineSelect) {
    elements.ocrEngineSelect.value = engine;
  }
  console.log('OCR engine changed to:', engine);
}

/**
 * Handle analysis mode change event
 */
function handleModeChanged(data) {
  const { mode } = data;
  if (elements.modeSelect) {
    elements.modeSelect.value = mode;
  }
  console.log('Analysis mode changed to:', mode);
}

/**
 * Handle benchmark result event
 */
function handleBenchmarkResult(data) {
  const { engines, winners } = data;
  
  if (!elements.benchmarkResults) return;
  elements.benchmarkResults.style.display = 'block';

  let html = '<div class="benchmark-title">Benchmark Results</div>';
  html += '<div class="benchmark-grid">';
  
  for (const [name, result] of Object.entries(engines)) {
    const isWinner = name === winners.fastest;
    const isMostConfident = name === winners.most_confident;
    const badges = [];
    if (isWinner) badges.push('<span class="badge badge-fast">fastest</span>');
    if (isMostConfident) badges.push('<span class="badge badge-conf">best conf</span>');
    
    html += `
      <div class="benchmark-item ${isWinner ? 'benchmark-winner' : ''}">
        <div class="benchmark-engine">${escapeHtml(name)} ${badges.join(' ')}</div>
        <div class="benchmark-stats">
          <span>${result.latency_ms.toFixed(0)}ms</span>
          <span>${(result.confidence * 100).toFixed(0)}%</span>
          <span>${result.boxes_count} box</span>
        </div>
        <div class="benchmark-preview">${escapeHtml(result.text_preview || '').substring(0, 80)}${(result.text_preview || '').length > 80 ? '...' : ''}</div>
      </div>
    `;
  }
  html += '</div>';

  elements.benchmarkResults.innerHTML = html;

  // Auto-hide after 15 seconds
  setTimeout(() => {
    elements.benchmarkResults.style.display = 'none';
  }, 15000);
}

/**
 * Switch analysis mode via API
 */
async function switchMode(mode) {
  try {
    const response = await fetch(`${BACKEND_URL}/mode/${mode}`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok) {
      console.error('Mode switch failed:', data);
    }
  } catch (error) {
    console.error('Mode switch error:', error);
  }
}

/**
 * Switch OCR engine via API
 */
async function switchOCREngine(engine) {
  try {
    const response = await fetch(`${BACKEND_URL}/ocr/engine/${engine}`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok) {
      console.error('OCR engine switch failed:', data);
    }
  } catch (error) {
    console.error('OCR engine switch error:', error);
  }
}

/**
 * Run OCR benchmark via API
 */
async function runBenchmark() {
  try {
    elements.btnBenchmark.disabled = true;
    elements.btnBenchmark.textContent = '...';
    const response = await fetch(`${BACKEND_URL}/ocr/benchmark`, { method: 'POST' });
    const data = await response.json();
    if (response.ok) {
      handleBenchmarkResult(data);
    }
  } catch (error) {
    console.error('Benchmark error:', error);
  } finally {
    elements.btnBenchmark.disabled = false;
    elements.btnBenchmark.textContent = '\u26a1 Test';
  }
}

/**
 * Load current OCR/mode settings from backend
 */
async function loadOCRSettings() {
  try {
    // Load mode
    const modeRes = await fetch(`${BACKEND_URL}/mode`);
    if (modeRes.ok) {
      const modeData = await modeRes.json();
      if (elements.modeSelect) {
        elements.modeSelect.value = modeData.mode;
      }
    }

    // Load OCR engines
    const ocrRes = await fetch(`${BACKEND_URL}/ocr/engines`);
    if (ocrRes.ok) {
      const ocrData = await ocrRes.json();
      if (elements.ocrEngineSelect && ocrData.engines) {
        // Rebuild options based on actually available engines
        elements.ocrEngineSelect.innerHTML = '';
        ocrData.engines.forEach(eng => {
          const opt = document.createElement('option');
          opt.value = eng.engine;
          opt.textContent = eng.engine;
          if (eng.active) opt.selected = true;
          elements.ocrEngineSelect.appendChild(opt);
        });
      }
    }
  } catch (error) {
    console.log('Could not load OCR settings (backend may not be ready):', error.message);
  }
}

/**
 * Category emoji mapping
 */
const CATEGORY_EMOJI = {
  ide: '💻', terminal: '🖥️', browser: '🌐', email: '📧',
  chat: '💬', office: '📄', media: '🎨', file_manager: '📁',
  system: '⚙️', unknown: '📝',
};

/**
 * Handle window context update from backend
 */
function handleWindowUpdate(data) {
  if (!elements.windowContext) return;

  const category = data.category || 'unknown';
  const appName = data.wm_class_name || data.title || '—';
  const emoji = CATEGORY_EMOJI[category] || '📝';

  elements.windowContext.style.display = 'block';
  elements.windowEmoji.textContent = emoji;
  elements.windowAppName.textContent = appName;
  elements.windowCategory.textContent = category;
  elements.windowCategory.className = `badge badge-cat-${category}`;

  // Detail line: git branch, CWD
  let detail = '';
  if (data.git && data.git.branch) {
    detail += `🔀 ${data.git.branch}`;
    if (data.git.status) detail += ` (${data.git.status})`;
  }
  if (data.cwd) {
    const shortCwd = data.cwd.replace(/^\/home\/[^/]+/, '~');
    if (detail) detail += '  •  ';
    detail += `📁 ${shortCwd}`;
  }
  elements.windowDetail.textContent = detail;
  elements.windowDetail.style.display = detail ? 'block' : 'none';
}

/**
 * Handle agent action suggestions from backend
 */
function handleAgentActions(data) {
  if (!elements.agentActions || !data.actions || data.actions.length === 0) {
    if (elements.agentActions) elements.agentActions.style.display = 'none';
    return;
  }

  elements.agentActions.style.display = 'block';

  let html = '';
  for (const action of data.actions) {
    const riskClass = `risk-${action.risk}`;
    const riskLabel = action.risk === 'safe' ? '✅' : action.risk === 'low' ? '🟡' : action.risk === 'medium' ? '🟠' : '🔴';
    html += `
      <div class="agent-action-item ${riskClass}">
        <div class="agent-action-desc">${riskLabel} ${escapeHtml(action.description)}</div>
        <div class="agent-action-cmd"><code>${escapeHtml(action.command)}</code></div>
        <div class="agent-action-buttons">
          <button class="btn-agent btn-approve" onclick="approveAndExecute('${action.action_id}')" title="Zatwierdź i wykonaj">▶ Wykonaj</button>
          <button class="btn-agent btn-copy" onclick="copyCommand('${escapeHtml(action.command)}')" title="Kopiuj komendę">📋</button>
        </div>
      </div>
    `;
  }

  elements.agentActionsList.innerHTML = html;

  // Auto-hide after 30 seconds
  setTimeout(() => {
    if (elements.agentActions) elements.agentActions.style.display = 'none';
  }, 30000);
}

/**
 * Handle agent execution result
 */
function handleAgentResult(data) {
  if (!data.executed) return;

  const statusEmoji = data.exit_code === 0 ? '✅' : '❌';
  const output = data.output ? data.output.substring(0, 200) : '(brak wyjścia)';

  // Show result as temporary notification in agent actions area
  if (elements.agentActionsList) {
    const resultHtml = `
      <div class="agent-result">
        <div class="agent-result-header">${statusEmoji} ${escapeHtml(data.description)} (exit: ${data.exit_code})</div>
        <pre class="agent-result-output">${escapeHtml(output)}</pre>
      </div>
    `;
    elements.agentActionsList.innerHTML = resultHtml;
    elements.agentActions.style.display = 'block';

    setTimeout(() => {
      if (elements.agentActions) elements.agentActions.style.display = 'none';
    }, 15000);
  }
}

/**
 * Approve and execute an agent action via API
 */
async function approveAndExecute(actionId) {
  try {
    // Approve first
    await fetch(`${BACKEND_URL}/agent/approve/${actionId}`, { method: 'POST' });
    // Then execute
    const res = await fetch(`${BACKEND_URL}/agent/execute/${actionId}`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      handleAgentResult(data);
    }
  } catch (error) {
    console.error('Agent execute error:', error);
  }
}

/**
 * Handle diagnostics broadcast from autodiagnostics loop
 */
function handleDiagnostics(data) {
  if (!data.all_ok) {
    const failed = data.checks.filter(c => !c.ok).map(c => c.name);
    console.warn('Diagnostics issues:', failed);
    // Flash connection indicator orange briefly
    elements.connectionStatus.classList.add('warning');
    elements.connectionStatus.title = `Diagnostics: ${failed.join(', ')}`;
    setTimeout(() => {
      elements.connectionStatus.classList.remove('warning');
      elements.connectionStatus.title = 'Connected';
    }, 5000);
  }
}

/**
 * Handle windows layout broadcast (all visible windows)
 */
function handleWindowsLayout(data) {
  // Update screen summary count
  if (elements.screenSummaryCount) {
    elements.screenSummaryCount.textContent = `${data.total} okien`;
  }
}

/**
 * Handle organized screen data (per-app crops + categories)
 */
function handleOrganizedScreen(data) {
  if (!elements.screenSummary) return;

  elements.screenSummary.style.display = 'flex';

  // Show summary text
  if (data.summary && elements.screenSummaryText) {
    elements.screenSummaryText.textContent = data.summary;
  }

  // Show window count with categories
  if (elements.screenSummaryCount) {
    const cats = (data.categories || []).map(c => CATEGORY_EMOJI[c] || c).join(' ');
    elements.screenSummaryCount.textContent = `${data.total_windows} okien ${cats}`;
  }
}

/**
 * Copy command to clipboard
 */
function copyCommand(command) {
  navigator.clipboard.writeText(command).then(() => {
    console.log('Command copied to clipboard:', command);
  }).catch(err => {
    console.error('Clipboard copy failed:', err);
  });
}

/**
 * Show the selection analysis panel with text pre-filled
 */
function showSelectionPanel(text) {
  if (!elements.selectionPanel) return;
  elements.selectionPanel.style.display = 'block';
  elements.selectionInput.value = text;
  elements.selectionResponse.innerHTML = '<div class="loading"><div class="loading-spinner"></div></div>';
  elements.selectionClipboard.style.display = 'none';
}

/**
 * Send text to backend for analysis
 */
async function analyzeSelection(text) {
  if (!text) return;

  showSelectionPanel(text);

  try {
    elements.btnAnalyze.disabled = true;
    elements.btnAnalyze.textContent = '...';

    const response = await fetch(`${BACKEND_URL}/analyze-selection`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });

    if (response.ok) {
      const data = await response.json();
      handleSelectionAnalysisResult(data);
    } else {
      elements.selectionResponse.innerHTML = '<div class="error-message">Błąd analizy</div>';
    }
  } catch (error) {
    console.error('Selection analysis error:', error);
    elements.selectionResponse.innerHTML = `<div class="error-message">⚠️ ${escapeHtml(error.message)}</div>`;
  } finally {
    elements.btnAnalyze.disabled = false;
    elements.btnAnalyze.textContent = '▶ Analizuj';
  }
}

/**
 * Display selection analysis result — skill matches with popup options
 */
function handleSelectionAnalysisResult(data) {
  if (!elements.selectionPanel) return;
  elements.selectionPanel.style.display = 'block';

  lastSelectionText = data.text || '';

  // New skill-based response format
  if (data.matches && data.matches.length > 0) {
    let html = '';

    for (const match of data.matches.slice(0, 3)) {
      html += `<div class="skill-match">`;
      html += `<div class="skill-match-header">`;
      html += `<span class="skill-match-icon">${match.icon || '🔧'}</span>`;
      html += `<span class="skill-match-label">${escapeHtml(match.label)}</span>`;
      html += `<span class="skill-match-conf">${Math.round(match.confidence * 100)}%</span>`;
      html += `</div>`;

      if (match.options && match.options.length > 0) {
        html += `<div class="skill-options">`;
        for (const opt of match.options) {
          const riskClass = opt.risk === 'safe' ? 'risk-safe' : opt.risk === 'low' ? 'risk-low' : opt.risk === 'medium' ? 'risk-medium' : 'risk-high';
          html += `<button class="skill-option-btn ${riskClass}" `;
          html += `onclick="executeSkillOption('${escapeHtml(match.skill)}', '${escapeHtml(opt.id)}')" `;
          html += `title="${escapeHtml(opt.description || '')}">`;
          html += `${opt.icon || ''} ${escapeHtml(opt.label)}`;
          html += `</button>`;
        }
        html += `</div>`;
      }
      html += `</div>`;
    }

    elements.selectionResponse.innerHTML = html;
    elements.selectionClipboard.style.display = 'none';
  } else if (data.response) {
    // Fallback: old format
    elements.selectionResponse.innerHTML = formatSimpleMarkdown(data.response);
    lastSelectionClipboardText = data.clipboard_text || '';
    elements.selectionClipboard.style.display = lastSelectionClipboardText ? 'flex' : 'none';
  } else {
    elements.selectionResponse.innerHTML = '<div style="color:#888">Brak dopasowanych umiejętności</div>';
    elements.selectionClipboard.style.display = 'none';
  }
}

/**
 * Execute a skill option chosen by user click
 */
async function executeSkillOption(skillName, optionId) {
  try {
    const response = await fetch(`${BACKEND_URL}/skill/execute`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        skill: skillName,
        option_id: optionId,
        text: lastSelectionText,
      }),
    });

    if (response.ok) {
      const result = await response.json();
      handleSkillResult(result);
    } else {
      elements.selectionResponse.innerHTML = '<div class="error-message">❌ Błąd wykonania</div>';
    }
  } catch (error) {
    console.error('Skill execution error:', error);
    elements.selectionResponse.innerHTML = `<div class="error-message">⚠️ ${escapeHtml(error.message)}</div>`;
  }
}

/**
 * Handle skill execution result
 */
function handleSkillResult(data) {
  if (!elements.selectionResponse) return;

  let html = '';
  const emoji = data.success ? '✅' : '❌';
  html += `<div class="skill-result-msg">${emoji} ${formatSimpleMarkdown(data.message || '')}</div>`;

  if (data.output) {
    html += `<pre class="skill-result-output">${escapeHtml(data.output.substring(0, 1000))}</pre>`;
  }

  elements.selectionResponse.innerHTML = html;

  // Show copy button if clipboard text available
  lastSelectionClipboardText = data.clipboard_text || '';
  if (lastSelectionClipboardText) {
    elements.selectionClipboard.style.display = 'flex';
    elements.btnCopySelection.textContent = '📋 Kopiuj';
  }

  // Auto-open URL if provided
  if (data.open_url) {
    window.open(data.open_url, '_blank');
  }
}

/**
 * Simple markdown-like formatting for analysis responses.
 * Supports: **bold**, `code`, ```code blocks```, \n → <br>
 */
function formatSimpleMarkdown(text) {
  let html = escapeHtml(text);
  // Code blocks (```)
  html = html.replace(/```([\s\S]*?)```/g, '<pre>$1</pre>');
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Line breaks
  html = html.replace(/\n/g, '<br>');
  return html;
}

/**
 * Set up mouse event forwarding for click-through overlay.
 * Interactive elements (selects, buttons) disable click-through on hover.
 */
function setupMouseForwarding() {
  if (!window.electron || !window.electron.setIgnoreMouseEvents) return;

  // All interactive elements that should be clickable
  const interactiveSelectors = 'select, button, input, textarea, .ocr-controls, .header, .agent-actions, .agent-action-buttons, .selection-panel, .resize-handle';

  document.addEventListener('mouseover', (e) => {
    if (e.target.closest(interactiveSelectors)) {
      window.electron.setIgnoreMouseEvents(false);
    }
  });

  document.addEventListener('mouseout', (e) => {
    if (e.target.closest(interactiveSelectors)) {
      window.electron.setIgnoreMouseEvents(true, { forward: true });
    }
  });
}

/**
 * Initialize on page load
 */
window.addEventListener('DOMContentLoaded', () => {
  console.log('Proxeen Assistant Overlay initialized');
  
  // Check if electron bridge is available
  if (window.electron) {
    console.log('Running in Electron');
    window.electron.log('Overlay UI loaded');
  }
  
  // Enable click-through forwarding for interactive elements
  setupMouseForwarding();

  // Connect to backend
  connect();

  // Set up OCR control event listeners
  if (elements.modeSelect) {
    elements.modeSelect.addEventListener('change', (e) => switchMode(e.target.value));
  }
  if (elements.ocrEngineSelect) {
    elements.ocrEngineSelect.addEventListener('change', (e) => switchOCREngine(e.target.value));
  }
  if (elements.btnBenchmark) {
    elements.btnBenchmark.addEventListener('click', runBenchmark);
  }

  // Selection panel event listeners
  if (elements.btnAnalyze) {
    elements.btnAnalyze.addEventListener('click', () => {
      const text = elements.selectionInput.value.trim();
      if (text) analyzeSelection(text);
    });
  }
  if (elements.btnCloseSelection) {
    elements.btnCloseSelection.addEventListener('click', () => {
      elements.selectionPanel.style.display = 'none';
      // Restore click-through
      if (window.electron) window.electron.setFocusable(false);
    });
  }
  if (elements.btnCopySelection) {
    elements.btnCopySelection.addEventListener('click', () => {
      if (lastSelectionClipboardText) {
        navigator.clipboard.writeText(lastSelectionClipboardText);
        elements.btnCopySelection.textContent = '✅ Skopiowano';
        setTimeout(() => { elements.btnCopySelection.textContent = '📋 Kopiuj'; }, 2000);
      }
    });
  }
  if (elements.btnMoveScreen) {
    elements.btnMoveScreen.addEventListener('click', () => {
      if (window.electron) window.electron.moveToNextScreen();
    });
  }
  if (elements.selectionInput) {
    elements.selectionInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const text = elements.selectionInput.value.trim();
        if (text) analyzeSelection(text);
      }
    });
    // Make overlay focusable when clicking textarea + pause selection watcher
    elements.selectionInput.addEventListener('focus', () => {
      if (window.electron) {
        window.electron.setFocusable(true);
        window.electron.setSelectionWatcher(false); // pause auto-detection while typing
      }
    });
    elements.selectionInput.addEventListener('blur', () => {
      if (window.electron) {
        window.electron.setFocusable(false);
        window.electron.setSelectionWatcher(true); // resume auto-detection
      }
    });
  }

  // Listen for Ctrl+Shift+S from main process
  if (window.electron && window.electron.onAnalyzeSelection) {
    window.electron.onAnalyzeSelection((text) => {
      showSelectionPanel(text);
      analyzeSelection(text);
    });
  }

  // Load current settings from backend after short delay
  setTimeout(loadOCRSettings, 2000);
  
  // Periodic health check (every 30s)
  setInterval(() => {
    if (!isConnected) {
      checkHealth().then(healthy => {
        if (healthy && !eventSource) {
          console.log('Backend is healthy, reconnecting...');
          connect();
        }
      });
    }
  }, 30000);
});

/**
 * Cleanup on unload
 */
window.addEventListener('beforeunload', () => {
  if (eventSource) {
    eventSource.close();
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
  }
});
