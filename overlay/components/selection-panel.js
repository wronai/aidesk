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
        <div class="clipboard-preview" style="display: none;">
          <span class="clipboard-preview-icon">📎</span>
          <span class="clipboard-preview-text"></span>
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

    this.clipPreview = this.qs('.clipboard-preview');
    this.clipPreviewText = this.qs('.clipboard-preview-text');

    this.lastClipboardText = '';
    this.lastText = '';

    // Event listeners
    this.closeBtn.addEventListener('click', () => this.hide());
    this.analyzeBtn.addEventListener('click', () => this.triggerAnalysis());
    this.copyBtn.addEventListener('click', () => this.doCopy());

    this.textarea.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.triggerAnalysis();
      }
    });

    this.textarea.addEventListener('focus', () => this.emit('input-focus'));
    this.textarea.addEventListener('blur', () => this.emit('input-blur'));

    // Delegate clicks on skill option buttons (dynamically created)
    this.responseEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.skill-option-btn');
      if (btn) {
        const skill = btn.dataset.skill;
        const optionId = btn.dataset.optionId;
        if (skill && optionId) {
          this.emit('execute-skill', { skill, option_id: optionId, text: this.lastText });
          btn.disabled = true;
          btn.textContent = '⏳...';
        }
      }
    });
  }

  show(text = '') {
    this.style.display = 'block';
    this.emit('panel-opened');
    if (text) {
      this.textarea.value = text;
      this.lastText = text;
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
      this.lastText = text;
      this.emit('analyze', { text });
      this.analyzeBtn.disabled = true;
      this.analyzeBtn.textContent = '⏳...';
      this._updateClipboardPreview();
    }
  }

  async _updateClipboardPreview() {
    try {
      const clip = await navigator.clipboard.readText();
      if (clip && clip.trim() && clip.trim() !== this.lastText) {
        const preview = clip.trim().substring(0, 80).replace(/\n/g, ' ↵ ');
        this.clipPreviewText.textContent = preview + (clip.length > 80 ? '…' : '');
        this.clipPreview.style.display = 'flex';
      } else {
        this.clipPreview.style.display = 'none';
      }
    } catch {
      this.clipPreview.style.display = 'none';
    }
  }

  /**
   * Show skill-based analysis result with clickable option buttons.
   */
  showResult(data) {
    this.analyzeBtn.disabled = false;
    this.analyzeBtn.textContent = '▶ Analizuj';

    if (data.error) {
      this.responseEl.innerHTML = `<div class="error-message">⚠️ ${this.escapeHtml(data.error)}</div>`;
      return;
    }

    // New skill-based format: matches with options
    if (data.matches && data.matches.length > 0) {
      this.lastText = data.text || this.lastText;
      let html = '';

      for (const match of data.matches.slice(0, 3)) {
        html += `<div class="skill-match">`;
        html += `<div class="skill-match-header">`;
        html += `<span class="skill-match-icon">${match.icon || '🔧'}</span>`;
        html += `<span class="skill-match-label">${this.escapeHtml(match.label)}</span>`;
        html += `<span class="skill-match-conf">${Math.round(match.confidence * 100)}%</span>`;
        html += `</div>`;

        if (match.options && match.options.length > 0) {
          html += `<div class="skill-options">`;
          for (const opt of match.options) {
            const riskClass = 'risk-' + (opt.risk || 'safe');
            html += `<button class="skill-option-btn ${riskClass}" `;
            html += `data-skill="${this.escapeHtml(match.skill)}" `;
            html += `data-option-id="${this.escapeHtml(opt.id)}" `;
            html += `title="${this.escapeHtml(opt.description || '')}">`;
            html += `${opt.icon || ''} ${this.escapeHtml(opt.label)}`;
            html += `</button>`;
          }
          html += `</div>`;
        }
        html += `</div>`;
      }

      this.responseEl.innerHTML = html;
      this.clipboardContainer.style.display = 'none';
    } else if (data.response) {
      // Legacy format fallback
      this.responseEl.innerHTML = this.formatMarkdown(data.response);
      this.lastClipboardText = data.clipboard_text || '';
      this.clipboardContainer.style.display = this.lastClipboardText ? 'flex' : 'none';
    } else {
      this.responseEl.innerHTML = '<div style="color:#888">Brak dopasowanych umiejętności</div>';
      this.clipboardContainer.style.display = 'none';
    }
  }

  /**
   * Show result after executing a skill option.
   */
  showSkillResult(data) {
    let html = '';
    const emoji = data.success ? '✅' : '❌';
    html += `<div class="skill-result-msg">${emoji} ${this.formatMarkdown(data.message || '')}</div>`;

    if (data.output) {
      html += `<pre class="skill-result-output">${this.escapeHtml(data.output.substring(0, 1500))}</pre>`;
    }

    if (data.error && !data.success) {
      html += `<div class="error-message">⚠️ ${this.escapeHtml(data.error)}</div>`;
    }

    this.responseEl.innerHTML = html;

    this.lastClipboardText = data.clipboard_text || '';
    if (this.lastClipboardText) {
      this.clipboardContainer.style.display = 'flex';
      this.copyBtn.textContent = '📋 Kopiuj';
    }

    // Auto-open URL if provided
    if (data.open_url) {
      window.open(data.open_url, '_blank');
    }
  }

  doCopy() {
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
