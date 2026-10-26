import {
    AlertCircle,
    Check,
    Copy,
    ExternalLink,
    Eye,
    EyeOff,
    Loader2,
    MessageSquare,
    Mic,
    Plus,
    Power,
    Rocket,
    Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { client } from "@/client/client.gen";
import {
    createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost,
    deactivateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenDelete,
    getEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenGet,
} from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { WIDGET_MODE_DOCUMENTATION_URLS } from "@/constants/documentation";

interface EmbedDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    workflowId: number;
    workflowName: string;
}

interface EmbedToken {
    id: number;
    token: string;
    allowed_domains: string[] | null;
    settings: Record<string, unknown> | null;
    is_active: boolean;
    voice_enabled?: boolean | null;
    chat_enabled?: boolean | null;
    usage_count: number;
    usage_limit: number | null;
    expires_at: string | null;
    created_at: string;
    embed_script: string;
    chat_embed_script?: string;
}

export function EmbedDialog({
    open,
    onOpenChange,
    workflowId,
    workflowName,
}: EmbedDialogProps) {
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [togglingVoice, setTogglingVoice] = useState(false);
    const [togglingChat, setTogglingChat] = useState(false);
    const [embedToken, setEmbedToken] = useState<EmbedToken | null>(null);
    const [copied, setCopied] = useState(false);

    // Widget Type: "voice" (Talk to Agent) vs "chat" (Chat Widget)
    const [widgetType, setWidgetType] = useState<"voice" | "chat">("voice");

    // Separate widget visibility toggles
    const [voiceEnabled, setVoiceEnabled] = useState(true);
    const [chatEnabled, setChatEnabled] = useState(true);
    const [isEnabled, setIsEnabled] = useState(true);
    const [domains, setDomains] = useState<string[]>([]);
    const [newDomain, setNewDomain] = useState("");
    const [embedMode, setEmbedMode] = useState<"floating" | "inline" | "headless">("floating");
    const [position, setPosition] = useState("bottom-right");
    const [buttonText, setButtonText] = useState("Talk to Agent");
    const [buttonColor, setButtonColor] = useState("#10b981");
    const [callToActionText, setCallToActionText] = useState("Click to start voice conversation");

    // Form state (Chat Widget)
    const [chatEmbedMode, setChatEmbedMode] = useState<"floating" | "inline">("floating");
    const [chatTitle, setChatTitle] = useState(workflowName || "Chat Agent");
    const [chatButtonColor, setChatButtonColor] = useState("#2563eb");
    const [chatPosition, setChatPosition] = useState("bottom-right");
    const [chatButtonText, setChatButtonText] = useState("Chat with Agent");

    // Active code snippet tab (mirrors widgetType by default)
    const [activeScriptType, setActiveScriptType] = useState<"voice" | "chat">("voice");

    const loadEmbedToken = useCallback(async () => {
        setLoading(true);
        try {
            const response = await getEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenGet({
                path: { workflow_id: workflowId },
            });

            if (response.data) {
                const data = response.data as EmbedToken;
                setEmbedToken(data);
                setIsEnabled(data.is_active);

                const settings = (data.settings || {}) as Record<string, any>;
                const vOn =
                    data.voice_enabled !== undefined && data.voice_enabled !== null
                        ? Boolean(data.voice_enabled)
                        : settings.voiceEnabled !== undefined
                        ? Boolean(settings.voiceEnabled)
                        : data.is_active;

                const cOn =
                    data.chat_enabled !== undefined && data.chat_enabled !== null
                        ? Boolean(data.chat_enabled)
                        : settings.chatEnabled !== undefined
                        ? Boolean(settings.chatEnabled)
                        : data.is_active;

                setVoiceEnabled(vOn);
                setChatEnabled(cOn);

                // Load settings
                if (data.settings) {
                    if (settings.widgetType === "voice" || settings.widgetType === "chat") {
                        setWidgetType(settings.widgetType);
                        setActiveScriptType(settings.widgetType);
                    }
                    setEmbedMode((settings.embedMode as "floating" | "inline" | "headless") || "floating");
                    setPosition(settings.position || "bottom-right");
                    setButtonText(settings.buttonText || "Talk to Agent");
                    setButtonColor(settings.buttonColor || "#10b981");
                    setCallToActionText(settings.callToActionText || "Click to start voice conversation");

                    // Chat settings
                    if (settings.chatEmbedMode === "floating" || settings.chatEmbedMode === "inline") {
                        setChatEmbedMode(settings.chatEmbedMode);
                    }
                    if (settings.chatTitle) setChatTitle(settings.chatTitle);
                    if (settings.chatButtonColor) setChatButtonColor(settings.chatButtonColor);
                    if (settings.chatPosition) setChatPosition(settings.chatPosition);
                    if (settings.chatButtonText) setChatButtonText(settings.chatButtonText);
                }

                // Load domains
                if (data.allowed_domains) {
                    setDomains(data.allowed_domains);
                }
            }
        } catch (error) {
            console.error("Failed to load embed token:", error);
        } finally {
            setLoading(false);
        }
    }, [workflowId]);

    useEffect(() => {
        if (open) {
            loadEmbedToken();
        }
    }, [open, loadEmbedToken]);

    const handleToggleVoice = async (newState: boolean) => {
        setVoiceEnabled(newState);
        if (!embedToken) {
            return;
        }

        setTogglingVoice(true);
        try {
            const res = await client.patch({
                url: "/api/v1/workflow/{workflow_id}/embed-token/toggle",
                path: { workflow_id: workflowId },
                body: { widget_type: "voice", enabled: newState },
            });

            if (res.data) {
                const updated = res.data as EmbedToken;
                setEmbedToken(updated);
                if (updated.voice_enabled !== undefined && updated.voice_enabled !== null) {
                    setVoiceEnabled(Boolean(updated.voice_enabled));
                }
                if (updated.chat_enabled !== undefined && updated.chat_enabled !== null) {
                    setChatEnabled(Boolean(updated.chat_enabled));
                }
                setIsEnabled(updated.is_active);
            }
        } catch (error) {
            console.error("Failed to toggle voice widget status:", error);
            setVoiceEnabled(!newState);
        } finally {
            setTogglingVoice(false);
        }
    };

    const handleToggleChat = async (newState: boolean) => {
        setChatEnabled(newState);
        if (!embedToken) {
            return;
        }

        setTogglingChat(true);
        try {
            const res = await client.patch({
                url: "/api/v1/workflow/{workflow_id}/embed-token/toggle",
                path: { workflow_id: workflowId },
                body: { widget_type: "chat", enabled: newState },
            });

            if (res.data) {
                const updated = res.data as EmbedToken;
                setEmbedToken(updated);
                if (updated.voice_enabled !== undefined && updated.voice_enabled !== null) {
                    setVoiceEnabled(Boolean(updated.voice_enabled));
                }
                if (updated.chat_enabled !== undefined && updated.chat_enabled !== null) {
                    setChatEnabled(Boolean(updated.chat_enabled));
                }
                setIsEnabled(updated.is_active);
            }
        } catch (error) {
            console.error("Failed to toggle chat widget status:", error);
            setChatEnabled(!newState);
        } finally {
            setTogglingChat(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const response = await createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost({
                path: { workflow_id: workflowId },
                body: {
                    allowed_domains: domains.length > 0 ? domains : null,
                    settings: {
                        widgetType,
                        voiceEnabled,
                        chatEnabled,
                        embedMode,
                        position,
                        buttonText,
                        buttonColor,
                        callToActionText,
                        size: "medium",
                        autoStart: false,
                        containerId: embedMode === "inline" ? "dograh-inline-container" : undefined,
                        chatEmbedMode,
                        chatTitle,
                        chatButtonColor,
                        chatPosition,
                        chatButtonText,
                    },
                    is_active: voiceEnabled || chatEnabled,
                    voice_enabled: voiceEnabled,
                    chat_enabled: chatEnabled,
                    usage_limit: null,
                    expires_in_days: null,
                },
            });

            if (response.data) {
                const data = response.data as EmbedToken;
                setEmbedToken(data);
                if (data.voice_enabled !== undefined && data.voice_enabled !== null) {
                    setVoiceEnabled(Boolean(data.voice_enabled));
                }
                if (data.chat_enabled !== undefined && data.chat_enabled !== null) {
                    setChatEnabled(Boolean(data.chat_enabled));
                }
                setIsEnabled(data.is_active);
            }
        } catch (error) {
            console.error("Failed to save embed token:", error);
        } finally {
            setSaving(false);
        }
    };

    const copyToClipboard = (text: string) => {
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    const addDomain = () => {
        if (newDomain.trim() && !domains.includes(newDomain.trim())) {
            setDomains([...domains, newDomain.trim()]);
            setNewDomain("");
        }
    };

    const removeDomain = (domain: string) => {
        setDomains(domains.filter((d) => d !== domain));
    };

    const handleKeyPress = (e: React.KeyboardEvent) => {
        if (e.key === "Enter") {
            e.preventDefault();
            addDomain();
        }
    };

    // Construct live script tags
    const currentOrigin =
        typeof window !== "undefined" && window.location.origin
            ? window.location.origin
            : "http://localhost:3000";

    const liveToken = embedToken?.token || "YOUR_TOKEN";

    const liveChatScript = `<!-- AgentX Chat Widget -->
<script>
  (function(d, s, id) {
    var js, fjs = d.getElementsByTagName(s)[0];
    if (d.getElementById(id)) return;
    js = d.createElement(s); js.id = id;
    js.src = '${currentOrigin}/embed/agentx-chat-widget.js?token=${liveToken}&apiEndpoint=${currentOrigin}&buttonText=${encodeURIComponent(chatButtonText)}&buttonColor=${encodeURIComponent(chatButtonColor)}&position=${encodeURIComponent(chatPosition)}&title=${encodeURIComponent(chatTitle)}';
    js.async = true;
    fjs.parentNode.insertBefore(js, fjs);
  }(document, 'script', 'agentx-chat-widget'));
</script>`;

    const displayedScript =
        activeScriptType === "chat"
            ? embedToken?.chat_embed_script || liveChatScript
            : embedToken?.embed_script || "";

    const isCurrentSnippetActive = activeScriptType === "chat" ? chatEnabled : voiceEnabled;
    const isCurrentSnippetToggling = activeScriptType === "chat" ? togglingChat : togglingVoice;
    const handleCurrentSnippetToggle = activeScriptType === "chat" ? handleToggleChat : handleToggleVoice;

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-3xl w-full max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                    <div className="flex items-center justify-between">
                        <DialogTitle className="flex items-center gap-2">
                            <Rocket className="h-5 w-5 text-primary" />
                            Configure Widget
                        </DialogTitle>
                        <a
                            href={widgetType === "voice" ? WIDGET_MODE_DOCUMENTATION_URLS[embedMode] : "https://docs.agentx.so"}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors pr-6"
                        >
                            Docs
                            <ExternalLink className="h-3.5 w-3.5" />
                        </a>
                    </div>
                    <DialogDescription>
                        Add &quot;{workflowName}&quot; to any website with a simple script tag.
                    </DialogDescription>
                </DialogHeader>

                {loading ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <div className="space-y-6">
                        {/* Separate Widget Status Cards: Talk to Agent vs Chat with Agent */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {/* Voice Agent Toggle Card */}
                            <div
                                className={`flex items-center justify-between p-4 rounded-xl border transition-all ${
                                    voiceEnabled
                                        ? "bg-emerald-500/10 border-emerald-500/30"
                                        : "bg-muted/40 border-border"
                                }`}
                            >
                                <div className="flex items-start gap-3 min-w-0 pr-2">
                                    <div
                                        className={`p-2 rounded-lg shrink-0 mt-0.5 ${
                                            voiceEnabled
                                                ? "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400"
                                                : "bg-muted text-muted-foreground"
                                        }`}
                                    >
                                        <Mic className="h-5 w-5" />
                                    </div>
                                    <div className="space-y-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <span className="text-sm font-semibold text-foreground">
                                                Talk to Agent
                                            </span>
                                            <span
                                                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] font-medium ${
                                                    voiceEnabled
                                                        ? "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
                                                        : "bg-muted text-muted-foreground border"
                                                }`}
                                            >
                                                <span
                                                    className={`h-1.5 w-1.5 rounded-full ${
                                                        voiceEnabled ? "bg-emerald-500 animate-pulse" : "bg-muted-foreground"
                                                    }`}
                                                />
                                                {voiceEnabled ? "Live" : "Disabled"}
                                            </span>
                                        </div>
                                        <p className="text-xs text-muted-foreground leading-snug">
                                            {voiceEnabled
                                                ? "Voice button is live & visible on websites."
                                                : "Voice button is completely hidden on websites."}
                                        </p>
                                    </div>
                                </div>
                                <div className="flex items-center gap-1.5 pl-2 shrink-0">
                                    {togglingVoice && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                                    <Switch
                                        id="voice-widget-toggle"
                                        checked={voiceEnabled}
                                        onCheckedChange={handleToggleVoice}
                                        disabled={togglingVoice}
                                        aria-label="Toggle voice widget visibility"
                                    />
                                </div>
                            </div>

                            {/* Chat Agent Toggle Card */}
                            <div
                                className={`flex items-center justify-between p-4 rounded-xl border transition-all ${
                                    chatEnabled
                                        ? "bg-emerald-500/10 border-emerald-500/30"
                                        : "bg-muted/40 border-border"
                                }`}
                            >
                                <div className="flex items-start gap-3 min-w-0 pr-2">
                                    <div
                                        className={`p-2 rounded-lg shrink-0 mt-0.5 ${
                                            chatEnabled
                                                ? "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400"
                                                : "bg-muted text-muted-foreground"
                                        }`}
                                    >
                                        <MessageSquare className="h-5 w-5" />
                                    </div>
                                    <div className="space-y-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <span className="text-sm font-semibold text-foreground">
                                                Chat with Agent
                                            </span>
                                            <span
                                                className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] font-medium ${
                                                    chatEnabled
                                                        ? "bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
                                                        : "bg-muted text-muted-foreground border"
                                                }`}
                                            >
                                                <span
                                                    className={`h-1.5 w-1.5 rounded-full ${
                                                        chatEnabled ? "bg-emerald-500 animate-pulse" : "bg-muted-foreground"
                                                    }`}
                                                />
                                                {chatEnabled ? "Live" : "Disabled"}
                                            </span>
                                        </div>
                                        <p className="text-xs text-muted-foreground leading-snug">
                                            {chatEnabled
                                                ? "Chat bubble is live & visible on websites."
                                                : "Chat bubble is completely hidden on websites."}
                                        </p>
                                    </div>
                                </div>
                                <div className="flex items-center gap-1.5 pl-2 shrink-0">
                                    {togglingChat && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
                                    <Switch
                                        id="chat-widget-toggle"
                                        checked={chatEnabled}
                                        onCheckedChange={handleToggleChat}
                                        disabled={togglingChat}
                                        aria-label="Toggle chat widget visibility"
                                    />
                                </div>
                            </div>
                        </div>

                        <Separator />

                                {/* Allowed Domains */}
                                <div className="space-y-3">
                                    <Label>
                                        Allowed Domains
                                        <span className="text-xs text-muted-foreground ml-2">
                                            (leave empty to allow all domains)
                                        </span>
                                    </Label>

                                    {/* Domain Input */}
                                    <div className="flex gap-2">
                                        <Input
                                            placeholder="example.com or *.example.com"
                                            value={newDomain}
                                            onChange={(e) => setNewDomain(e.target.value)}
                                            onKeyPress={handleKeyPress}
                                        />
                                        <Button
                                            type="button"
                                            size="icon"
                                            variant="outline"
                                            onClick={addDomain}
                                            disabled={!newDomain.trim()}
                                        >
                                            <Plus className="h-4 w-4" />
                                        </Button>
                                    </div>

                                    {/* Domain List */}
                                    {domains.length > 0 && (
                                        <div className="space-y-2">
                                            {domains.map((domain, index) => (
                                                <div
                                                    key={index}
                                                    className="flex items-center justify-between bg-muted/50 rounded-lg px-3 py-2"
                                                >
                                                    <span className="text-sm font-mono">{domain}</span>
                                                    <Button
                                                        type="button"
                                                        size="icon"
                                                        variant="ghost"
                                                        className="h-6 w-6"
                                                        onClick={() => removeDomain(domain)}
                                                    >
                                                        <Trash2 className="h-3 w-3" />
                                                    </Button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                <Separator />

                                {/* Widget Type Selector */}
                                <div className="space-y-3">
                                    <Label className="text-sm font-semibold">Widget Type</Label>
                                    <div className="grid grid-cols-2 gap-3">
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setWidgetType("voice");
                                                setActiveScriptType("voice");
                                            }}
                                            className={`flex items-center justify-center gap-2.5 p-3 rounded-lg border-2 font-medium text-sm transition-all ${
                                                widgetType === "voice"
                                                    ? "border-primary bg-primary/10 text-foreground shadow-sm"
                                                    : "border-muted hover:border-muted-foreground/30 text-muted-foreground hover:text-foreground"
                                            }`}
                                        >
                                            <Mic className="h-4 w-4" />
                                            Talk to Agent (Voice)
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => {
                                                setWidgetType("chat");
                                                setActiveScriptType("chat");
                                            }}
                                            className={`flex items-center justify-center gap-2.5 p-3 rounded-lg border-2 font-medium text-sm transition-all ${
                                                widgetType === "chat"
                                                    ? "border-primary bg-primary/10 text-foreground shadow-sm"
                                                    : "border-muted hover:border-muted-foreground/30 text-muted-foreground hover:text-foreground"
                                            }`}
                                        >
                                            <MessageSquare className="h-4 w-4" />
                                            Chat Widget
                                        </button>
                                    </div>
                                </div>

                                {/* VOICE AGENT CONFIGURATION */}
                                {widgetType === "voice" && (
                                    <div className="space-y-6">
                                        {/* Embed Mode Selection */}
                                        <div className="space-y-4">
                                            <Label>Embed Mode</Label>
                                            <div className="grid grid-cols-3 gap-4">
                                                <button
                                                    type="button"
                                                    onClick={() => setEmbedMode("floating")}
                                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                                        embedMode === "floating"
                                                            ? "border-primary bg-primary/5"
                                                            : "border-muted hover:border-muted-foreground/20"
                                                    }`}
                                                >
                                                    <div className="space-y-1.5">
                                                        <div className="font-medium text-sm">Floating Widget</div>
                                                        <div className="text-xs text-muted-foreground leading-snug">
                                                            Shows as a button in corner of the page
                                                        </div>
                                                    </div>
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setEmbedMode("inline")}
                                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                                        embedMode === "inline"
                                                            ? "border-primary bg-primary/5"
                                                            : "border-muted hover:border-muted-foreground/20"
                                                    }`}
                                                >
                                                    <div className="space-y-1.5">
                                                        <div className="font-medium text-sm">Inline Component</div>
                                                        <div className="text-xs text-muted-foreground leading-snug">
                                                            Embeds directly in your page content
                                                        </div>
                                                    </div>
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setEmbedMode("headless")}
                                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                                        embedMode === "headless"
                                                            ? "border-primary bg-primary/5"
                                                            : "border-muted hover:border-muted-foreground/20"
                                                    }`}
                                                >
                                                    <div className="space-y-1.5">
                                                        <div className="font-medium text-sm">Headless (BYO UI)</div>
                                                        <div className="text-xs text-muted-foreground leading-snug">
                                                            Drive calls from your own buttons via JS API
                                                        </div>
                                                    </div>
                                                </button>
                                            </div>
                                        </div>

                                        {/* Configuration */}
                                        <div className="space-y-4">
                                            <Label>Configuration</Label>

                                            {embedMode !== "headless" && (
                                                <div className="grid grid-cols-2 gap-4">
                                                    <div className="space-y-2">
                                                        <Label htmlFor="button-text" className="text-sm">
                                                            Button Text
                                                        </Label>
                                                        <Input
                                                            id="button-text"
                                                            value={buttonText}
                                                            onChange={(e) => setButtonText(e.target.value)}
                                                            placeholder="Talk to Agent"
                                                            maxLength={40}
                                                        />
                                                    </div>
                                                    <div className="space-y-2">
                                                        <Label htmlFor="button-color" className="text-sm">
                                                            Button Color
                                                        </Label>
                                                        <div className="flex gap-2">
                                                            <Input
                                                                type="color"
                                                                value={buttonColor}
                                                                onChange={(e) => setButtonColor(e.target.value)}
                                                                className="w-14 h-10 cursor-pointer"
                                                            />
                                                            <Input
                                                                id="button-color"
                                                                value={buttonColor}
                                                                onChange={(e) => setButtonColor(e.target.value)}
                                                                className="flex-1"
                                                            />
                                                        </div>
                                                    </div>
                                                </div>
                                            )}

                                            {embedMode === "floating" && (
                                                <div className="space-y-2">
                                                    <Label htmlFor="position" className="text-sm">
                                                        Position
                                                    </Label>
                                                    <Select value={position} onValueChange={setPosition}>
                                                        <SelectTrigger id="position">
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="bottom-right">Bottom Right</SelectItem>
                                                            <SelectItem value="bottom-left">Bottom Left</SelectItem>
                                                            <SelectItem value="top-right">Top Right</SelectItem>
                                                            <SelectItem value="top-left">Top Left</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                            )}

                                            {embedMode === "inline" && (
                                                <div className="space-y-2">
                                                    <Label htmlFor="cta-text" className="text-sm">
                                                        Call to Action Text
                                                    </Label>
                                                    <Input
                                                        id="cta-text"
                                                        value={callToActionText}
                                                        onChange={(e) => setCallToActionText(e.target.value)}
                                                        placeholder="Click to start voice conversation"
                                                    />
                                                </div>
                                            )}

                                            {/* Centered Preview */}
                                            {embedMode === "floating" && (
                                                <div className="rounded-lg border bg-muted/30 p-6 flex items-center justify-center">
                                                    <button
                                                        type="button"
                                                        className="inline-flex items-center gap-2 rounded-full px-5 py-3 font-medium text-white shadow-lg whitespace-nowrap transition-transform hover:scale-105"
                                                        style={{ backgroundColor: buttonColor }}
                                                    >
                                                        <Mic className="h-4 w-4" />
                                                        {buttonText || "Talk to Agent"}
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}

                                {/* CHAT WIDGET CONFIGURATION (Just like Talk to Agent widget!) */}
                                {widgetType === "chat" && (
                                    <div className="space-y-6">
                                        {/* Embed Mode Selection */}
                                        <div className="space-y-4">
                                            <Label>Embed Mode</Label>
                                            <div className="grid grid-cols-2 gap-4">
                                                <button
                                                    type="button"
                                                    onClick={() => setChatEmbedMode("floating")}
                                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                                        chatEmbedMode === "floating"
                                                            ? "border-primary bg-primary/5"
                                                            : "border-muted hover:border-muted-foreground/20"
                                                    }`}
                                                >
                                                    <div className="space-y-1.5">
                                                        <div className="font-medium text-sm">Floating Widget</div>
                                                        <div className="text-xs text-muted-foreground leading-snug">
                                                            Shows as a button in corner of the page
                                                        </div>
                                                    </div>
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setChatEmbedMode("inline")}
                                                    className={`p-4 rounded-lg border-2 text-left transition-all ${
                                                        chatEmbedMode === "inline"
                                                            ? "border-primary bg-primary/5"
                                                            : "border-muted hover:border-muted-foreground/20"
                                                    }`}
                                                >
                                                    <div className="space-y-1.5">
                                                        <div className="font-medium text-sm">Inline Component</div>
                                                        <div className="text-xs text-muted-foreground leading-snug">
                                                            Embeds directly in your page content
                                                        </div>
                                                    </div>
                                                </button>
                                            </div>
                                        </div>

                                        {/* Configuration */}
                                        <div className="space-y-4">
                                            <Label>Configuration</Label>

                                            <div className="grid grid-cols-2 gap-4">
                                                <div className="space-y-2">
                                                    <Label htmlFor="chat-button-text" className="text-sm">
                                                        Button Text
                                                    </Label>
                                                    <Input
                                                        id="chat-button-text"
                                                        value={chatButtonText}
                                                        onChange={(e) => setChatButtonText(e.target.value)}
                                                        placeholder="Chat with Agent"
                                                        maxLength={40}
                                                    />
                                                </div>
                                                <div className="space-y-2">
                                                    <Label htmlFor="chat-button-color" className="text-sm">
                                                        Button Color
                                                    </Label>
                                                    <div className="flex gap-2">
                                                        <Input
                                                            type="color"
                                                            value={chatButtonColor}
                                                            onChange={(e) => setChatButtonColor(e.target.value)}
                                                            className="w-14 h-10 cursor-pointer"
                                                        />
                                                        <Input
                                                            id="chat-button-color"
                                                            value={chatButtonColor}
                                                            onChange={(e) => setChatButtonColor(e.target.value)}
                                                            className="flex-1 font-mono"
                                                        />
                                                    </div>
                                                </div>
                                            </div>

                                            <div className="grid grid-cols-2 gap-4">
                                                <div className="space-y-2">
                                                    <Label htmlFor="chat-position" className="text-sm">
                                                        Position
                                                    </Label>
                                                    <Select value={chatPosition} onValueChange={setChatPosition}>
                                                        <SelectTrigger id="chat-position">
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="bottom-right">Bottom Right</SelectItem>
                                                            <SelectItem value="bottom-left">Bottom Left</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                                <div className="space-y-2">
                                                    <Label htmlFor="chat-title" className="text-sm">
                                                        Chat Title
                                                    </Label>
                                                    <Input
                                                        id="chat-title"
                                                        value={chatTitle}
                                                        onChange={(e) => setChatTitle(e.target.value)}
                                                        placeholder="Chat Agent"
                                                        maxLength={40}
                                                    />
                                                </div>
                                            </div>

                                            {/* Centered Preview (Just like Talk to Agent widget!) */}
                                            <div className="rounded-lg border bg-muted/30 p-6 flex items-center justify-center">
                                                <button
                                                    type="button"
                                                    className="inline-flex items-center gap-2 rounded-full px-5 py-3 font-medium text-white shadow-lg whitespace-nowrap transition-transform hover:scale-105"
                                                    style={{ backgroundColor: chatButtonColor }}
                                                >
                                                    <MessageSquare className="h-4 w-4" />
                                                    {chatButtonText || "Chat with Agent"}
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                )}

                                <Separator />

                                {/* Save Button */}
                                <div className="flex justify-end">
                                    <Button onClick={handleSave} disabled={saving}>
                                        {saving ? (
                                            <>
                                                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                                Saving...
                                            </>
                                        ) : (
                                            "Save Configurations"
                                        )}
                                    </Button>
                                </div>

                                <Separator />

                                {/* Embed Script Code Box */}
                                {embedToken ? (
                                    <div className="space-y-4 rounded-xl border bg-card p-4 shadow-sm">
                                        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <Label className="text-sm font-semibold">Code Snippet</Label>
                                                <div className="inline-flex rounded-md border p-0.5 bg-muted/50 text-xs">
                                                    <button
                                                        type="button"
                                                        onClick={() => setActiveScriptType("chat")}
                                                        className={`px-2.5 py-1 rounded font-medium transition-all ${
                                                            activeScriptType === "chat"
                                                                ? "bg-background text-foreground shadow-sm"
                                                                : "text-muted-foreground hover:text-foreground"
                                                        }`}
                                                    >
                                                        Chat Widget
                                                    </button>
                                                    <button
                                                        type="button"
                                                        onClick={() => setActiveScriptType("voice")}
                                                        className={`px-2.5 py-1 rounded font-medium transition-all ${
                                                            activeScriptType === "voice"
                                                                ? "bg-background text-foreground shadow-sm"
                                                                : "text-muted-foreground hover:text-foreground"
                                                        }`}
                                                    >
                                                        Voice Agent
                                                    </button>
                                                </div>
                                            </div>

                                            {/* Snippet Toggle Switch + Copy Button */}
                                            <div className="flex items-center gap-2.5">
                                                <div className="flex items-center gap-2 bg-muted/50 px-2.5 py-1 rounded-lg border">
                                                    <span className="text-xs font-medium flex items-center gap-1.5">
                                                        {isCurrentSnippetActive ? (
                                                            <>
                                                                <Eye className="h-3.5 w-3.5 text-emerald-500" />
                                                                <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
                                                                    {activeScriptType === "chat" ? "Chat Active" : "Voice Active"}
                                                                </span>
                                                            </>
                                                        ) : (
                                                            <>
                                                                <EyeOff className="h-3.5 w-3.5 text-muted-foreground" />
                                                                <span className="text-muted-foreground">
                                                                    {activeScriptType === "chat" ? "Chat Disabled" : "Voice Disabled"}
                                                                </span>
                                                            </>
                                                        )}
                                                    </span>
                                                    {isCurrentSnippetToggling && (
                                                        <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                                                    )}
                                                    <Switch
                                                        checked={isCurrentSnippetActive}
                                                        onCheckedChange={handleCurrentSnippetToggle}
                                                        disabled={isCurrentSnippetToggling}
                                                        aria-label={`Toggle ${activeScriptType === "chat" ? "chat" : "voice"} snippet visibility`}
                                                    />
                                                </div>

                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => copyToClipboard(displayedScript)}
                                                >
                                                    {copied ? (
                                                        <>
                                                            <Check className="h-4 w-4 mr-1 text-emerald-500" />
                                                            Copied!
                                                        </>
                                                    ) : (
                                                        <>
                                                            <Copy className="h-4 w-4 mr-1" />
                                                            Copy Code
                                                        </>
                                                    )}
                                                </Button>
                                            </div>
                                        </div>

                                        {/* State Banner / Explainer */}
                                        {isCurrentSnippetActive ? (
                                            <div className="flex items-center gap-2 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-800 dark:text-emerald-200">
                                                <span className="h-2 w-2 rounded-full bg-emerald-500 shrink-0" />
                                                <span>
                                                    <strong>{activeScriptType === "chat" ? "Chat widget" : "Voice agent"} is active:</strong> The {activeScriptType === "chat" ? "chat bubble" : "voice button"} is visible and operating on websites using this snippet.
                                                </span>
                                            </div>
                                        ) : (
                                            <div className="flex items-center gap-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-xs text-amber-800 dark:text-amber-200">
                                                <AlertCircle className="h-4 w-4 shrink-0 text-amber-500" />
                                                <span>
                                                    <strong>{activeScriptType === "chat" ? "Chat widget" : "Voice agent"} is disabled (invisible):</strong> The {activeScriptType === "chat" ? "chat bubble" : "voice button"} is hidden on your website. Flip the toggle back ON anytime to make it reappear without modifying website code.
                                                </span>
                                            </div>
                                        )}

                                        <div className="relative">
                                            <pre
                                                className={`border rounded-lg p-4 text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all transition-opacity ${
                                                    isCurrentSnippetActive ? "bg-muted/70 opacity-100" : "bg-muted/30 opacity-70"
                                                }`}
                                            >
                                                <code>{displayedScript}</code>
                                            </pre>
                                        </div>
                                        <p className="text-xs text-muted-foreground">
                                            Paste this snippet before the closing <code className="font-mono bg-muted px-1 py-0.5 rounded">&lt;/body&gt;</code> tag on your website. Disabling the toggle makes the widget icon disappear automatically without code changes.
                                        </p>
                                    </div>
                                ) : (
                                    <div className="space-y-3">
                                        <Label className="text-muted-foreground">Embed Code</Label>
                                        <div className="rounded-lg border border-dashed bg-muted/30 px-4 py-8 text-center text-sm text-muted-foreground">
                                            Click <span className="font-medium text-foreground">Save Configurations</span> to generate the embed script.
                                        </div>
                                    </div>
                                )}
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}
