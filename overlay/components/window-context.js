import { BaseComponent } from './base.js';

const CATEGORY_EMOJI = {
  ide: '💻', terminal: '🖥️', browser: '🌐', email: '📧',
  chat: '💬', office: '📄', media: '🎨', file_manager: '📁',
  graphics: '🖌️', game: '🎮', utility: '🛠️',
  system: '⚙️', unknown: '📝',
};

export class WindowContext extends BaseComponent {
  connectedCallback() {
    this.style.display = 'none';
    this.innerHTML = `
      <div class="window-context">
        <div class="window-context-app">
          <span class="window-context-emoji">📝</span>
          <span class="window-context-name">—</span>
          <span class="window-context-category badge">—</span>
        </div>
        <div class="window-context-detail"></div>
      </div>
    `;
    
    this.emojiEl = this.qs('.window-context-emoji');
    this.nameEl = this.qs('.window-context-name');
    this.categoryEl = this.qs('.window-context-category');
    this.detailEl = this.qs('.window-context-detail');
  }

  update(data) {
    this.style.display = 'block';
    
    const category = data.category || 'unknown';
    const appName = data.wm_class_name || data.title || '—';
    const emoji = CATEGORY_EMOJI[category] || '📝';

    this.emojiEl.textContent = emoji;
    this.nameEl.textContent = appName;
    this.categoryEl.textContent = category;
    this.categoryEl.className = `window-context-category badge badge-cat-${category}`;

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
    
    this.detailEl.textContent = detail;
    this.detailEl.style.display = detail ? 'block' : 'none';
  }
}

customElements.define('window-context', WindowContext);
