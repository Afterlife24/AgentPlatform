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

  if (!apiEndpoint) {
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
      title: 'AI Consultant',
      subtitle: 'Online • Ready to assist',
      buttonColor: '#2563eb',
      buttonText: 'Chat with Us',
      position: 'bottom-right',
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

      /* Floating Trigger Button */
      .agentx-chat-trigger {
        position: relative;
        width: 62px;
        height: 62px;
        border-radius: 50%;
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 60%, #4338ca 100%);
        box-shadow: 0 10px 25px -4px rgba(37, 99, 235, 0.5), 0 0 0 1px rgba(255, 255, 255, 0.25) inset, 0 2px 4px rgba(0, 0, 0, 0.1);
        border: none;
        cursor: grab;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #ffffff;
        transition: transform 0.22s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.22s ease;
        user-select: none;
        -webkit-user-select: none;
        -webkit-user-drag: none;
        touch-action: none;
      }
      .agentx-chat-trigger:hover {
        transform: scale(1.08) translateY(-2px);
        box-shadow: 0 16px 36px -4px rgba(37, 99, 235, 0.65), 0 0 0 1.5px rgba(255, 255, 255, 0.4) inset;
      }
      .agentx-chat-trigger:active,
      #agentx-chat-widget-root.is-dragging .agentx-chat-trigger {
        cursor: grabbing !important;
        transform: scale(0.95);
      }

      /* Online Status Badge on Trigger */
      .agentx-trigger-badge {
        position: absolute;
        top: 2px;
        right: 2px;
        width: 14px;
        height: 14px;
        border-radius: 50%;
        background: #10b981;
        border: 2.5px solid #ffffff;
        box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
        animation: agentx-badge-pulse 2.2s infinite;
        pointer-events: none;
      }
      @keyframes agentx-badge-pulse {
        0% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
        70% { box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
        100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
      }

      /* Icon Toggle Animation */
      .agentx-icon-chat,
      .agentx-icon-close {
        position: absolute;
        transition: transform 0.26s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.2s ease;
        display: flex;
        align-items: center;
        justify-content: center;
        pointer-events: none;
      }
      .agentx-chat-trigger.is-open .agentx-icon-chat {
        transform: rotate(90deg) scale(0);
        opacity: 0;
      }
      .agentx-chat-trigger:not(.is-open) .agentx-icon-close {
        transform: rotate(-90deg) scale(0);
        opacity: 0;
      }
      .agentx-chat-trigger.is-open .agentx-icon-close {
        transform: rotate(0) scale(1);
        opacity: 1;
      }

      /* Interactive Teaser Pill */
      .agentx-teaser {
        position: absolute;
        right: 76px;
        top: 50%;
        transform: translateY(-50%);
        background: #ffffff;
        color: #0f172a;
        padding: 9px 15px;
        border-radius: 24px;
        font-size: 13px;
        font-weight: 600;
        letter-spacing: -0.01em;
        white-space: nowrap;
        box-shadow: 0 10px 28px -4px rgba(0, 0, 0, 0.15), 0 0 0 1px rgba(0, 0, 0, 0.06);
        display: flex;
        align-items: center;
        gap: 8px;
        cursor: pointer;
        opacity: 0;
        pointer-events: none;
        transition: opacity 0.3s ease, transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        z-index: -1;
      }
      .agentx-teaser.is-visible {
        opacity: 1;
        pointer-events: auto;
      }
      .agentx-teaser:hover {
        transform: translateY(-50%) scale(1.03);
        box-shadow: 0 14px 32px -4px rgba(0, 0, 0, 0.2), 0 0 0 1px rgba(37, 99, 235, 0.2);
      }
      #agentx-chat-widget-root.pos-bottom-left .agentx-teaser {
        right: auto;
        left: 76px;
      }

      /* Chat Popup Window */
      .agentx-chat-popup {
        position: fixed;
        bottom: 96px;
        right: 24px;
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
      #agentx-chat-widget-root.pos-bottom-left .agentx-chat-popup {
        right: auto;
        left: 24px;
        transform-origin: bottom left;
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
    // Escape basic HTML
    let escaped = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Linkify markdown links: [label](url)
    escaped = escaped.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function (match, label, url) {
      return `<a href="${url}" class="agentx-internal-link">${label}</a>`;
    });

    // Linkify raw URLs
    escaped = escaped.replace(/(https?:\/\/[^\s<]+)/g, function (url) {
      return `<a href="${url}" class="agentx-internal-link">${url}</a>`;
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
   * Intelligently position chat popup near trigger button unless user
   * previously dragged popup to their own custom screen location.
   */
  function positionPopupNearTrigger() {
    const root = document.getElementById('agentx-chat-widget-root');
    const popup = document.getElementById('agentx-chat-popup');
    if (!root || !popup) return;

    // Check if user has explicitly dragged popup to custom location
    try {
      const savedPopup = sessionStorage.getItem(STORAGE_KEYS.POPUP_POS);
      if (savedPopup) {
        const pos = JSON.parse(savedPopup);
        if (typeof pos.left === 'number' && typeof pos.top === 'number') {
          const clampedX = Math.max(12, Math.min(pos.left, window.innerWidth - 320));
          const clampedY = Math.max(12, Math.min(pos.top, window.innerHeight - 300));
          popup.style.setProperty('left', `${clampedX}px`, 'important');
          popup.style.setProperty('top', `${clampedY}px`, 'important');
          popup.style.setProperty('right', 'auto', 'important');
          popup.style.setProperty('bottom', 'auto', 'important');
          return;
        }
      }
    } catch (e) {}

    // Otherwise position popup adjacent to floating trigger button
    const triggerRect = root.getBoundingClientRect();
    const popupWidth = Math.min(380, window.innerWidth - 32);
    const popupHeight = Math.min(560, window.innerHeight - 120);
    const margin = 16;

    let left;
    if (triggerRect.left + popupWidth <= window.innerWidth - margin) {
      left = Math.max(margin, triggerRect.left);
    } else {
      left = Math.max(margin, triggerRect.right - popupWidth);
    }

    let top;
    if (triggerRect.top > window.innerHeight / 2) {
      top = Math.max(margin, triggerRect.top - popupHeight - 12);
    } else {
      top = Math.min(window.innerHeight - popupHeight - margin, triggerRect.bottom + 12);
    }

    popup.style.setProperty('left', `${left}px`, 'important');
    popup.style.setProperty('top', `${top}px`, 'important');
    popup.style.setProperty('right', 'auto', 'important');
    popup.style.setProperty('bottom', 'auto', 'important');
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

    root.innerHTML = `
      <div class="agentx-chat-popup ${state.isOpen ? '' : 'agentx-hidden'}" id="agentx-chat-popup">
        <div class="agentx-chat-header">
          <div class="agentx-drag-handle" title="Drag to move chat window"></div>
          <div class="agentx-header-content">
            <div class="agentx-header-info">
              <div class="agentx-avatar-wrap">
                <div class="agentx-avatar">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 2a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2 2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z"/>
                    <rect x="4" y="8" width="16" height="12" rx="4"/>
                    <path d="M2 14h2"/>
                    <path d="M20 14h2"/>
                    <path d="M15 13v2"/>
                    <path d="M9 13v2"/>
                  </svg>
                </div>
                <div class="agentx-avatar-dot"></div>
              </div>
              <div class="agentx-header-text">
                <h4 id="agentx-header-title">${state.config.title}</h4>
                <p id="agentx-header-sub">
                  <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#10b981;"></span>
                  <span>${state.config.subtitle}</span>
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
            <input type="text" class="agentx-input" id="agentx-chat-input" placeholder="Ask anything about our AI solutions..." autocomplete="off" />
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

      <div class="agentx-teaser" id="agentx-teaser">
        <span>💬 Have questions? Chat with AI</span>
      </div>

      <button class="agentx-chat-trigger ${state.isOpen ? 'is-open' : ''}" id="agentx-chat-trigger" title="${state.config.buttonText}" aria-label="Open chat">
        <div class="agentx-trigger-badge"></div>
        <div class="agentx-icon-chat">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor">
            <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"></path>
          </svg>
        </div>
        <div class="agentx-icon-close">
          <svg viewBox="0 0 24 24" width="26" height="26" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </div>
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

    if (state.isOpen) {
      positionPopupNearTrigger();
    }
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
        <div style="display:flex;align-items:center;gap:10px;">
          <div class="agentx-welcome-badge">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"></path>
            </svg>
          </div>
          <div>
            <h5 class="agentx-welcome-title">Welcome to AgentX Consultancy</h5>
            <p class="agentx-welcome-desc">How can I assist you with your AI & Automation goals?</p>
          </div>
        </div>
        <div class="agentx-chips-wrap">
          <button class="agentx-chip-btn" data-prompt="What AI consulting and automation services do you offer?">
            <span>🚀 What services do you offer?</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <button class="agentx-chip-btn" data-prompt="How do AI agent workflows improve business efficiency?">
            <span>⚡ How does AI automation work?</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
          <button class="agentx-chip-btn" data-prompt="I would like to schedule an AI strategy consultation. How can we connect?">
            <span>📅 Book a consultation</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>
          </button>
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
    const teaser = document.getElementById('agentx-teaser');

    // Make trigger freely draggable across viewport
    makeDraggable(root, trigger, {
      margin: 12,
      onDragEnd: function (pos) {
        try {
          sessionStorage.setItem(STORAGE_KEYS.TRIGGER_POS, JSON.stringify(pos));
        } catch (e) {}
      }
    });

    // Make popup window freely draggable by its header
    const popupHeader = popup.querySelector('.agentx-chat-header');
    if (popupHeader) {
      makeDraggable(popup, popupHeader, {
        margin: 12,
        ignoreSelector: '#agentx-close-btn, #agentx-close-btn *, #agentx-restart-btn, #agentx-restart-btn *',
        onDragEnd: function (pos) {
          try {
            sessionStorage.setItem(STORAGE_KEYS.POPUP_POS, JSON.stringify(pos));
          } catch (e) {}
        }
      });
    }

    // Trigger toggle
    trigger.addEventListener('click', function () {
      state.isOpen = !state.isOpen;
      if (state.isOpen) {
        trigger.classList.add('is-open');
        if (teaser) teaser.classList.remove('is-visible');
        positionPopupNearTrigger();
        popup.classList.remove('agentx-hidden');
        input.focus();
      } else {
        trigger.classList.remove('is-open');
        popup.classList.add('agentx-hidden');
      }
      persistSessionState();
    });

    // Teaser pill interaction
    if (teaser && !state.isOpen) {
      setTimeout(function () {
        if (!state.isOpen && teaser) {
          teaser.classList.add('is-visible');
        }
      }, 1600);
      teaser.addEventListener('click', function () {
        teaser.classList.remove('is-visible');
        if (!state.isOpen) {
          trigger.click();
        }
      });
    }

    // Close button: triggers backend teardown & state hygiene (Priority 3 & 4)
    closeBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      state.isOpen = false;
      trigger.classList.remove('is-open');
      popup.classList.add('agentx-hidden');
      // Terminate backend session & clear local state
      terminateAndResetSession();
      renderMessages();
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

      // Internal links
      const link = e.target.closest('a');
      if (link && link.href) {
        const linkUrl = new URL(link.href, window.location.href);
        if (linkUrl.origin === window.location.origin) {
          e.preventDefault();
          state.isOpen = true;
          persistSessionState();
          window.location.href = link.href;
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

            const title = (cfg.settings && cfg.settings.chatTitle) || state.config.title;
            if (title) {
              state.config.title = title;
              const titleEl = document.getElementById('agentx-header-title');
              if (titleEl) titleEl.textContent = title;
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
            }

            const btnText = (cfg.settings && cfg.settings.chatButtonText) || cfg.chat_button_text || 'Chat with us';
            if (btnText) {
              state.config.buttonText = btnText;
              const trigger = document.getElementById('agentx-chat-trigger');
              if (trigger) trigger.setAttribute('title', btnText);
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
