/**
 * Base Web Component class with helper methods.
 */
export class BaseComponent extends HTMLElement {
  constructor() {
    super();
    this.elements = {};
  }

  /**
   * Helper to select elements within this component
   */
  qs(selector) {
    return this.querySelector(selector);
  }

  /**
   * Helper to select all elements within this component
   */
  qsa(selector) {
    return this.querySelectorAll(selector);
  }

  /**
   * Dispatch a custom event
   */
  emit(name, detail = {}) {
    this.dispatchEvent(new CustomEvent(name, {
      detail,
      bubbles: true,
      composed: true
    }));
  }

  /**
   * Escape HTML to prevent XSS
   */
  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}
