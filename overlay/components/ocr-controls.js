import { BaseComponent } from './base.js';

export class OCRControls extends BaseComponent {
  connectedCallback() {
    this.innerHTML = `
      <div class="ocr-controls">
        <div class="ocr-controls-header">
          <span class="ocr-controls-title">🔤 OCR / Tryb analizy</span>
          <button class="btn-benchmark" title="Benchmark OCR">⚡ Test</button>
        </div>
        <div class="ocr-selectors">
          <div class="selector-group">
            <label class="selector-label">Tryb</label>
            <select class="ocr-select mode-select">
              <option value="hybrid">Hybrid (OCR→LLM)</option>
              <option value="vision_only">Vision Only</option>
              <option value="ocr_only">OCR Only</option>
              <option value="ocr_plus_vision">OCR + Vision</option>
            </select>
          </div>
          <div class="selector-group">
            <label class="selector-label">OCR Engine</label>
            <select class="ocr-select engine-select">
              <option value="paddleocr">PaddleOCR</option>
              <option value="easyocr">EasyOCR</option>
              <option value="tesseract">Tesseract</option>
            </select>
          </div>
        </div>
        <div class="benchmark-results" style="display: none;"></div>
        <div class="ocr-info" style="display: none;">
          <span class="ocr-info-text"></span>
        </div>
      </div>
    `;

    this.modeSelect = this.qs('.mode-select');
    this.engineSelect = this.qs('.engine-select');
    this.benchmarkBtn = this.qs('.btn-benchmark');
    this.benchmarkResults = this.qs('.benchmark-results');
    this.ocrInfo = this.qs('.ocr-info');
    this.ocrInfoText = this.qs('.ocr-info-text');

    this.modeSelect.addEventListener('change', (e) => this.emit('mode-change', { mode: e.target.value }));
    this.engineSelect.addEventListener('change', (e) => this.emit('engine-change', { engine: e.target.value }));
    this.benchmarkBtn.addEventListener('click', () => this.emit('run-benchmark'));
  }

  setMode(mode) {
    if (this.modeSelect) this.modeSelect.value = mode;
  }

  setEngine(engine) {
    if (this.engineSelect) this.engineSelect.value = engine;
  }

  setAvailableEngines(engines) {
    if (!this.engineSelect || !engines) return;
    
    this.engineSelect.innerHTML = '';
    engines.forEach(eng => {
      const opt = document.createElement('option');
      opt.value = eng.engine;
      opt.textContent = eng.engine;
      if (eng.active) opt.selected = true;
      this.engineSelect.appendChild(opt);
    });
  }

  updateInfo(ocrData) {
    if (ocrData && ocrData.engine !== 'disabled') {
      this.ocrInfo.style.display = 'block';
      this.ocrInfoText.textContent = 
        `OCR: ${ocrData.engine} • ${ocrData.latency_ms.toFixed(0)}ms • ${(ocrData.confidence * 100).toFixed(0)}% • ${ocrData.boxes_count} box`;
    } else {
      this.ocrInfo.style.display = 'none';
    }
  }

  showBenchmarkResults(data) {
    const { engines, winners } = data;
    this.benchmarkResults.style.display = 'block';

    let html = '<div class="benchmark-title">Benchmark Results</div>';
    html += '<div class="benchmark-grid">';
    
    for (const [name, result] of Object.entries(engines)) {
      const isWinner = name === winners.fastest;
      const isMostConfident = name === winners.most_confident;
      const badges = [];
      if (isWinner) badges.push('<span class="badge badge-fast">fastest</span>');
      if (isMostConfident) badges.push('<span class="badge badge-conf">best conf</span>');
      
      html += `
        <div class="benchmark-item ${isWinner ? 'benchmark-winner' : ''}">
          <div class="benchmark-engine">${this.escapeHtml(name)} ${badges.join(' ')}</div>
          <div class="benchmark-stats">
            <span>${result.latency_ms.toFixed(0)}ms</span>
            <span>${(result.confidence * 100).toFixed(0)}%</span>
            <span>${result.boxes_count} box</span>
          </div>
          <div class="benchmark-preview">${this.escapeHtml(result.text_preview || '').substring(0, 80)}${(result.text_preview || '').length > 80 ? '...' : ''}</div>
        </div>
      `;
    }
    html += '</div>';

    this.benchmarkResults.innerHTML = html;

    // Auto-hide after 15 seconds
    setTimeout(() => {
      this.benchmarkResults.style.display = 'none';
    }, 15000);
  }

  setBenchmarkLoading(loading) {
    this.benchmarkBtn.disabled = loading;
    this.benchmarkBtn.textContent = loading ? '...' : '⚡ Test';
  }
}

customElements.define('ocr-controls', OCRControls);
