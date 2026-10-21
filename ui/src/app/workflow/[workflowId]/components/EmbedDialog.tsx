import {
    Check,
    Copy,
    ExternalLink,
    Globe,
    Layers,
    Loader2,
    Lock,
    MessageSquare,
    Mic,
    Plus,
    RefreshCw,
    Rocket,
    Send,
    ShieldCheck,
    Trash2,
    X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost,
    deactivateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenDelete,
    getEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenGet,
} from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
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
    const [embedToken, setEmbedToken] = useState<EmbedToken | null>(null);
    const [copied, setCopied] = useState(false);

    // Widget Category: "chat" (Priority 3 & 4) vs "voice" (Dograh Voice)
    const [widgetType, setWidgetType] = useState<"chat" | "voice">("chat");

    // Form state (Shared & Voice)
    const [isEnabled, setIsEnabled] = useState(false);
    const [domains, setDomains] = useState<string[]>([]);
    const [newDomain, setNewDomain] = useState("");
    const [embedMode, setEmbedMode] = useState<"floating" | "inline" | "headless">("floating");
    const [position, setPosition] = useState("bottom-right");
    const [buttonText, setButtonText] = useState("Talk to Agent");
    const [buttonColor, setButtonColor] = useState("#10b981");
    const [callToActionText, setCallToActionText] = useState("Click to start voice conversation");

    // Form state (Chat Widget - Priority 3 & 4)
    const [chatTitle, setChatTitle] = useState(workflowName || "AI Assistant");
    const [chatSubtitle, setChatSubtitle] = useState("Online • Ready to assist");
    const [chatButtonColor, setChatButtonColor] = useState("#2563eb");
    const [chatPosition, setChatPosition] = useState("bottom-right");
    const [chatButtonText, setChatButtonText] = useState("Chat with us");

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

                // Load settings
                if (data.settings) {
                    const settings = data.settings as Record<string, string>;
                    if (settings.widgetType === "voice" || settings.widgetType === "chat") {
                        setWidgetType(settings.widgetType);
                    }
                    setEmbedMode((settings.embedMode as "floating" | "inline" | "headless") || "floating");
                    setPosition(settings.position || "bottom-right");
                    setButtonText(settings.buttonText || "Talk to Agent");
                    setButtonColor(settings.buttonColor || "#10b981");
                    setCallToActionText(settings.callToActionText || "Click to start voice conversation");

                    // Chat settings
                    if (settings.chatTitle) setChatTitle(settings.chatTitle);
                    if (settings.chatSubtitle) setChatSubtitle(settings.chatSubtitle);
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

    const handleSave = async () => {
        setSaving(true);
        try {
            if (!isEnabled && embedToken) {
                // Deactivate token
                await deactivateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenDelete({
                    path: { workflow_id: workflowId },
                });
                setEmbedToken(null);
            } else if (isEnabled) {
                // Create or update token
                const response = await createOrUpdateEmbedTokenApiV1WorkflowWorkflowIdEmbedTokenPost({
                    path: { workflow_id: workflowId },
                    body: {
                        allowed_domains: domains.length > 0 ? domains : null,
                        settings: {
                            widgetType,
                            embedMode,
                            position,
                            buttonText,
                            buttonColor,
                            callToActionText,
                            size: "medium",
                            autoStart: false,
                            containerId: embedMode === "inline" ? "dograh-inline-container" : undefined,
                            chatTitle,
                            chatSubtitle,
                            chatButtonColor,
                            chatPosition,
                            chatButtonText,
                        },
                        usage_limit: null,
                        expires_in_days: null,
                    },
                });

                if (response.data) {
                    setEmbedToken(response.data as EmbedToken);
                }
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

    // Construct live chat embed script
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
    js.src = '${currentOrigin}/embed/agentx-chat-widget.js?token=${liveToken}&environment=local&apiEndpoint=${currentOrigin}';
    js.async = true;
    fjs.parentNode.insertBefore(js, fjs);
  }(document, 'script', 'agentx-chat-widget'));
</script>`;

    const activeEmbedScript =
        widgetType === "chat"
            ? embedToken?.chat_embed_script || liveChatScript
            : embedToken?.embed_script || "";

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-4xl w-full max-h-[90vh] overflow-y-auto">
                <DialogHeader>
                    <div className="flex items-center justify-between">
                        <DialogTitle className="flex items-center gap-2">
                            <Rocket className="h-5 w-5 text-primary" />
                            Configure Embed Widget
                        </DialogTitle>
                        {widgetType === "voice" && (
                            <a
                                href={WIDGET_MODE_DOCUMENTATION_URLS[embedMode]}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors pr-6"
                            >
                                Docs
                                <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                        )}
                    </div>
                    <DialogDescription>
                        Generate an embeddable script snippet to deploy &quot;{workflowName}&quot; as an interactive Chat Widget or Voice Agent on any web page.
                    </DialogDescription>
                </DialogHeader>

                {loading ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                    </div>
                ) : (
                    <div className="space-y-6">
                        {/* Enable/Disable Toggle */}
                        <div className="flex items-center justify-between p-4 rounded-lg bg-muted/40 border">
                            <div className="space-y-0.5">
                                <Label htmlFor="embed-enabled" className="text-base font-semibold">Enable Embedding</Label>
                                <p className="text-sm text-muted-foreground">
                                    Allow this workflow to be embedded across web applications via script tag
                                </p>
                            </div>
                            <Switch
                                id="embed-enabled"
                                checked={isEnabled}
                                onCheckedChange={setIsEnabled}
                            />
                        </div>

                        {isEnabled && (
                            <>
                                {/* Widget Category Selector (Priority 3) */}
                                <div className="space-y-2">
                                    <Label className="text-sm font-semibold">Select Widget Type</Label>
                                    <div className="grid grid-cols-2 gap-4">
                                        <button
                                            type="button"
                                            onClick={() => setWidgetType("chat")}
                                            className={`p-4 rounded-xl border-2 text-left transition-all ${
                                                widgetType === "chat"
                                                    ? "border-blue-600 bg-blue-50/50 dark:bg-blue-950/20 shadow-sm"
                                                    : "border-muted hover:border-muted-foreground/30 bg-card"
                                            }`}
                                        >
                                            <div className="flex items-center gap-2 mb-1.5">
                                                <div className="p-1.5 rounded-md bg-blue-500/10 text-blue-600">
                                                    <MessageSquare className="h-5 w-5" />
                                                </div>
                                                <span className="font-semibold text-foreground">Chat Widget</span>
                                                <Badge variant="secondary" className="text-[10px] ml-auto bg-blue-100 text-blue-700 dark:bg-blue-900/60 dark:text-blue-300">
                                                    Priority 3 & 4
                                                </Badge>
                                            </div>
                                            <p className="text-xs text-muted-foreground leading-relaxed">
                                                Embeddable text conversational assistant with sessionStorage navigation persistence, state hygiene, and backend teardown.
                                            </p>
                                        </button>

                                        <button
                                            type="button"
                                            onClick={() => setWidgetType("voice")}
                                            className={`p-4 rounded-xl border-2 text-left transition-all ${
                                                widgetType === "voice"
                                                    ? "border-emerald-600 bg-emerald-50/50 dark:bg-emerald-950/20 shadow-sm"
                                                    : "border-muted hover:border-muted-foreground/30 bg-card"
                                            }`}
                                        >
                                            <div className="flex items-center gap-2 mb-1.5">
                                                <div className="p-1.5 rounded-md bg-emerald-500/10 text-emerald-600">
                                                    <Mic className="h-5 w-5" />
                                                </div>
                                                <span className="font-semibold text-foreground">Voice Agent</span>
                                                <Badge variant="outline" className="text-[10px] ml-auto">
                                                    WebRTC
                                                </Badge>
                                            </div>
                                            <p className="text-xs text-muted-foreground leading-relaxed">
                                                Ultra-low latency two-way audio voice agent supporting floating, inline, and headless trigger modes.
                                            </p>
                                        </button>
                                    </div>
                                </div>

                                <Separator />

                                {/* Allowed Domains */}
                                <div className="space-y-3">
                                    <div className="flex items-center justify-between">
                                        <Label className="text-sm font-semibold">
                                            Allowed Domains
                                            <span className="text-xs font-normal text-muted-foreground ml-2">
                                                (leave empty to permit all host origins)
                                            </span>
                                        </Label>
                                        {domains.length === 0 && (
                                            <Badge variant="outline" className="text-[11px] text-muted-foreground">
                                                Universal Access (All Domains)
                                            </Badge>
                                        )}
                                    </div>

                                    {/* Domain Input */}
                                    <div className="flex gap-2">
                                        <Input
                                            placeholder="example.com or localhost:3000"
                                            value={newDomain}
                                            onChange={(e) => setNewDomain(e.target.value)}
                                            onKeyPress={handleKeyPress}
                                        />
                                        <Button
                                            type="button"
                                            size="sm"
                                            variant="secondary"
                                            onClick={addDomain}
                                            disabled={!newDomain.trim()}
                                        >
                                            <Plus className="h-4 w-4 mr-1" />
                                            Add Domain
                                        </Button>
                                    </div>

                                    {/* Domain List */}
                                    {domains.length > 0 && (
                                        <div className="flex flex-wrap gap-2 pt-1">
                                            {domains.map((domain, index) => (
                                                <div
                                                    key={index}
                                                    className="inline-flex items-center gap-1.5 bg-muted rounded-md px-2.5 py-1 text-xs font-mono"
                                                >
                                                    <span>{domain}</span>
                                                    <button
                                                        type="button"
                                                        onClick={() => removeDomain(domain)}
                                                        className="text-muted-foreground hover:text-foreground"
                                                    >
                                                        <X className="h-3 w-3" />
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                <Separator />

                                {/* CHAT WIDGET CONFIGURATION & PREVIEW */}
                                {widgetType === "chat" && (
                                    <div className="space-y-6">
                                        <div className="space-y-4">
                                            <Label className="text-sm font-semibold">Chat Widget Appearance</Label>

                                            <div className="grid grid-cols-2 gap-4">
                                                <div className="space-y-2">
                                                    <Label htmlFor="chat-title" className="text-xs">Chat Header Title</Label>
                                                    <Input
                                                        id="chat-title"
                                                        value={chatTitle}
                                                        onChange={(e) => setChatTitle(e.target.value)}
                                                        placeholder="AI Assistant"
                                                    />
                                                </div>

                                                <div className="space-y-2">
                                                    <Label htmlFor="chat-subtitle" className="text-xs">Subtitle Status</Label>
                                                    <Input
                                                        id="chat-subtitle"
                                                        value={chatSubtitle}
                                                        onChange={(e) => setChatSubtitle(e.target.value)}
                                                        placeholder="Online • Ready to assist"
                                                    />
                                                </div>

                                                <div className="space-y-2">
                                                    <Label htmlFor="chat-color" className="text-xs">Theme & Button Color</Label>
                                                    <div className="flex gap-2">
                                                        <Input
                                                            type="color"
                                                            value={chatButtonColor}
                                                            onChange={(e) => setChatButtonColor(e.target.value)}
                                                            className="w-12 h-10 p-1 cursor-pointer"
                                                        />
                                                        <Input
                                                            id="chat-color"
                                                            value={chatButtonColor}
                                                            onChange={(e) => setChatButtonColor(e.target.value)}
                                                            className="flex-1 font-mono text-sm"
                                                        />
                                                    </div>
                                                </div>

                                                <div className="space-y-2">
                                                    <Label htmlFor="chat-pos" className="text-xs">Screen Position</Label>
                                                    <Select value={chatPosition} onValueChange={setChatPosition}>
                                                        <SelectTrigger id="chat-pos">
                                                            <SelectValue />
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="bottom-right">Bottom Right (Standard)</SelectItem>
                                                            <SelectItem value="bottom-left">Bottom Left</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Interactive Visual Preview */}
                                        <div className="space-y-2">
                                            <Label className="text-xs text-muted-foreground uppercase tracking-wider font-semibold">
                                                Live Widget Preview
                                            </Label>
                                            <div className="rounded-xl border bg-muted/20 p-6 flex items-center justify-center min-h-[320px]">
                                                {/* Simulated Chat Popup */}
                                                <div className="w-[320px] rounded-xl border shadow-xl bg-card overflow-hidden text-card-foreground">
                                                    {/* Header */}
                                                    <div
                                                        className="px-4 py-3 flex items-center justify-between text-white"
                                                        style={{ backgroundColor: chatButtonColor }}
                                                    >
                                                        <div className="flex items-center gap-2.5">
                                                            <div className="w-8 h-8 rounded-full bg-white/20 flex items-center justify-center font-bold text-xs text-white">
                                                                AI
                                                            </div>
                                                            <div>
                                                                <h4 className="text-sm font-semibold leading-tight">{chatTitle}</h4>
                                                                <p className="text-[11px] opacity-90 leading-tight">{chatSubtitle}</p>
                                                            </div>
                                                        </div>
                                                        <div className="w-6 h-6 rounded-md hover:bg-black/10 flex items-center justify-center cursor-pointer">
                                                            <X className="h-3.5 w-3.5 text-white" />
                                                        </div>
                                                    </div>

                                                    {/* Messages Area */}
                                                    <div className="p-3.5 space-y-2.5 bg-background/50 text-xs min-h-[140px] flex flex-col justify-end">
                                                        <div className="max-w-[85%] rounded-lg px-3 py-2 bg-muted text-foreground self-start">
                                                            Hello! How can I assist you today?
                                                        </div>
                                                        <div
                                                            className="max-w-[85%] rounded-lg px-3 py-2 text-white self-end font-medium"
                                                            style={{ backgroundColor: chatButtonColor }}
                                                        >
                                                            Can you show me our active workflows?
                                                        </div>
                                                    </div>

                                                    {/* Footer Input */}
                                                    <div className="p-2.5 border-t bg-card flex items-center gap-2">
                                                        <div className="flex-1 bg-muted/60 rounded-md px-3 py-1.5 text-xs text-muted-foreground">
                                                            Type your message...
                                                        </div>
                                                        <div
                                                            className="w-7 h-7 rounded-md flex items-center justify-center text-white"
                                                            style={{ backgroundColor: chatButtonColor }}
                                                        >
                                                            <Send className="h-3.5 w-3.5" />
                                                        </div>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Specification Highlights */}
                                        <div className="grid grid-cols-2 gap-3 p-4 rounded-xl border bg-muted/30">
                                            <div className="flex items-start gap-2.5">
                                                <Globe className="h-4 w-4 text-blue-500 mt-0.5 shrink-0" />
                                                <div>
                                                    <p className="text-xs font-semibold">Global Scope</p>
                                                    <p className="text-[11px] text-muted-foreground">Persists across all application routes seamlessly.</p>
                                                </div>
                                            </div>
                                            <div className="flex items-start gap-2.5">
                                                <RefreshCw className="h-4 w-4 text-emerald-500 mt-0.5 shrink-0" />
                                                <div>
                                                    <p className="text-xs font-semibold">Navigation Persistence</p>
                                                    <p className="text-[11px] text-muted-foreground">In-chat links navigate in same tab keeping chat open.</p>
                                                </div>
                                            </div>
                                            <div className="flex items-start gap-2.5">
                                                <ShieldCheck className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
                                                <div>
                                                    <p className="text-xs font-semibold">State Hygiene & Teardown</p>
                                                    <p className="text-[11px] text-muted-foreground">Closing widget terminates server session and resets context.</p>
                                                </div>
                                            </div>
                                            <div className="flex items-start gap-2.5">
                                                <Lock className="h-4 w-4 text-purple-500 mt-0.5 shrink-0" />
                                                <div>
                                                    <p className="text-xs font-semibold">Singleton & sessionStorage</p>
                                                    <p className="text-[11px] text-muted-foreground">Tab-scoped storage with zero cross-tab memory bleed.</p>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                )}

                                {/* VOICE WIDGET CONFIGURATION */}
                                {widgetType === "voice" && (
                                    <div className="space-y-6">
                                        {/* Embed Mode Selection */}
                                        <div className="space-y-4">
                                            <Label className="text-sm font-semibold">Voice Embed Mode</Label>
                                            <div className="grid grid-cols-3 gap-4">
                                                <button
                                                    type="button"
                                                    onClick={() => setEmbedMode("floating")}
                                                    className={`p-4 rounded-lg border-2 transition-all ${
                                                        embedMode === "floating"
                                                            ? "border-primary bg-primary/5"
                                                            : "border-muted hover:border-muted-foreground/20"
                                                    }`}
                                                >
                                                    <div className="space-y-2">
                                                        <div className="font-medium">Floating Widget</div>
                                                        <div className="text-xs text-muted-foreground">
                                                            Shows as a button in corner of the page
                                                        </div>
                                                    </div>
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setEmbedMode("inline")}
                                                    className={`p-4 rounded-lg border-2 transition-all ${
                                                        embedMode === "inline"
                                                            ? "border-primary bg-primary/5"
                                                            : "border-muted hover:border-muted-foreground/20"
                                                    }`}
                                                >
                                                    <div className="space-y-2">
                                                        <div className="font-medium">Inline Component</div>
                                                        <div className="text-xs text-muted-foreground">
                                                            Embeds directly in your page content
                                                        </div>
                                                    </div>
                                                </button>
                                                <button
                                                    type="button"
                                                    onClick={() => setEmbedMode("headless")}
                                                    className={`p-4 rounded-lg border-2 transition-all ${
                                                        embedMode === "headless"
                                                            ? "border-primary bg-primary/5"
                                                            : "border-muted hover:border-muted-foreground/20"
                                                    }`}
                                                >
                                                    <div className="space-y-2">
                                                        <div className="font-medium">Headless (BYO UI)</div>
                                                        <div className="text-xs text-muted-foreground">
                                                            Drive calls from your own UI buttons via JS API
                                                        </div>
                                                    </div>
                                                </button>
                                            </div>
                                        </div>

                                        {/* Voice options */}
                                        {embedMode !== "headless" && (
                                            <div className="grid grid-cols-2 gap-4">
                                                <div className="space-y-2">
                                                    <Label htmlFor="button-text" className="text-sm">Button Text</Label>
                                                    <Input
                                                        id="button-text"
                                                        value={buttonText}
                                                        onChange={(e) => setButtonText(e.target.value)}
                                                        placeholder="Talk to Agent"
                                                        maxLength={40}
                                                    />
                                                </div>
                                                <div className="space-y-2">
                                                    <Label htmlFor="button-color" className="text-sm">Button Color</Label>
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
                                                <Label htmlFor="position" className="text-sm">Position</Label>
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
                                                <Label htmlFor="cta-text" className="text-sm">Call to Action Text</Label>
                                                <Input
                                                    id="cta-text"
                                                    value={callToActionText}
                                                    onChange={(e) => setCallToActionText(e.target.value)}
                                                    placeholder="Click to start voice conversation"
                                                />
                                            </div>
                                        )}

                                        {/* Voice Preview */}
                                        {embedMode === "floating" && (
                                            <div className="rounded-lg border bg-muted/30 p-6 flex items-center justify-center">
                                                <button
                                                    className="inline-flex items-center gap-2 rounded-full px-5 py-3 font-medium text-white shadow-lg whitespace-nowrap"
                                                    style={{ backgroundColor: buttonColor }}
                                                >
                                                    <Mic className="h-4 w-4" />
                                                    {buttonText || "Talk to Agent"}
                                                </button>
                                            </div>
                                        )}
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

                                {/* Embed Script Code Box */}
                                {embedToken && embedToken.is_active ? (
                                    <>
                                        <Separator />
                                        <div className="space-y-3">
                                            <div className="flex items-center justify-between">
                                                <div className="space-y-0.5">
                                                    <Label className="text-sm font-semibold">
                                                        {widgetType === "chat" ? "AgentX Chat Widget Snippet" : "Dograh Voice Widget Snippet"}
                                                    </Label>
                                                    <p className="text-xs text-muted-foreground">
                                                        Copy this script tag into the HTML of your website or application
                                                    </p>
                                                </div>
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => copyToClipboard(activeEmbedScript)}
                                                >
                                                    {copied ? (
                                                        <>
                                                            <Check className="h-4 w-4 mr-1 text-emerald-500" />
                                                            Copied!
                                                        </>
                                                    ) : (
                                                        <>
                                                            <Copy className="h-4 w-4 mr-1" />
                                                            Copy Script Tag
                                                        </>
                                                    )}
                                                </Button>
                                            </div>
                                            <div className="relative">
                                                <pre className="bg-muted/70 border rounded-lg p-4 text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all">
                                                    <code>{activeEmbedScript}</code>
                                                </pre>
                                            </div>
                                            <p className="text-xs text-muted-foreground">
                                                Changes saved to configurations will take effect immediately without requiring code re-embedding.
                                            </p>
                                        </div>
                                    </>
                                ) : (
                                    <>
                                        <Separator />
                                        <div className="space-y-3">
                                            <Label className="text-muted-foreground">Embed Code</Label>
                                            <div className="rounded-lg border border-dashed bg-muted/30 px-4 py-8 text-center text-sm text-muted-foreground">
                                                Click <span className="font-medium text-foreground">Save Configurations</span> to activate your token and generate the embed script.
                                            </div>
                                        </div>
                                    </>
                                )}
                            </>
                        )}
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}
