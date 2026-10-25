/**
 * AgentX Embeddable Chat Widget
 * 
 * Complies with AgentX Integration & Deployment Specification:
 * - Priority 3: Global Scope, Navigation Persistence, State Hygiene, Singleton Guard
 * - Priority 4: sessionStorage Architecture, Centralized Orchestration, Backend Teardown, Context Isolation
 */
(function () {
  'use strict';

  // 1. Singleton Guard
  if (window.__AGENTX_CHAT_WIDGET_INITIALIZED__) {
    return;
  }
  window.__AGENTX_CHAT_WIDGET_INITIALIZED__ = true;

  // Storage Keys (sessionStorage ensures tab-scoped isolation without leaking across sessions)
  const STORAGE_KEYS = {
    SESSION_KEY: 'agentx_chat_session_key',
    IS_OPEN: 'agentx_chat_open',
    MESSAGES: 'agentx_chat_messages',
    TRIGGER_POS: 'agentx_chat_trigger_pos',
    POPUP_POS: 'agentx_chat_popup_pos',
  };

  // Helper: Generate UUIDv4 for session key
  function generateSessionKey() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return 'web_' + crypto.randomUUID();
    }
    return 'web_' + 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  // Find the executing script tag
  const scriptTag =
    document.currentScript ||
    document.querySelector('script[src*="agentx-chat-widget.js"]') ||
    document.querySelector('script[src*="dograh-widget.js"]');

  if (!scriptTag) {
    console.warn('AgentX Chat Widget: Script tag could not be identified.');
    return;
  }

  const scriptUrl = new URL(scriptTag.src, window.location.href);
  const token = scriptUrl.searchParams.get('token') || scriptTag.getAttribute('data-token') || '';
  let apiEndpoint = scriptUrl.searchParams.get('apiEndpoint') || scriptTag.getAttribute('data-api-endpoint') || '';

  if (!apiEndpoint || apiEndpoint.includes('host.docker.internal')) {
    apiEndpoint = window.location.origin.includes('localhost')
      ? 'http://localhost:3000'
      : window.location.origin;
  }
  apiEndpoint = apiEndpoint.replace(/\/+$/, '');

  // Global Widget State
  const state = {
    token: token,
    apiEndpoint: apiEndpoint,
    sessionKey: '',
    isOpen: false,
    isLoading: false,
    messages: [],
    config: {
      title: scriptUrl.searchParams.get('title') || scriptTag.getAttribute('data-title') || 'Chat Agent',
      subtitle: scriptUrl.searchParams.get('subtitle') || scriptTag.getAttribute('data-subtitle') || 'Chat Agent • Online',
      buttonColor: scriptUrl.searchParams.get('buttonColor') || scriptTag.getAttribute('data-button-color') || '#2563eb',
      buttonText: scriptUrl.searchParams.get('buttonText') || scriptTag.getAttribute('data-button-text') || 'Chat with Agent',
      position: scriptUrl.searchParams.get('position') || scriptTag.getAttribute('data-position') || 'bottom-right',
    },
  };

  // 2. Storage & Memory Management (Priority 4)
  function initOrRestoreSession() {
    try {
      const storedSessionKey = sessionStorage.getItem(STORAGE_KEYS.SESSION_KEY);
      const storedIsOpen = sessionStorage.getItem(STORAGE_KEYS.IS_OPEN) === 'true';
      const storedMessages = sessionStorage.getItem(STORAGE_KEYS.MESSAGES);

      if (storedSessionKey) {
        state.sessionKey = storedSessionKey;
        state.isOpen = storedIsOpen;
        state.messages = storedMessages ? JSON.parse(storedMessages) : [];
      } else {
        // Brand new session
        state.sessionKey = generateSessionKey();
        sessionStorage.setItem(STORAGE_KEYS.SESSION_KEY, state.sessionKey);
        state.messages = [];
        state.isOpen = false;
      }
      try {
        sessionStorage.removeItem(STORAGE_KEYS.POPUP_POS);
      } catch (e) {}
    } catch (e) {
      state.sessionKey = generateSessionKey();
      state.messages = [];
      state.isOpen = false;
    }
  }

  function persistSessionState() {
    try {
      sessionStorage.setItem(STORAGE_KEYS.SESSION_KEY, state.sessionKey);
      sessionStorage.setItem(STORAGE_KEYS.IS_OPEN, state.isOpen ? 'true' : 'false');
      sessionStorage.setItem(STORAGE_KEYS.MESSAGES, JSON.stringify(state.messages));
    } catch (e) {
      // Ignore quota/private mode errors
    }
  }

  // 3. Backend Teardown Protocol (Priority 4)
  function performBackendTeardown(sessionKeyToTerminate) {
    if (!sessionKeyToTerminate) return;

    const teardownUrl = `${state.apiEndpoint}/api/v1/public/embed/chat/teardown`;
    const payload = JSON.stringify({
      token: state.token,
      session_key: sessionKeyToTerminate,
    });

    if (navigator.sendBeacon) {
      const blob = new Blob([payload], { type: 'application/json' });
      navigator.sendBeacon(teardownUrl, blob);
    } else {
      fetch(teardownUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      }).catch(function (err) {
        console.debug('Teardown report failed:', err);
      });
    }
  }

  // Reset & terminate session (State Hygiene)
  function terminateAndResetSession() {
    const oldSessionKey = state.sessionKey;
    performBackendTeardown(oldSessionKey);

    // Clear client-side storage completely
    try {
      sessionStorage.removeItem(STORAGE_KEYS.SESSION_KEY);
      sessionStorage.removeItem(STORAGE_KEYS.IS_OPEN);
      sessionStorage.removeItem(STORAGE_KEYS.MESSAGES);
    } catch (e) {}

    // Instantiate fresh, isolated context for next opening
    state.sessionKey = generateSessionKey();
    state.messages = [];
    state.isOpen = false;
    state.isLoading = false;
  }

  // 4. UI Rendering & Styles
  function injectStyles() {
    if (document.getElementById('agentx-chat-widget-styles')) return;

    const style = document.createElement('style');
    style.id = 'agentx-chat-widget-styles';
    style.textContent = `
      #agentx-chat-widget-root {
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 999999;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        touch-action: none;
      }
      #agentx-chat-widget-root.is-dragging {
        transition: none !important;
      }
      #agentx-chat-widget-root.pos-bottom-left {
        right: auto;
        left: 24px;
      }
      #agentx-chat-widget-root:not(.pos-bottom-left).has-dograh-voice {
        bottom: 84px;
      }

      /* Floating Trigger Pill Button (Matches Configuration tab preview & Talk to Agent style) */
      .agentx-chat-trigger {
        position: relative;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        border-radius: 9999px;
        padding: 12px 20px;
        background: #2563eb;
        color: #ffffff;
        font-family: inherit;
        font-size: 14px;
        font-weight: 600;
        line-height: 1;
        white-space: nowrap;
        box-shadow: 0 10px 25px -4px rgba(37, 99, 235, 0.45), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        border: none;
        cursor: grab;
        transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.2s ease, background 0.2s ease;
        user-select: none;
        -webkit-user-select: none;
        -webkit-user-drag: none;
        touch-action: none;
      }
      .agentx-chat-trigger:hover {
        transform: translateY(-2px) scale(1.02);
        box-shadow: 0 14px 28px -4px rgba(37, 99, 235, 0.55), 0 6px 12px -2px rgba(0, 0, 0, 0.1);
      }
      .agentx-chat-trigger:active,
      #agentx-chat-widget-root.is-dragging .agentx-chat-trigger {
        cursor: grabbing !important;
        transform: scale(0.98);
      }

      /* Online Status Badge on Trigger */
      .agentx-trigger-badge {
        position: absolute;
        top: -1px;
        right: -1px;
        width: 11px;
        height: 11px;
        border-radius: 50%;
        background: #10b981;
        border: 2px solid #ffffff;
        box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
        animation: agentx-badge-pulse 2.2s infinite;
        pointer-events: none;
      }
      @keyframes agentx-badge-pulse {
        0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
        70% { box-shadow: 0 0 0 7px rgba(16, 185, 129, 0); }
        100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
      }

      .agentx-pill-icon {
        width: 16px;
        height: 16px;
        stroke: currentColor;
        stroke-width: 2;
        fill: none;
        stroke-linecap: round;
        stroke-linejoin: round;
        flex-shrink: 0;
      }
      .agentx-pill-text {
        font-size: 14px;
        font-weight: 600;
        color: #ffffff;
        letter-spacing: -0.01em;
      }

      /* Chat Popup Window - Permanently anchored to and follows widget */
      .agentx-chat-popup {
        position: absolute;
        bottom: calc(100% + 14px);
        right: 0;
        left: auto;
        width: 390px;
        max-width: calc(100vw - 32px);
        height: 600px;
        max-height: calc(100vh - 120px);
        background: #ffffff;
        border-radius: 24px;
        box-shadow: 0 25px 60px -10px rgba(15, 23, 42, 0.35), 0 0 0 1px rgba(15, 23, 42, 0.08);
        display: flex;
        flex-direction: column;
        overflow: hidden;
        transform-origin: bottom right;
        transition: opacity 0.25s ease, transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        z-index: 999999;
        touch-action: none;
      }
      .agentx-chat-popup.is-dragging {
        transition: none !important;
        box-shadow: 0 32px 72px -12px rgba(15, 23, 42, 0.45), 0 0 0 1px rgba(15, 23, 42, 0.12) !important;
      }
      #agentx-chat-widget-root.pos-bottom-left .agentx-chat-popup,
      #agentx-chat-widget-root.dock-left .agentx-chat-popup {
        right: auto;
        left: 0;
        transform-origin: bottom left;
      }
      #agentx-chat-widget-root.dock-right .agentx-chat-popup {
        left: auto;
        right: 0;
        transform-origin: bottom right;
      }
      #agentx-chat-widget-root.dock-top .agentx-chat-popup {
        bottom: auto;
        top: calc(100% + 14px);
        transform-origin: top right;
      }
      #agentx-chat-widget-root.dock-top.pos-bottom-left .agentx-chat-popup,
      #agentx-chat-widget-root.dock-top.dock-left .agentx-chat-popup {
        right: auto;
        left: 0;
        transform-origin: top left;
      }
      .agentx-chat-popup.agentx-hidden {
        opacity: 0;
        pointer-events: none;
        transform: scale(0.92) translateY(16px);
      }

      /* Chat Header */
      .agentx-chat-header {
        background: linear-gradient(135deg, #091224 0%, #111d38 60%, #1e293b 100%);
        color: #ffffff;
        padding: 10px 18px 14px 18px;
        display: flex;
        flex-direction: column;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        cursor: grab;
        user-select: none;
        -webkit-user-select: none;
        touch-action: none;
        position: relative;
      }
      .agentx-chat-header:active,
      .agentx-chat-popup.is-dragging .agentx-chat-header {
        cursor: grabbing !important;
      }
      .agentx-drag-handle {
        width: 36px;
        height: 4px;
        background: rgba(255, 255, 255, 0.25);
        border-radius: 99px;
        margin: 0 auto 10px auto;
        transition: background 0.2s;
      }
      .agentx-chat-header:hover .agentx-drag-handle {
        background: rgba(255, 255, 255, 0.45);
      }
      .agentx-header-content {
        display: flex;
        align-items: center;
        justify-content: space-between;
        width: 100%;
      }
      .agentx-header-info {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .agentx-avatar-wrap {
        position: relative;
      }
      .agentx-avatar {
        width: 40px;
        height: 40px;
        border-radius: 12px;
        background: linear-gradient(135deg, #3b82f6 0%, #6366f1 50%, #8b5cf6 100%);
        box-shadow: 0 4px 14px rgba(59, 130, 246, 0.4);
        display: flex;
        align-items: center;
        justify-content: center;
        color: #ffffff;
      }
      .agentx-avatar-dot {
        position: absolute;
        bottom: -1px;
        right: -1px;
        width: 10px;
        height: 10px;
        background: #10b981;
        border: 2px solid #091224;
        border-radius: 50%;
      }
      .agentx-header-text h4 {
        margin: 0;
        font-size: 15px;
        font-weight: 700;
        color: #ffffff;
        letter-spacing: -0.01em;
      }
      .agentx-header-text p {
        margin: 2px 0 0;
        font-size: 12px;
        color: #94a3b8;
        display: flex;
        align-items: center;
        gap: 5px;
      }
      .agentx-header-actions {
        display: flex;
        align-items: center;
        gap: 4px;
      }
      .agentx-header-btn {
        background: transparent;
        border: none;
        color: #94a3b8;
        cursor: pointer;
        padding: 7px;
        border-radius: 9px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: color 0.15s, background 0.15s;
      }
      .agentx-header-btn:hover {
        color: #ffffff;
        background: rgba(255, 255, 255, 0.12);
      }

      /* Messages Stream */
      .agentx-chat-messages {
        flex: 1;
        padding: 16px;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 14px;
        background: #f8fafc;
        scrollbar-width: thin;
        scrollbar-color: #cbd5e1 transparent;
      }
      .agentx-chat-messages::-webkit-scrollbar {
        width: 6px;
      }
      .agentx-chat-messages::-webkit-scrollbar-thumb {
        background: #cbd5e1;
        border-radius: 4px;
      }

      /* Welcome Card & Chips */
      .agentx-welcome-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 18px;
        padding: 18px;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.04);
        display: flex;
        flex-direction: column;
        gap: 12px;
        animation: agentx-fade-in 0.3s ease-out;
      }
      .agentx-welcome-badge {
        width: 32px;
        height: 32px;
        border-radius: 8px;
        background: linear-gradient(135deg, #2563eb, #6366f1);
        display: flex;
        align-items: center;
        justify-content: center;
        color: #ffffff;
      }
      .agentx-welcome-title {
        font-size: 15px;
        font-weight: 700;
        color: #0f172a;
        margin: 0;
      }
      .agentx-welcome-desc {
        font-size: 13px;
        color: #64748b;
        line-height: 1.45;
        margin: 0;
      }
      .agentx-chips-wrap {
        display: flex;
        flex-direction: column;
        gap: 7px;
        margin-top: 4px;
      }
      .agentx-chip-btn {
        background: #f1f5f9;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 9px 12px;
        font-size: 12.5px;
        font-weight: 500;
        color: #1e293b;
        text-align: left;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: space-between;
        transition: all 0.16s ease;
      }
      .agentx-chip-btn:hover {
        background: #ffffff;
        border-color: #3b82f6;
        color: #2563eb;
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.12);
      }
      .agentx-chip-btn svg {
        opacity: 0.4;
        transition: opacity 0.16s, transform 0.16s;
      }
      .agentx-chip-btn:hover svg {
        opacity: 1;
        transform: translateX(2px);
      }

      /* Messages */
      .agentx-msg-row {
        display: flex;
        flex-direction: column;
        max-width: 84%;
        animation: agentx-fade-in 0.25s ease-out;
      }
      .agentx-msg-row.agentx-row-user {
        align-self: flex-end;
      }
      .agentx-msg-row.agentx-row-assistant {
        align-self: flex-start;
      }
      .agentx-msg {
        padding: 11px 15px;
        font-size: 14px;
        line-height: 1.5;
        word-break: break-word;
      }
      .agentx-row-user .agentx-msg {
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
        color: #ffffff;
        border-radius: 18px 18px 4px 18px;
        box-shadow: 0 4px 14px rgba(37, 99, 235, 0.28);
      }
      .agentx-row-assistant .agentx-msg {
        background: #ffffff;
        color: #0f172a;
        border: 1px solid #e2e8f0;
        border-radius: 18px 18px 18px 4px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
      }
      .agentx-msg-time {
        font-size: 10px;
        color: #94a3b8;
        margin-top: 4px;
        padding: 0 4px;
      }
      .agentx-row-user .agentx-msg-time {
        text-align: right;
      }
      .agentx-msg a {
        color: #2563eb;
        text-decoration: underline;
        font-weight: 500;
      }
      .agentx-row-user .agentx-msg a {
        color: #ffffff;
        text-decoration: underline;
      }

      /* Typing Indicator */
      .agentx-typing {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 12px 16px;
        align-self: flex-start;
        background: #ffffff;
        border-radius: 18px 18px 18px 4px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        color: #64748b;
        font-size: 12px;
        font-weight: 500;
        animation: agentx-fade-in 0.25s ease-out;
      }
      .agentx-typing-dots {
        display: flex;
        gap: 4px;
        align-items: center;
      }
      .agentx-typing-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #3b82f6;
        animation: agentx-bounce 1.4s infinite ease-in-out;
      }
      .agentx-typing-dot:nth-child(2) { animation-delay: 0.2s; }
      .agentx-typing-dot:nth-child(3) { animation-delay: 0.4s; }
      @keyframes agentx-bounce {
        0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
        40% { transform: scale(1.15); opacity: 1; }
      }
      @keyframes agentx-fade-in {
        from { opacity: 0; transform: translateY(6px); }
        to { opacity: 1; transform: translateY(0); }
      }

      /* Footer & Input Bar */
      .agentx-chat-footer {
        padding: 12px 14px 10px 14px;
        background: #ffffff;
        border-top: 1px solid #f1f5f9;
        display: flex;
        flex-direction: column;
        gap: 7px;
        box-sizing: border-box;
      }
      .agentx-input-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        background: #f1f5f9;
        border: 1.5px solid #e2e8f0;
        border-radius: 28px;
        padding: 4px 6px 4px 14px;
        transition: all 0.2s ease;
      }
      .agentx-input-bar:focus-within {
        background: #ffffff;
        border-color: #3b82f6;
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.16);
      }
      .agentx-input {
        flex: 1;
        border: none;
        background: transparent;
        font-size: 14px;
        line-height: 1.4;
        color: #0f172a;
        caret-color: #2563eb;
        outline: none;
        font-family: inherit;
        padding: 6px 0;
      }
      .agentx-input::placeholder {
        color: #94a3b8;
      }
      .agentx-send-btn {
        width: 34px;
        height: 34px;
        border-radius: 50%;
        background: linear-gradient(135deg, #2563eb, #4f46e5);
        border: none;
        color: #ffffff;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 8px rgba(37, 99, 235, 0.35);
        transition: transform 0.15s ease, opacity 0.15s ease, background 0.2s;
      }
      .agentx-send-btn:hover:not(:disabled) {
        transform: scale(1.06);
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.45);
      }
      .agentx-send-btn:disabled {
        background: #cbd5e1;
        cursor: not-allowed;
        box-shadow: none;
        opacity: 0.6;
      }
      .agentx-footer-watermark {
        font-size: 10.5px;
        color: #94a3b8;
        text-align: center;
        font-weight: 500;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 4px;
      }
    `;
    document.head.appendChild(style);
  }

  // Linkify helper with Navigation Persistence (opens in same tab without tearing down widget)
  function formatMessageText(text) {
    if (!text) return '';

    // Escape basic HTML to prevent XSS
    let escaped = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // 1. Extract markdown links [label](url) and replace with placeholder tokens
    const mdTokens = [];
    escaped = escaped.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (match, label, rawUrl) {
      const idx = mdTokens.length;
      let url = (rawUrl || '').trim();

      // Normalize portfolio links directly to /portifolio
      if (url.includes('portifolio') || url.includes('portfolio')) {
        url = '/portifolio';
      } else if (/^localhost(\/|:)/i.test(url)) {
        url = 'http://' + url;
      }

      mdTokens.push({ label: label, url: url });
      return `___AGENTX_MD_TOKEN_${idx}___`;
    });

    // 2. Linkify raw URLs (protocol or bare localhost) that are NOT part of markdown links
    escaped = escaped.replace(/(https?:\/\/[^\s<]+)/g, function (rawUrl) {
      let url = rawUrl.trim();
      let label = url;
      if (url.includes('portifolio') || url.includes('portfolio')) {
        url = '/portifolio';
        label = 'Portfolio';
      }
      return `<a href="${url}" class="agentx-internal-link" target="_self">${label}</a>`;
    });

    escaped = escaped.replace(/(?<!["'/])(localhost(?::\d+)?\/[^\s<]+)/g, function (rawUrl) {
      let url = rawUrl.trim();
      let label = url;
      if (url.includes('portifolio') || url.includes('portfolio')) {
        url = '/portifolio';
        label = 'Portfolio';
      } else {
        url = 'http://' + url;
      }
      return `<a href="${url}" class="agentx-internal-link" target="_self">${label}</a>`;
    });

    // 3. Restore markdown links from tokens
    escaped = escaped.replace(/___AGENTX_MD_TOKEN_(\d+)___/g, function (match, idxStr) {
      const token = mdTokens[parseInt(idxStr, 10)];
      if (!token) return '';
      return `<a href="${token.url}" class="agentx-internal-link" target="_self">${token.label}</a>`;
    });

    return escaped.replace(/\n/g, '<br/>');
  }

  /**
   * Helper to make floating elements freely draggable across the viewport.
   * Handles mouse and touch gestures, boundary clamping, and suppresses click events on drag.
   */
  function makeDraggable(element, handle, options) {
    options = options || {};
    handle = handle || element;
    let isPointerDown = false;
    let isDragging = false;
    let hasMoved = false;
    let startX = 0, startY = 0;
    let initialLeft = 0, initialTop = 0;

    function getCoords(e) {
      if (e.touches && e.touches.length > 0) {
        return { x: e.touches[0].clientX, y: e.touches[0].clientY };
      }
      return { x: e.clientX, y: e.clientY };
    }

    function onPointerDown(e) {
      if (e.type === 'mousedown' && e.button !== 0) return;
      if (options.ignoreSelector && e.target.closest(options.ignoreSelector)) return;

      const coords = getCoords(e);
      isPointerDown = true;
      isDragging = false;
      hasMoved = false;
      startX = coords.x;
      startY = coords.y;

      const rect = element.getBoundingClientRect();
      initialLeft = rect.left;
      initialTop = rect.top;

      if (e.type === 'mousedown') {
        document.addEventListener('mousemove', onPointerMove, { passive: false });
        document.addEventListener('mouseup', onPointerUp);
      } else {
        document.addEventListener('touchmove', onPointerMove, { passive: false });
        document.addEventListener('touchend', onPointerUp);
        document.addEventListener('touchcancel', onPointerUp);
      }
    }

    function onPointerMove(e) {
      if (!isPointerDown) return;
      const coords = getCoords(e);
      const dx = coords.x - startX;
      const dy = coords.y - startY;

      if (!isDragging && Math.sqrt(dx * dx + dy * dy) > 5) {
        isDragging = true;
        hasMoved = true;
        element.classList.add('is-dragging');
        document.body.style.userSelect = 'none';
        document.body.style.webkitUserSelect = 'none';
      }

      if (isDragging) {
        if (e.cancelable) e.preventDefault();

        let newLeft = initialLeft + dx;
        let newTop = initialTop + dy;

        const rect = element.getBoundingClientRect();
        const margin = options.margin !== undefined ? options.margin : 12;
        const minLeft = margin;
        const maxLeft = Math.max(minLeft, window.innerWidth - rect.width - margin);
        const minTop = margin;
        const maxTop = Math.max(minTop, window.innerHeight - rect.height - margin);

        newLeft = Math.max(minLeft, Math.min(newLeft, maxLeft));
        newTop = Math.max(minTop, Math.min(newTop, maxTop));

        element.style.setProperty('left', `${newLeft}px`, 'important');
        element.style.setProperty('top', `${newTop}px`, 'important');
        element.style.setProperty('right', 'auto', 'important');
        element.style.setProperty('bottom', 'auto', 'important');

        if (options.onDrag) {
          options.onDrag();
        }
      }
    }

    function onPointerUp() {
      if (!isPointerDown) return;
      isPointerDown = false;

      document.removeEventListener('mousemove', onPointerMove);
      document.removeEventListener('mouseup', onPointerUp);
      document.removeEventListener('touchmove', onPointerMove);
      document.removeEventListener('touchend', onPointerUp);
      document.removeEventListener('touchcancel', onPointerUp);

      document.body.style.userSelect = '';
      document.body.style.webkitUserSelect = '';
      element.classList.remove('is-dragging');

      if (isDragging && options.onDragEnd) {
        const rect = element.getBoundingClientRect();
        options.onDragEnd({ left: rect.left, top: rect.top });
      }
    }

    handle.addEventListener('mousedown', onPointerDown);
    handle.addEventListener('touchstart', onPointerDown, { passive: true });

    // Intercept click when dragging so buttons do not accidentally trigger
    handle.addEventListener('click', function (e) {
      if (hasMoved) {
        e.preventDefault();
        e.stopPropagation();
        hasMoved = false;
      }
    }, true);

    // Keep within bounds on window resize
    window.addEventListener('resize', function () {
      if (element.style.left) {
        const rect = element.getBoundingClientRect();
        const margin = 12;
        let l = rect.left;
        let t = rect.top;
        if (rect.right > window.innerWidth - margin) {
          l = Math.max(margin, window.innerWidth - rect.width - margin);
        }
        if (rect.bottom > window.innerHeight - margin) {
          t = Math.max(margin, window.innerHeight - rect.height - margin);
        }
        if (l !== rect.left || t !== rect.top) {
          element.style.setProperty('left', `${l}px`, 'important');
          element.style.setProperty('top', `${t}px`, 'important');
        }
      }
    });
  }

  /**
   * Dynamically anchor chat popup to trigger widget.
   * Keeps popup strictly docked to the trigger button wherever it moves.
   */
  function positionPopupNearTrigger() {
    const root = document.getElementById('agentx-chat-widget-root');
    const popup = document.getElementById('agentx-chat-popup');
    if (!root || !popup) return;

    // Remove any hardcoded inline coordinates so popup strictly follows widget
    popup.style.removeProperty('left');
    popup.style.removeProperty('top');
    popup.style.removeProperty('right');
    popup.style.removeProperty('bottom');

    const rect = root.getBoundingClientRect();
    const isLeft = (rect.left + rect.width / 2) < (window.innerWidth / 2) || root.classList.contains('pos-bottom-left');
    const isTop = rect.top < 380;

    if (isLeft) {
      root.classList.add('dock-left');
      root.classList.remove('dock-right');
    } else {
      root.classList.add('dock-right');
      root.classList.remove('dock-left');
    }

    if (isTop) {
      root.classList.add('dock-top');
    } else {
      root.classList.remove('dock-top');
    }
  }

  function mountDOM() {
    let root = document.getElementById('agentx-chat-widget-root');
    if (root) {
      root.remove();
    }

    root = document.createElement('div');
    root.id = 'agentx-chat-widget-root';
    if (state.config.position === 'bottom-left') {
      root.classList.add('pos-bottom-left');
    }
    const checkVoice = () => {
      if (document.getElementById('dograh-widget-root') || document.querySelector('.dograh-widget-container') || document.querySelector('script[src*="dograh-widget.js"]')) {
        root.classList.add('has-dograh-voice');
      }
    };
    checkVoice();
    setTimeout(checkVoice, 500);
    setTimeout(checkVoice, 1500);

    root.innerHTML = `
      <div class="agentx-chat-popup ${state.isOpen ? '' : 'agentx-hidden'}" id="agentx-chat-popup">
        <div class="agentx-chat-header">
          <div class="agentx-drag-handle" title="Drag to move chat window"></div>
          <div class="agentx-header-content">
            <div class="agentx-header-info">
              <div class="agentx-avatar-wrap">
                <div class="agentx-avatar" style="background:${state.config.buttonColor};">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                  </svg>
                </div>
                <div class="agentx-avatar-dot"></div>
              </div>
              <div class="agentx-header-text">
                <h4 id="agentx-header-title">${state.config.title}</h4>
                <p id="agentx-header-sub">
                  <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#10b981;"></span>
                  <span>Chat Agent • Online</span>
                </p>
              </div>
            </div>
            <div class="agentx-header-actions">
              <button class="agentx-header-btn" id="agentx-restart-btn" title="Restart conversation" aria-label="Restart conversation">
                <svg viewBox="0 0 24 24" width="16" height="16" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="1 4 1 10 7 10"></polyline>
                  <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
                </svg>
              </button>
              <button class="agentx-header-btn" id="agentx-close-btn" title="Close chat" aria-label="Close chat">
                <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>
          </div>
        </div>
        <div class="agentx-chat-messages" id="agentx-messages-container"></div>
        <div class="agentx-chat-footer">
          <div class="agentx-input-bar">
            <input type="text" class="agentx-input" id="agentx-chat-input" placeholder="Type a message to Chat Agent..." autocomplete="off" />
            <button class="agentx-send-btn" id="agentx-send-btn" aria-label="Send message">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"></path>
              </svg>
            </button>
          </div>
          <div class="agentx-footer-watermark">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
            </svg>
            <span>Powered by AgentX Platform</span>
          </div>
        </div>
      </div>

      <button class="agentx-chat-trigger ${state.isOpen ? 'is-open' : ''}" id="agentx-chat-trigger" title="${state.config.buttonText}" aria-label="${state.config.buttonText}" style="background: ${state.config.buttonColor};">
        <div class="agentx-trigger-badge"></div>
        <svg class="agentx-pill-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
        </svg>
        <span class="agentx-pill-text" id="agentx-pill-text">${state.config.buttonText}</span>
      </button>
    `;

    // Restore saved trigger position if available
    try {
      const savedTrigger = sessionStorage.getItem(STORAGE_KEYS.TRIGGER_POS);
      if (savedTrigger) {
        const pos = JSON.parse(savedTrigger);
        if (typeof pos.left === 'number' && typeof pos.top === 'number') {
          const clampedX = Math.max(12, Math.min(pos.left, window.innerWidth - 72));
          const clampedY = Math.max(12, Math.min(pos.top, window.innerHeight - 72));
          root.style.setProperty('left', `${clampedX}px`, 'important');
          root.style.setProperty('top', `${clampedY}px`, 'important');
          root.style.setProperty('right', 'auto', 'important');
          root.style.setProperty('bottom', 'auto', 'important');
        }
      }
    } catch (e) {}

    document.body.appendChild(root);

    function checkVoiceOverlap() {
      if (document.getElementById('dograh-widget-root') || document.querySelector('.dograh-widget-container')) {
        root.classList.add('has-dograh-voice');
      }
    }
    checkVoiceOverlap();
    setTimeout(checkVoiceOverlap, 400);
    setTimeout(checkVoiceOverlap, 1200);

    bindEvents();
    renderMessages();

    positionPopupNearTrigger();
  }

  function formatTime(timestamp) {
    try {
      const d = timestamp ? new Date(timestamp) : new Date();
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return '';
    }
  }

  function renderMessages() {
    const container = document.getElementById('agentx-messages-container');
    if (!container) return;

    container.innerHTML = '';

    if (state.messages.length === 0) {
      const welcome = document.createElement('div');
      welcome.className = 'agentx-welcome-card';
      welcome.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;">
          <div class="agentx-welcome-badge" style="background:${state.config.buttonColor};">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
            </svg>
          </div>
          <div>
            <h5 class="agentx-welcome-title">${state.config.title || 'Chat Agent'}</h5>
            <p class="agentx-welcome-desc">Connected to workflow agent. Send a message to start chatting.</p>
          </div>
        </div>
      `;
      container.appendChild(welcome);
    } else {
      state.messages.forEach(function (msg) {
        const row = document.createElement('div');
        row.className = 'agentx-msg-row ' + (msg.role === 'user' ? 'agentx-row-user' : 'agentx-row-assistant');

        const bubble = document.createElement('div');
        bubble.className = 'agentx-msg';
        bubble.innerHTML = formatMessageText(msg.text);

        const time = document.createElement('div');
        time.className = 'agentx-msg-time';
        time.textContent = formatTime(msg.timestamp);

        row.appendChild(bubble);
        row.appendChild(time);
        container.appendChild(row);
      });
    }

    if (state.isLoading) {
      const typing = document.createElement('div');
      typing.className = 'agentx-typing';
      typing.innerHTML = `
        <div class="agentx-typing-dots">
          <div class="agentx-typing-dot"></div>
          <div class="agentx-typing-dot"></div>
          <div class="agentx-typing-dot"></div>
        </div>
        <span>AI is thinking...</span>
      `;
      container.appendChild(typing);
    }

    container.scrollTop = container.scrollHeight;
  }

  // 5. Send Message to AgentPlatform Backend
  async function sendMessage(text) {
    const cleanText = (text || '').trim();
    if (!cleanText || state.isLoading) return;

    // Append user message
    state.messages.push({ role: 'user', text: cleanText, timestamp: Date.now() });
    state.isLoading = true;
    persistSessionState();
    renderMessages();

    try {
      const response = await fetch(`${state.apiEndpoint}/api/v1/public/embed/chat/message`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Origin': window.location.origin,
        },
        body: JSON.stringify({
          token: state.token,
          session_key: state.sessionKey,
          text: cleanText,
        }),
      });

      if (!response.ok) {
        throw new Error('Message request failed with status ' + response.status);
      }

      const data = await response.json();
      const reply = data.assistant_text || "I'm processing your inquiry. How else may I assist you?";
      state.messages.push({ role: 'assistant', text: reply, timestamp: Date.now() });
    } catch (err) {
      console.error('AgentX Chat Widget error:', err);
      state.messages.push({
        role: 'assistant',
        text: 'Sorry, I encountered an issue connecting to the agent. Please check your connection and try again.',
        timestamp: Date.now(),
      });
    } finally {
      state.isLoading = false;
      persistSessionState();
      renderMessages();
    }
  }

  // 6. Navigation Persistence & Event Listeners
  function bindEvents() {
    const root = document.getElementById('agentx-chat-widget-root');
    const trigger = document.getElementById('agentx-chat-trigger');
    const popup = document.getElementById('agentx-chat-popup');
    const closeBtn = document.getElementById('agentx-close-btn');
    const restartBtn = document.getElementById('agentx-restart-btn');
    const input = document.getElementById('agentx-chat-input');
    const sendBtn = document.getElementById('agentx-send-btn');
    const container = document.getElementById('agentx-messages-container');
    // Make trigger freely draggable across viewport (popup follows widget automatically)
    makeDraggable(root, trigger, {
      margin: 12,
      onDrag: positionPopupNearTrigger,
      onDragEnd: function (pos) {
        try {
          sessionStorage.setItem(STORAGE_KEYS.TRIGGER_POS, JSON.stringify(pos));
        } catch (e) {}
        positionPopupNearTrigger();
      }
    });

    // Make popup header drag the whole widget container so popup strictly follows trigger
    const popupHeader = popup.querySelector('.agentx-chat-header');
    if (popupHeader) {
      makeDraggable(root, popupHeader, {
        margin: 12,
        ignoreSelector: '#agentx-close-btn, #agentx-close-btn *, #agentx-restart-btn, #agentx-restart-btn *',
        onDrag: positionPopupNearTrigger,
        onDragEnd: function (pos) {
          try {
            sessionStorage.setItem(STORAGE_KEYS.TRIGGER_POS, JSON.stringify(pos));
          } catch (e) {}
          positionPopupNearTrigger();
        }
      });
    }

    // Trigger toggle
    trigger.addEventListener('click', function () {
      state.isOpen = !state.isOpen;
      if (state.isOpen) {
        trigger.classList.add('is-open');
        positionPopupNearTrigger();
        popup.classList.remove('agentx-hidden');
        renderMessages();
        input.focus();
      } else {
        trigger.classList.remove('is-open');
        popup.classList.add('agentx-hidden');
      }
      persistSessionState();
    });

    // Close button: closes the popup window; messages persist in the tab until the tab is closed
    closeBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      state.isOpen = false;
      trigger.classList.remove('is-open');
      popup.classList.add('agentx-hidden');
      persistSessionState();
    });

    // Restart conversation button
    if (restartBtn) {
      restartBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        terminateAndResetSession();
        renderMessages();
        persistSessionState();
      });
    }

    // Send button & enter key
    sendBtn.addEventListener('click', function () {
      const val = input.value;
      input.value = '';
      sendMessage(val);
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        const val = input.value;
        input.value = '';
        sendMessage(val);
      }
    });

    // Delegate quick prompt chips & navigation persistence
    container.addEventListener('click', function (e) {
      // Prompt chips
      const chip = e.target.closest('.agentx-chip-btn');
      if (chip) {
        const prompt = chip.getAttribute('data-prompt');
        if (prompt) {
          sendMessage(prompt);
          return;
        }
      }

      // Internal links & same-tab navigation
      const link = e.target.closest('a');
      if (link) {
        let rawHref = (link.getAttribute('href') || '').trim();
        let parseHref = rawHref;
        if (/^localhost(\/|:)/i.test(parseHref)) {
          parseHref = 'http://' + parseHref;
        }

        try {
          const parsed = new URL(parseHref, window.location.href);
          const isSameHost =
            parsed.hostname === window.location.hostname ||
            parsed.hostname === 'localhost' ||
            parsed.hostname === '127.0.0.1';

          if (isSameHost) {
            e.preventDefault();
            // Preserve the widget open state across the navigation
            state.isOpen = true;
            persistSessionState();

            // Extract target path cleanly without stray 'localhost' segment
            let targetPath = parsed.pathname;
            if (targetPath.startsWith('/localhost/')) {
              targetPath = targetPath.replace(/^\/localhost/, '');
            } else if (targetPath === '/localhost') {
              targetPath = '/';
            }
            if (parsed.search) targetPath += parsed.search;
            if (parsed.hash) targetPath += parsed.hash;

            // Normalize portfolio routes directly
            if (targetPath.includes('portifolio') || targetPath.includes('portfolio')) {
              targetPath = '/portifolio';
            }

            // Seamless client-side router transition in the same tab
            try {
              window.history.pushState({}, '', targetPath);
              window.dispatchEvent(new PopStateEvent('popstate'));
              window.dispatchEvent(new Event('locationchange'));
            } catch (navErr) {
              window.location.href = targetPath;
            }
            return;
          }
        } catch (err) {
          if (rawHref.includes('portifolio') || rawHref.includes('portfolio') || rawHref.startsWith('/') || rawHref.startsWith('./')) {
            e.preventDefault();
            state.isOpen = true;
            persistSessionState();
            const cleanPath = '/portifolio';
            try {
              window.history.pushState({}, '', cleanPath);
              window.dispatchEvent(new PopStateEvent('popstate'));
              window.dispatchEvent(new Event('locationchange'));
            } catch (navErr) {
              window.location.href = cleanPath;
            }
            return;
          }
        }
      }
    });
  }

  // 7. Initialization Lifecycle
  function init() {
    initOrRestoreSession();
    injectStyles();
    mountDOM();

    // Fetch widget config if available
    if (state.token) {
      fetch(`${state.apiEndpoint}/api/v1/public/embed/config/${state.token}`, {
        method: 'GET',
        headers: { 'Origin': window.location.origin },
      })
        .then(function (res) {
          return res.ok ? res.json() : null;
        })
        .then(function (cfg) {
          if (cfg) {
            const pos =
              (cfg.settings && cfg.settings.chatPosition) ||
              cfg.chat_position ||
              'bottom-right';

            state.config.position = pos;

            const root = document.getElementById('agentx-chat-widget-root');
            if (root && !sessionStorage.getItem(STORAGE_KEYS.TRIGGER_POS)) {
              if (pos === 'bottom-left') {
                root.classList.add('pos-bottom-left');
              } else {
                root.classList.remove('pos-bottom-left');
              }
            }

            const title = (cfg.settings && cfg.settings.chatTitle) || cfg.chat_title || cfg.workflow_name || state.config.title;
            if (title) {
              state.config.title = title;
              const titleEl = document.getElementById('agentx-header-title');
              if (titleEl) titleEl.textContent = title;
              const welcomeTitle = document.querySelector('.agentx-welcome-title');
              if (welcomeTitle) welcomeTitle.textContent = title;
            }

            const subtitle = (cfg.settings && cfg.settings.chatSubtitle) || state.config.subtitle;
            if (subtitle) {
              state.config.subtitle = subtitle;
              const subEl = document.getElementById('agentx-header-sub');
              if (subEl) subEl.textContent = subtitle;
            }

            const btnColor = (cfg.settings && cfg.settings.chatButtonColor) || cfg.chat_button_color || '#2563eb';
            if (btnColor) {
              state.config.buttonColor = btnColor;
              const trigger = document.getElementById('agentx-chat-trigger');
              if (trigger) trigger.style.background = btnColor;
              const avatar = document.querySelector('.agentx-avatar');
              if (avatar) avatar.style.background = btnColor;
              const welcomeBadge = document.querySelector('.agentx-welcome-badge');
              if (welcomeBadge) welcomeBadge.style.background = btnColor;
            }

            const btnText = (cfg.settings && cfg.settings.chatButtonText) || cfg.chat_button_text || 'Chat with Agent';
            if (btnText) {
              state.config.buttonText = btnText;
              const trigger = document.getElementById('agentx-chat-trigger');
              if (trigger) {
                trigger.setAttribute('title', btnText);
                trigger.setAttribute('aria-label', btnText);
              }
              const pillTextEl = document.getElementById('agentx-pill-text');
              if (pillTextEl) pillTextEl.textContent = btnText;
            }
          }
        })
        .catch(function () {});
    }
  }

  // Wait for DOM to be ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
