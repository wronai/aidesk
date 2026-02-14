import { BaseComponent } from './base.js';

export class ConnectionStatus extends BaseComponent {
  connectedCallback() {
    this.innerHTML = `
      <div class="status-indicator" title="Connecting...">
        <div class="status-dot"></div>
      </div>
    `;
    this.indicator = this.qs('.status-indicator');
    
    // Listen for global SSE events
    window.addEventListener('sse:connecting', () => this.update('connecting'));
    window.addEventListener('sse:connected', () => this.update('connected'));
    window.addEventListener('sse:error', () => this.update('error'));
  }

  update(status) {
    this.indicator.className = 'status-indicator';
    
    switch (status) {
      case 'connected':
        this.indicator.classList.add('connected');
        this.indicator.title = 'Połączono';
        break;
      case 'connecting':
        this.indicator.title = 'Łączenie...';
        break;
      case 'error':
        this.indicator.classList.add('error');
        this.indicator.title = 'Błąd połączenia';
        break;
      case 'warning':
        this.indicator.classList.add('warning');
        break;
    }
  }
  
  setWarning(message) {
    this.update('warning');
    this.indicator.title = message;
    setTimeout(() => this.update('connected'), 5000);
  }
}

customElements.define('connection-status', ConnectionStatus);
