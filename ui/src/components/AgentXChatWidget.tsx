"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";

declare global {
  interface Window {
    __AGENTX_CHAT_WIDGET_INITIALIZED__?: boolean;
  }
}

export default function AgentXChatWidget() {
  const pathname = usePathname();

  useEffect(() => {
    if (typeof window === "undefined") return;

    // Singleton guard: do not re-inject if already present
    if (window.__AGENTX_CHAT_WIDGET_INITIALIZED__ || document.getElementById("agentx-chat-widget-script")) {
      return;
    }

    const script = document.createElement("script");
    script.id = "agentx-chat-widget-script";
    const token = process.env.NEXT_PUBLIC_DEFAULT_EMBED_TOKEN || "emb_A0TwzhYN4WzUSDC6NxK_04MdTzMe7HHNCCfICvv07j4";
    const apiEndpoint = window.location.origin;
    script.src = `/embed/agentx-chat-widget.js?token=${token}&environment=local&apiEndpoint=${encodeURIComponent(apiEndpoint)}`;
    script.async = true;
    document.body.appendChild(script);
  }, []);

  return null;
}
