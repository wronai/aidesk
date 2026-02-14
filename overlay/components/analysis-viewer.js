import { BaseComponent } from './base.js';

export class AnalysisViewer extends BaseComponent {
  connectedCallback() {
    this.innerHTML = `
      <div class="analysis-section">
        <div class="analysis-header">🖥️ Analiza ekranu</div>
        <div class="analysis-content">
          <div class="loading">
            <div class="loading-spinner"></div>
            <div class="loading-text">Czekam na zmiany ekranu...</div>
          </div>
        </div>
      </div>
    `;
    this.contentEl = this.qs('.analysis-content');
  }

  update(text, isError = false) {
    if (isError) {
      this.contentEl.innerHTML = `
        <div class="error-message">
          ⚠️ ${this.escapeHtml(text)}
        </div>
      `;
      return;
    }

    this.contentEl.classList.add('updating');
    
    // Smooth update transition
    setTimeout(() => {
      this.contentEl.innerHTML = this.formatAnalysisText(text);
      this.contentEl.classList.remove('updating');
    }, 150);
  }

  formatAnalysisText(text) {
    try {
      const json = JSON.parse(text);
      let html = '';
      
      if (json.app) {
        html += `<div style="font-weight: 600; margin-bottom: 8px;">📱 ${this.escapeHtml(json.app)}</div>`;
      }
      
      if (json.task) {
        html += `<div style="margin-bottom: 10px; color: #a3a3a3;">${this.escapeHtml(json.task)}</div>`;
      }
      
      if (json.suggestions && json.suggestions.length > 0) {
        html += '<div style="margin-top: 10px;">';
        json.suggestions.forEach(suggestion => {
          html += `<div style="margin-bottom: 6px;">💡 ${this.escapeHtml(suggestion)}</div>`;
        });
        html += '</div>';
      }
      
      if (json.summary) {
        html += `<div style="margin-top: 10px; font-style: italic; color: #888;">${this.escapeHtml(json.summary)}</div>`;
      }

      if (json.priority) {
        const priorityClass = `priority-${json.priority}`;
        html = `<div class="${priorityClass}">${html}</div>`;
      }
      
      return html || this.escapeHtml(text);
    } catch (e) {
      return this.escapeHtml(text).replace(/\n/g, '<br>');
    }
  }
}

customElements.define('analysis-viewer', AnalysisViewer);
