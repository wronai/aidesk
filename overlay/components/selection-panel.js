import { BaseComponent } from './base.js';

export class SelectionPanel extends BaseComponent {
  connectedCallback() {
    this.style.display = 'none';
    this.innerHTML = `
      <div class="selection-panel">
        <div class="selection-panel-header">
          <span class="selection-panel-title">🔍 Analiza zaznaczenia</span>
          <button class="btn-close-panel" title="Zamknij">✕</button>
        </div>
        <div class="selection-panel-input">
          <textarea class="selection-textarea" placeholder="Wklej tekst do analizy lub użyj Ctrl+Shift+S..." rows="3"></textarea>
          <button class="btn-analyze" title="Analizuj">▶ Analizuj</button>
        </div>
        <div class="selection-panel-response"></div>
        <div class="selection-panel-clipboard" style="display: none;">
          <button class="btn-agent btn-copy" title="Kopiuj do schowka">📋 Kopiuj</button>
        </div>
      </div>
    `;

    this.textarea = this.qs('.selection-textarea');
    this.responseEl = this.qs('.selection-panel-response');
    this.analyzeBtn = this.qs('.btn-analyze');
    this.closeBtn = this.qs('.btn-close-panel');
    this.copyBtn = this.qs('.btn-copy');
    this.clipboardContainer = this.qs('.selection-panel-clipboard');
    
    this.lastClipboardText = '';

    // Event listeners
    this.closeBtn.addEventListener('click', () => this.hide());
    this.analyzeBtn.addEventListener('click', () => this.triggerAnalysis());
    this.copyBtn.addEventListener('click', () => this.copyToClipboard());
    
    this.textarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.triggerAnalysis();
      }
    });

    // Handle focus events for Electron bridge interaction
    this.textarea.addEventListener('focus', () => this.emit('input-focus'));
    this.textarea.addEventListener('blur', () => this.emit('input-blur'));
  }

  show(text = '') {
    this.style.display = 'block';
    if (text) {
      this.textarea.value = text;
      // If auto-showing with text, maybe trigger analysis automatically?
      // Or just clear previous response
      this.responseEl.innerHTML = '<div class="loading"><div class="loading-spinner"></div></div>';
      this.clipboardContainer.style.display = 'none';
    }
  }

  hide() {
    this.style.display = 'none';
    this.emit('panel-closed');
  }

  triggerAnalysis() {
    const text = this.textarea.value.trim();
    if (text) {
      this.emit('analyze', { text });
      this.analyzeBtn.disabled = true;
      this.analyzeBtn.textContent = '...';
    }
  }

  showResult(data) {
    this.analyzeBtn.disabled = false;
    this.analyzeBtn.textContent = '▶ Analizuj';
    
    if (data.error) {
      this.responseEl.innerHTML = `<div class="error-message">⚠️ ${this.escapeHtml(data.error)}</div>`;
      return;
    }

    const html = this.formatMarkdown(data.response || 'Brak odpowiedzi');
    this.responseEl.innerHTML = html;

    this.lastClipboardText = data.clipboard_text || '';
    if (this.lastClipboardText) {
      this.clipboardContainer.style.display = 'flex';
      this.copyBtn.textContent = '📋 Kopiuj';
    } else {
      this.clipboardContainer.style.display = 'none';
    }
  }

  copyToClipboard() {
    if (this.lastClipboardText) {
      navigator.clipboard.writeText(this.lastClipboardText);
      this.copyBtn.textContent = '✅ Skopiowano';
      setTimeout(() => { this.copyBtn.textContent = '📋 Kopiuj'; }, 2000);
    }
  }

  formatMarkdown(text) {
    let html = this.escapeHtml(text);
    html = html.replace(/```([\s\S]*?)```/g, '<pre>$1</pre>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\n/g, '<br>');
    return html;
  }
}

customElements.define('selection-panel', SelectionPanel);
