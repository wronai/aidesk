/**
 * Proxeen Assistant - Overlay App (Web Components Architecture)
 */
import { BACKEND_URL } from './config.js';
import { sseService } from './services/sse.js';
import './components/connection-status.js';
import './components/analysis-viewer.js';
import './components/transcript-viewer.js';
import './components/window-context.js';
import './components/screen-summary.js';
import './components/ocr-controls.js';
import './components/agent-actions.js';
import './components/selection-panel.js';

const VOICE_COMMAND_REGEX = /\b(przetłumacz|translate|tłumacz|uruchom|wykonaj|run|exec|odpal|przeczytaj|czytaj|read|mów|powiedz|kopiuj|copy|skopiuj|szukaj|search|wyszukaj|google|otwórz|open|otworz|zapisz|save|zachowaj|wyjaśnij|explain|opisz)\b/i;

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

    this.lastAutoVoiceTranscript = '';
    this.lastAutoVoiceTs = 0;
    
    // Other elements
    this.timestampEl = document.getElementById('timestamp');
    this.statsTextEl = document.getElementById('statsText');
    this.btnMoveScreen = document.getElementById('btnMoveScreen');

    this.init();
  }

  handleTranscript(data) {
    this.transcriptViewer.update(data.text, data.is_final);
    this.maybeTriggerVoiceCommandAnalysis(data);
  }

  handleSelectionAnalysis(data) {
    if (!this.selectionPanel) return;
    this.selectionPanel.show(data.text || this.selectionPanel.lastText || '');
    this.selectionPanel.showResult(data);
  }

  async maybeTriggerVoiceCommandAnalysis(data) {
    if (!this.selectionPanel || !data || !data.is_final) return;

    const transcript = (data.text || '').trim();
    if (!transcript || !VOICE_COMMAND_REGEX.test(transcript)) return;

    // Avoid duplicate auto-triggering on repeated final transcript frames
    const now = Date.now();
    if (this.lastAutoVoiceTranscript === transcript && (now - this.lastAutoVoiceTs) < 3000) {
      return;
    }
    this.lastAutoVoiceTranscript = transcript;
    this.lastAutoVoiceTs = now;

    // If user is actively typing in the panel, do not override input.
    if (document.activeElement === this.selectionPanel.textarea) return;

    const contextText = await this.resolveVoiceContextText(transcript);
    this.selectionPanel.show(contextText);
    this.analyzeSelection(contextText);
  }

  async resolveVoiceContextText(transcript) {
    const panelText = (this.selectionPanel.lastText || this.selectionPanel.textarea?.value || '').trim();
    if (panelText.length >= 3) return panelText;

    try {
      const clip = (await navigator.clipboard.readText() || '').trim();
      if (clip.length >= 3) return clip;
    } catch (_) {
      // Clipboard may be unavailable when overlay is unfocused.
    }

    // Fallback: use transcript itself so voice command options can still render.
    return transcript;
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
    // Connection status is handled by <connection-status> component via window events

    // Data events from SSE server
    sseService.on('analysis', (data) => this.handleAnalysis(data));
    sseService.on('transcript', (data) => this.handleTranscript(data));
    sseService.on('window', (data) => this.windowContext.update(data));
    sseService.on('organized_screen', (data) => this.screenSummary.update(data));
    sseService.on('agent_actions', (data) => this.agentActions.update(data.actions));
    sseService.on('agent_result', (data) => this.agentActions.showResult(data));
    sseService.on('selection_analysis', (data) => this.handleSelectionAnalysis(data));
    sseService.on('skill_result', (data) => this.selectionPanel.showSkillResult(data));
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
    this.selectionPanel.addEventListener('execute-skill', (e) => this.executeSkill(e.detail));
    this.selectionPanel.addEventListener('input-focus', () => this.setFocus(true));
    this.selectionPanel.addEventListener('input-blur', () => this.setFocus(false));
    this.selectionPanel.addEventListener('panel-opened', () => this.setInteractive(true));
    this.selectionPanel.addEventListener('panel-closed', () => this.setInteractive(false));

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
      // Read system clipboard to send alongside selection for intent detection
      let clipboard_text = '';
      try {
        clipboard_text = await navigator.clipboard.readText();
      } catch (_) { /* clipboard read may fail without focus */ }

      const res = await fetch(`${BACKEND_URL}/analyze-selection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, clipboard_text }),
      });
      const data = await res.json();
      this.selectionPanel.showResult(data);
    } catch (e) {
      this.selectionPanel.showResult({ error: e.message });
    }
  }

  async executeSkill(detail) {
    const { skill, option_id, text } = detail;
    try {
      let clipboard_text = '';
      try {
        clipboard_text = await navigator.clipboard.readText();
      } catch (_) { /* clipboard read may fail without focus */ }

      const res = await fetch(`${BACKEND_URL}/skill/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ skill, option_id, text, clipboard_text }),
      });
      const data = await res.json();
      this.selectionPanel.showSkillResult(data);
    } catch (e) {
      this.selectionPanel.showSkillResult({ success: false, error: e.message });
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

  /**
   * Make entire overlay interactive (for selection panel, popups) or restore click-through.
   * This is the KEY fix: when panel is open, the whole window catches clicks.
   */
  setInteractive(interactive) {
    if (!window.electron) return;
    if (interactive) {
      window.electron.setIgnoreMouseEvents(false);
      window.electron.setFocusable(true);
    } else {
      window.electron.setIgnoreMouseEvents(true, { forward: true });
      window.electron.setFocusable(false);
    }
  }

  setupMouseForwarding() {
    if (!window.electron || !window.electron.setIgnoreMouseEvents) return;
    
    // When mouse enters any interactive area, disable click-through
    const interactiveSelectors = 'select, button, input, textarea, ocr-controls, agent-actions, selection-panel, .header, .footer, .resize-handle, .skill-option-btn, .btn-agent, .btn-analyze, .btn-close-panel, .btn-header';

    document.addEventListener('mouseover', (e) => {
      // If selection panel is visible, always interactive
      if (this.selectionPanel && this.selectionPanel.style.display !== 'none') return;
      if (e.target.closest(interactiveSelectors)) {
        window.electron.setIgnoreMouseEvents(false);
      }
    });

    document.addEventListener('mouseout', (e) => {
      // If selection panel is visible, stay interactive
      if (this.selectionPanel && this.selectionPanel.style.display !== 'none') return;
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
