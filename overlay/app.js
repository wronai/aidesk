/**
 * AI Desktop Assistant - Overlay App (Web Components Architecture)
 */
import { sseService } from './services/sse.js';
import './components/connection-status.js';
import './components/analysis-viewer.js';
import './components/transcript-viewer.js';
import './components/window-context.js';
import './components/screen-summary.js';
import './components/ocr-controls.js';
import './components/agent-actions.js';
import './components/selection-panel.js';

const BACKEND_URL = 'http://localhost:8001';

class App {
  constructor() {
    // UI Components
    this.connectionStatus = document.querySelector('connection-status');
    this.analysisViewer = document.querySelector('analysis-viewer');
    this.transcriptViewer = document.querySelector('transcript-viewer');
    this.windowContext = document.querySelector('window-context');
    this.screenSummary = document.querySelector('screen-summary');
    this.ocrControls = document.querySelector('ocr-controls');
    this.agentActions = document.querySelector('agent-actions');
    this.selectionPanel = document.querySelector('selection-panel');
    
    // Other elements
    this.timestampEl = document.getElementById('timestamp');
    this.statsTextEl = document.getElementById('statsText');
    this.btnMoveScreen = document.getElementById('btnMoveScreen');

    this.init();
  }

  init() {
    this.setupSSE();
    this.setupEventListeners();
    this.setupMouseForwarding();
    this.setupShortcuts();
    this.loadInitialSettings();
    
    // Periodic health check
    setInterval(() => this.checkHealth(), 30000);
    
    console.log('App v2 initialized');
  }

  setupSSE() {
    // Connection status updates
    sseService.on('sse:connecting', () => this.connectionStatus.update('connecting'));
    sseService.on('sse:connected', () => this.connectionStatus.update('connected'));
    sseService.on('sse:error', () => this.connectionStatus.update('error'));

    // Data events
    sseService.on('analysis', (data) => this.handleAnalysis(data));
    sseService.on('transcript', (data) => this.transcriptViewer.update(data.text, data.is_final));
    sseService.on('window', (data) => this.windowContext.update(data));
    sseService.on('organized_screen', (data) => this.screenSummary.update(data));
    sseService.on('agent_actions', (data) => this.agentActions.update(data.actions));
    sseService.on('agent_result', (data) => this.agentActions.showResult(data));
    sseService.on('ocr_benchmark', (data) => this.ocrControls.showBenchmarkResults(data));
    sseService.on('error', (data) => this.analysisViewer.update(data.message, true));
    sseService.on('diagnostics', (data) => this.handleDiagnostics(data));
    
    // Connect
    sseService.connect();
  }

  setupEventListeners() {
    // OCR Controls events
    this.ocrControls.addEventListener('mode-change', (e) => this.switchMode(e.detail.mode));
    this.ocrControls.addEventListener('engine-change', (e) => this.switchEngine(e.detail.engine));
    this.ocrControls.addEventListener('run-benchmark', () => this.runBenchmark());

    // Agent Actions events
    this.agentActions.addEventListener('approve-action', (e) => this.approveAction(e.detail.id));
    this.agentActions.addEventListener('copy-command', (e) => this.copyToClipboard(e.detail.command));

    // Selection Panel events
    this.selectionPanel.addEventListener('analyze', (e) => this.analyzeSelection(e.detail.text));
    this.selectionPanel.addEventListener('input-focus', () => this.setFocus(true));
    this.selectionPanel.addEventListener('input-blur', () => this.setFocus(false));

    // Header buttons
    if (this.btnMoveScreen) {
      this.btnMoveScreen.addEventListener('click', () => {
        if (window.electron) window.electron.moveToNextScreen();
      });
    }

    // Electron events
    if (window.electron && window.electron.onAnalyzeSelection) {
      window.electron.onAnalyzeSelection((text) => {
        this.selectionPanel.show(text);
        this.analyzeSelection(text);
      });
    }
  }

