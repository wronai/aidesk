import { BaseComponent } from './base.js';

const CATEGORY_EMOJI = {
  ide: '💻', terminal: '🖥️', browser: '🌐', email: '📧',
  chat: '💬', office: '📄', media: '🎨', file_manager: '📁',
  system: '⚙️', unknown: '📝',
};

export class ScreenSummary extends BaseComponent {
  connectedCallback() {
    this.style.display = 'none';
    this.innerHTML = `
      <div class="screen-summary">
        <span class="screen-summary-icon">📊</span>
        <span class="screen-summary-text"></span>
        <span class="screen-summary-count"></span>
      </div>
    `;
    this.textEl = this.qs('.screen-summary-text');
    this.countEl = this.qs('.screen-summary-count');
  }

  update(data) {
    this.style.display = 'flex';

    if (data.summary) {
      this.textEl.textContent = data.summary;
    }

    if (data.total_windows !== undefined) {
      const cats = (data.categories || []).map(c => CATEGORY_EMOJI[c] || c).join(' ');
      this.countEl.textContent = `${data.total_windows} okien ${cats}`;
    }
  }
}

customElements.define('screen-summary', ScreenSummary);
