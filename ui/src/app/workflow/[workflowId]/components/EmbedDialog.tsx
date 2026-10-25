import {
    Check,
    Copy,
    ExternalLink,
    Loader2,
    MessageSquare,
    Mic,
    Plus,
    Rocket,
    Trash2,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

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

    // Widget Type: "voice" (Talk to Agent) vs "chat" (Chat Widget)
    const [widgetType, setWidgetType] = useState<"voice" | "chat">("voice");

    // Form state (Shared & Voice)
    const [isEnabled, setIsEnabled] = useState(false);
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

                // Load settings
                if (data.settings) {
                    const settings = data.settings as Record<string, string>;
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
                            chatEmbedMode,
                            chatTitle,
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
                        {/* Enable/Disable Toggle */}
                        <div className="flex items-center justify-between p-4 rounded-lg bg-muted/40 border">
                            <div className="space-y-0.5">
                                <Label htmlFor="embed-enabled" className="text-base font-semibold">
                                    Enable Embedding
                                </Label>
                                <p className="text-sm text-muted-foreground">
                                    Allow this workflow to be embedded on external websites
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

                                {/* Embed Script Code Box */}
                                {embedToken && embedToken.is_active ? (
                                    <>
                                        <Separator />
                                        <div className="space-y-3">
                                            <div className="flex items-center justify-between">
                                                <div className="flex items-center gap-2">
                                                    <Label className="text-sm font-semibold">Embed Code</Label>
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
                                            <div className="relative">
                                                <pre className="bg-muted/70 border rounded-lg p-4 text-xs font-mono overflow-x-auto whitespace-pre-wrap break-all">
                                                    <code>{displayedScript}</code>
                                                </pre>
                                            </div>
                                            <p className="text-xs text-muted-foreground">
                                                Add this script to your website&apos;s HTML to enable the widget. Configuration changes will apply automatically without re-embedding.
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
