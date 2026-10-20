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
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      }
      #agentx-chat-widget-root.pos-bottom-left {
        right: auto;
        left: 24px;
      }
      .agentx-chat-trigger {
        width: 60px;
        height: 60px;
        border-radius: 50%;
        background: linear-gradient(135deg, #2563eb, #1d4ed8);
        box-shadow: 0 8px 24px rgba(37, 99, 235, 0.35);
        border: none;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        color: #ffffff;
        transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.2s ease;
      }
      .agentx-chat-trigger:hover {
        transform: scale(1.06);
        box-shadow: 0 12px 28px rgba(37, 99, 235, 0.45);
      }
      .agentx-chat-trigger:active {
        transform: scale(0.96);
      }
      .agentx-chat-trigger svg {
        width: 28px;
        height: 28px;
        fill: currentColor;
      }
      .agentx-chat-popup {
        position: fixed;
        bottom: 96px;
        right: 24px;
        width: 380px;
        max-width: calc(100vw - 32px);
        height: 560px;
        max-height: calc(100vh - 120px);
        background: #ffffff;
        border-radius: 16px;
        box-shadow: 0 16px 40px rgba(0, 0, 0, 0.16), 0 0 1px rgba(0, 0, 0, 0.2);
        display: flex;
        flex-direction: column;
        overflow: hidden;
        transform-origin: bottom right;
        transition: opacity 0.25s ease, transform 0.25s cubic-bezier(0.16, 1, 0.3, 1);
        z-index: 999999;
      }
      #agentx-chat-widget-root.pos-bottom-left .agentx-chat-popup {
        right: auto;
        left: 24px;
        transform-origin: bottom left;
      }
      .agentx-chat-popup.agentx-hidden {
        opacity: 0;
        pointer-events: none;
        transform: scale(0.92) translateY(12px);
      }
      .agentx-chat-header {
        background: linear-gradient(135deg, #1e293b, #0f172a);
        color: #ffffff;
        padding: 16px 20px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      }
      .agentx-header-info {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .agentx-avatar {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        background: #3b82f6;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 14px;
      }
      .agentx-header-text h4 {
        margin: 0;
        font-size: 15px;
        font-weight: 600;
        color: #ffffff;
      }
      .agentx-header-text p {
        margin: 2px 0 0;
        font-size: 12px;
        color: #94a3b8;
      }
      .agentx-close-btn {
        background: transparent;
        border: none;
        color: #94a3b8;
        cursor: pointer;
        padding: 6px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: color 0.15s, background 0.15s;
      }
      .agentx-close-btn:hover {
        color: #ffffff;
        background: rgba(255, 255, 255, 0.1);
      }
      .agentx-chat-messages {
        flex: 1;
        padding: 16px;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 12px;
        background: #f8fafc;
      }
      .agentx-msg {
        max-width: 82%;
        padding: 10px 14px;
        border-radius: 14px;
        font-size: 14px;
        line-height: 1.45;
        word-break: break-word;
      }
      .agentx-msg-user {
        align-self: flex-end;
        background: #2563eb;
        color: #ffffff;
        border-bottom-right-radius: 4px;
      }
      .agentx-msg-assistant {
        align-self: flex-start;
        background: #ffffff;
        color: #1e293b;
        border: 1px solid #e2e8f0;
        border-bottom-left-radius: 4px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
      }
      .agentx-msg a {
        color: #2563eb;
        text-decoration: underline;
        cursor: pointer;
      }
      .agentx-typing {
        display: flex;
        gap: 4px;
        padding: 12px 16px;
        align-self: flex-start;
        background: #ffffff;
        border-radius: 14px;
        border: 1px solid #e2e8f0;
      }
      .agentx-typing-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: #94a3b8;
        animation: agentx-pulse 1.2s infinite ease-in-out;
      }
      .agentx-typing-dot:nth-child(2) { animation-delay: 0.2s; }
      .agentx-typing-dot:nth-child(3) { animation-delay: 0.4s; }
      @keyframes agentx-pulse {
        0%, 80%, 100% { transform: scale(0.7); opacity: 0.4; }
        40% { transform: scale(1.1); opacity: 1; }
      }
      .agentx-chat-footer {
        padding: 12px 14px;
        background: #ffffff;
        border-top: 1px solid #e2e8f0;
        display: flex;
        gap: 8px;
        align-items: center;
      }
      .agentx-input {
        flex: 1;
        padding: 10px 14px;
        border: 1px solid #cbd5e1;
        border-radius: 20px;
        font-size: 14px;
        outline: none;
        transition: border-color 0.15s ease;
      }
      .agentx-input:focus {
        border-color: #2563eb;
      }
      .agentx-send-btn {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        background: #2563eb;
        border: none;
        color: #ffffff;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: background 0.15s ease;
      }
      .agentx-send-btn:disabled {
        background: #cbd5e1;
        cursor: not-allowed;
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
          <div class="agentx-header-info">
            <div class="agentx-avatar">AI</div>
            <div class="agentx-header-text">
              <h4 id="agentx-header-title">${state.config.title}</h4>
              <p id="agentx-header-sub">${state.config.subtitle}</p>
            </div>
          </div>
          <button class="agentx-close-btn" id="agentx-close-btn" title="End conversation and close" aria-label="Close chat">
            <svg viewBox="0 0 24 24" width="18" height="18" stroke="currentColor" stroke-width="2" fill="none"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
          </button>
        </div>
        <div class="agentx-chat-messages" id="agentx-messages-container"></div>
        <div class="agentx-chat-footer">
          <input type="text" class="agentx-input" id="agentx-chat-input" placeholder="Type your message..." autocomplete="off" />
          <button class="agentx-send-btn" id="agentx-send-btn" aria-label="Send message">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"></path></svg>
          </button>
        </div>
      </div>
      <button class="agentx-chat-trigger" id="agentx-chat-trigger" title="${state.config.buttonText}" aria-label="Open chat">
        <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"></path></svg>
      </button>
    `;

    document.body.appendChild(root);
    bindEvents();
    renderMessages();
  }

  function renderMessages() {
    const container = document.getElementById('agentx-messages-container');
    if (!container) return;

    container.innerHTML = '';

    if (state.messages.length === 0) {
      const welcome = document.createElement('div');
      welcome.className = 'agentx-msg agentx-msg-assistant';
      welcome.innerHTML = 'Hello! Welcome to AgentX Consultancy. How can I assist you with your AI & Automation goals today?';
      container.appendChild(welcome);
    } else {
      state.messages.forEach(function (msg) {
        const div = document.createElement('div');
        div.className = 'agentx-msg ' + (msg.role === 'user' ? 'agentx-msg-user' : 'agentx-msg-assistant');
        div.innerHTML = formatMessageText(msg.text);
        container.appendChild(div);
      });
    }

    if (state.isLoading) {
      const typing = document.createElement('div');
      typing.className = 'agentx-typing';
      typing.innerHTML = '<div class="agentx-typing-dot"></div><div class="agentx-typing-dot"></div><div class="agentx-typing-dot"></div>';
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
    const trigger = document.getElementById('agentx-chat-trigger');
    const popup = document.getElementById('agentx-chat-popup');
    const closeBtn = document.getElementById('agentx-close-btn');
    const input = document.getElementById('agentx-chat-input');
    const sendBtn = document.getElementById('agentx-send-btn');
    const container = document.getElementById('agentx-messages-container');

    // Trigger toggle
    trigger.addEventListener('click', function () {
      state.isOpen = !state.isOpen;
      if (state.isOpen) {
        popup.classList.remove('agentx-hidden');
        input.focus();
      } else {
        popup.classList.add('agentx-hidden');
      }
      persistSessionState();
    });

    // Close button: triggers backend teardown & state hygiene (Priority 3 & 4)
    closeBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      popup.classList.add('agentx-hidden');
      // Terminate backend session & clear local state
      terminateAndResetSession();
      renderMessages();
    });

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

    // Navigation Persistence: Handle internal link clicks in same tab
    container.addEventListener('click', function (e) {
      const link = e.target.closest('a');
      if (link && link.href) {
        const linkUrl = new URL(link.href, window.location.href);
        // If internal link on same origin
        if (linkUrl.origin === window.location.origin) {
          e.preventDefault();
          // Keep chat open across the navigation
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
            if (cfg.button_text) state.config.buttonText = cfg.button_text;
            if (cfg.button_color) state.config.buttonColor = cfg.button_color;
            if (cfg.position) state.config.position = cfg.position;
            const root = document.getElementById('agentx-chat-widget-root');
            if (root && cfg.position === 'bottom-left') {
              root.classList.add('pos-bottom-left');
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
