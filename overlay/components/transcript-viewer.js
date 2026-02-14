import { BaseComponent } from './base.js';

export class TranscriptViewer extends BaseComponent {
  connectedCallback() {
    this.style.display = 'none';
    this.innerHTML = `
      <div class="transcript-section">
        <div class="transcript-header">🎤 Transkrypcja</div>
        <div class="transcript-text"></div>
      </div>
    `;
    this.textEl = this.qs('.transcript-text');
    this.hideTimer = null;
  }

  update(text, isFinal) {
    this.style.display = 'block';
    this.textEl.textContent = text;

    if (!isFinal) {
      this.textEl.style.opacity = '0.7';
      if (this.hideTimer) clearTimeout(this.hideTimer);
    } else {
      this.textEl.style.opacity = '1';
      // Auto-hide after 5 seconds
      if (this.hideTimer) clearTimeout(this.hideTimer);
      this.hideTimer = setTimeout(() => {
        this.style.display = 'none';
      }, 5000);
    }
  }
}

customElements.define('transcript-viewer', TranscriptViewer);
