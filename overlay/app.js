/**
 * AI Desktop Assistant - Overlay UI Logic
 * Connects to backend via Server-Sent Events (SSE)
 */

const BACKEND_URL = 'http://localhost:8000';
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
};

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
  const { text, timestamp, size_kb, tokens, cost, provider } = data;

  // Update analysis content with fade effect
  elements.analysis.classList.add('updating');
  
  setTimeout(() => {
    // Remove loading state
    elements.analysis.innerHTML = formatAnalysisText(text);
    elements.analysis.classList.remove('updating');

    // Update stats
    const statsText = `${provider} • ${Math.round(tokens)} tok • $${cost.toFixed(6)}`;
    elements.statsText.textContent = statsText;

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
 * Initialize on page load
 */
window.addEventListener('DOMContentLoaded', () => {
  console.log('AI Desktop Assistant Overlay initialized');
  
  // Check if electron bridge is available
  if (window.electron) {
    console.log('Running in Electron');
    window.electron.log('Overlay UI loaded');
  }
  
  // Connect to backend
  connect();
  
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
