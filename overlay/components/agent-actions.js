import { BaseComponent } from './base.js';

export class AgentActions extends BaseComponent {
  connectedCallback() {
    this.style.display = 'none';
    this.innerHTML = `
      <div class="agent-actions-container">
        <div class="agent-actions-header">
          <span class="agent-actions-title">🤖 Sugerowane akcje</span>
        </div>
        <div class="agent-actions-list"></div>
      </div>
    `;
    this.listEl = this.qs('.agent-actions-list');
    this.containerEl = this.qs('.agent-actions-container');
    
    // Add global styles for risk levels here or rely on main css
  }

  update(actions) {
    if (!actions || actions.length === 0) {
      this.style.display = 'none';
      return;
    }

    this.style.display = 'block';
    let html = '';
    
    for (const action of actions) {
      const riskClass = `risk-${action.risk}`;
      const riskLabel = action.risk === 'safe' ? '✅' : action.risk === 'low' ? '🟡' : action.risk === 'medium' ? '🟠' : '🔴';
      
      html += `
        <div class="agent-action-item ${riskClass}">
          <div class="agent-action-desc">${riskLabel} ${this.escapeHtml(action.description)}</div>
          <div class="agent-action-cmd"><code>${this.escapeHtml(action.command)}</code></div>
          <div class="agent-action-buttons">
            <button class="btn-agent btn-approve" data-id="${action.action_id}" title="Zatwierdź i wykonaj">▶ Wykonaj</button>
            <button class="btn-agent btn-copy" data-cmd="${this.escapeHtml(action.command)}" title="Kopiuj komendę">📋</button>
          </div>
        </div>
      `;
    }

    this.listEl.innerHTML = html;

    // Attach event listeners to new buttons
    this.qsa('.btn-approve').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.emit('approve-action', { id: e.target.dataset.id });
      });
    });

    this.qsa('.btn-copy').forEach(btn => {
      btn.addEventListener('click', (e) => {
        this.emit('copy-command', { command: e.target.dataset.cmd });
      });
    });

    // Auto-hide after 30 seconds
    setTimeout(() => {
      this.style.display = 'none';
    }, 30000);
  }

  showResult(data) {
    if (!data.executed) return;

    const statusEmoji = data.exit_code === 0 ? '✅' : '❌';
    const output = data.output ? data.output.substring(0, 200) : '(brak wyjścia)';

    const resultHtml = `
      <div class="agent-result">
        <div class="agent-result-header">${statusEmoji} ${this.escapeHtml(data.description)} (exit: ${data.exit_code})</div>
        <pre class="agent-result-output">${this.escapeHtml(output)}</pre>
      </div>
    `;
    
    this.listEl.innerHTML = resultHtml;
    this.style.display = 'block';

    setTimeout(() => {
      this.style.display = 'none';
    }, 15000);
  }
}

customElements.define('agent-actions', AgentActions);