  handleAnalysis(data) {
    const { text, timestamp, tokens, cost, provider, mode, ocr, window: winData, agent_actions, organized_screen } = data;

    this.analysisViewer.update(text);
    
    if (winData) this.windowContext.update(winData);
    if (organized_screen) this.screenSummary.update(organized_screen);
    if (agent_actions) this.agentActions.update(agent_actions);

    // Update stats text
    const modeLabel = mode || 'vision';
    const catLabel = winData ? ` • ${winData.category}` : '';
    const stats = `${provider} • ${modeLabel}${catLabel} • ${Math.round(tokens)} tok • $${cost.toFixed(6)}`;
    if (this.statsTextEl) this.statsTextEl.textContent = stats;

    // Update OCR info in controls
    this.ocrControls.updateInfo(ocr);

    // Update timestamp
    const date = new Date(timestamp * 1000);
    if (this.timestampEl) {
      this.timestampEl.textContent = date.toLocaleTimeString('pl-PL');
    }
  }

  handleDiagnostics(data) {
    if (!data.all_ok) {
      const failed = data.checks.filter(c => !c.ok).map(c => c.name);
      this.connectionStatus.setWarning(`Diagnostics: ${failed.join(', ')}`);
    }
  }

  async switchMode(mode) {
    try {
      await fetch(`${BACKEND_URL}/mode/${mode}`, { method: 'POST' });
    } catch (e) {
      console.error('Mode switch failed', e);
    }
  }

  async switchEngine(engine) {
    try {
      await fetch(`${BACKEND_URL}/ocr/engine/${engine}`, { method: 'POST' });
    } catch (e) {
      console.error('Engine switch failed', e);
    }
  }

  async runBenchmark() {
    this.ocrControls.setBenchmarkLoading(true);
    try {
      const res = await fetch(`${BACKEND_URL}/ocr/benchmark`, { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        this.ocrControls.showBenchmarkResults(data);
      }
    } catch (e) {
      console.error('Benchmark failed', e);
    } finally {
      this.ocrControls.setBenchmarkLoading(false);
    }
  }

  async approveAction(actionId) {
    try {
      await fetch(`${BACKEND_URL}/agent/approve/${actionId}`, { method: 'POST' });
      const res = await fetch(`${BACKEND_URL}/agent/execute/${actionId}`, { method: 'POST' });
      const data = await res.json();
      this.agentActions.showResult(data);
    } catch (e) {
      console.error('Action execution failed', e);
    }
  }

  async analyzeSelection(text) {
    try {
      const res = await fetch(`${BACKEND_URL}/analyze-selection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();
      this.selectionPanel.showResult(data);
    } catch (e) {
      this.selectionPanel.showResult({ error: e.message });
    }
  }

  copyToClipboard(text) {
    navigator.clipboard.writeText(text);
  }

  setFocus(focused) {
    if (window.electron) {
      window.electron.setFocusable(focused);
      window.electron.setSelectionWatcher(!focused);
    }
  }

  setupMouseForwarding() {
    if (!window.electron || !window.electron.setIgnoreMouseEvents) return;
    
    // Components handle their own interactivity, but we need global forwarding logic
    // Actually, css :hover based forwarding is easiest
    const interactiveSelectors = 'select, button, input, textarea, ocr-controls, agent-actions, selection-panel, .header, .resize-handle';

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

  setupShortcuts() {
    // Global shortcuts can be handled here if needed
  }

  async loadInitialSettings() {
    try {
      const [modeRes, ocrRes] = await Promise.all([
        fetch(`${BACKEND_URL}/mode`),
        fetch(`${BACKEND_URL}/ocr/engines`)
      ]);
      
      if (modeRes.ok) {
        const data = await modeRes.json();
        this.ocrControls.setMode(data.mode);
      }
      
      if (ocrRes.ok) {
        const data = await ocrRes.json();
        this.ocrControls.setAvailableEngines(data.engines);
      }
    } catch (e) {
      console.warn('Failed to load initial settings', e);
    }
  }

  async checkHealth() {
    try {
      const res = await fetch(`${BACKEND_URL}/health`);
      const data = await res.json();
      if (data.status === 'healthy' && !sseService.isConnected) {
        sseService.connect();
      }
    } catch (e) {
      // ignore
    }
  }
}

// Start app
window.addEventListener('DOMContentLoaded', () => {
  new App();
});
