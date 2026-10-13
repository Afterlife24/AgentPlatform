"use client";

import {
    ArrowDownLeft,
    ArrowUpRight,
    ChevronLeft,
    ChevronRight,
    CreditCard,
    ExternalLink,
    Info,
    Receipt,
    RefreshCw,
    Scale,
    ShieldAlert,
    TriangleAlert,
    Wallet,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { createMpsCreditPurchaseUrlApiV1OrganizationsUsageMpsCreditsPurchaseUrlPost, getBillingCreditsApiV1OrganizationsBillingCreditsGet } from "@/client/sdk.gen";
import type { MpsBillingCreditsResponse, MpsCreditLedgerEntryResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useAppConfig } from "@/context/AppConfigContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import {
    entryKind,
    entryLabel,
    formatCurrency,
    formatCurrencyPrecise,
    formatDateTime,
    formatMinor,
    formatTokens,
    getPageFromSearchParams,
    runHref,
} from "./lib";

const LEDGER_PAGE_SIZE = 50;

const formatCredits = (value: number | null | undefined) =>
    (value ?? 0).toLocaleString(undefined, {
        maximumFractionDigits: 2,
        minimumFractionDigits: 0,
    });

export default function BillingPage() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const auth = useAuth();
    const { config } = useAppConfig();
    const [credits, setCredits] = useState<MpsBillingCreditsResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [purchasing, setPurchasing] = useState(false);
    const [currentPage, setCurrentPage] = useState(() => getPageFromSearchParams(searchParams));

    const isBillingV2 = credits?.billing_version === "v2";
    const isOssMode = config?.deploymentMode === "oss";
    const canPurchaseCredits = isBillingV2 && !isOssMode;

    const currency = credits?.account?.currency ?? "USD";
    const balance = credits?.remaining_credits ?? 0;
    const totalSpent = credits?.total_credits_used ?? 0;
    const lowBalance = Boolean(credits?.low_balance);
    const isBlocked = Boolean(credits?.is_blocked);

    // Legacy (hosted non-v2) still shows a quota progress bar.
    const totalQuota = credits?.total_quota ?? 0;
    const usagePercent = totalQuota > 0 ? Math.min(100, Math.round((totalSpent / totalQuota) * 100)) : 0;

    const ledgerEntries = useMemo(() => credits?.ledger_entries ?? [], [credits?.ledger_entries]);
    const ledgerPage = credits?.page ?? currentPage;
    const ledgerTotalCount = credits?.total_count ?? ledgerEntries.length;
    const ledgerTotalPages = credits?.total_pages ?? 0;

    const fetchCredits = useCallback(
        async (page: number, { silent = false }: { silent?: boolean } = {}) => {
            if (auth.loading) return;
            if (!auth.isAuthenticated) {
                setLoading(false);
                return;
            }

            if (silent) setRefreshing(true);
            else setLoading(true);

            try {
                const response = await getBillingCreditsApiV1OrganizationsBillingCreditsGet({
                    query: { page, limit: LEDGER_PAGE_SIZE },
                });
                if (response.error) {
                    throw new Error(detailFromError(response.error, "Failed to load billing"));
                }
                setCredits(response.data ?? null);
            } catch (error) {
                console.error("Failed to fetch billing:", error);
                toast.error(error instanceof Error ? error.message : "Failed to load billing");
            } finally {
                setLoading(false);
                setRefreshing(false);
            }
        },
        [auth.isAuthenticated, auth.loading],
    );

    useEffect(() => {
        const nextPage = getPageFromSearchParams(searchParams);
        setCurrentPage((prev) => (prev === nextPage ? prev : nextPage));
    }, [searchParams]);

    useEffect(() => {
        fetchCredits(currentPage);
    }, [currentPage, fetchCredits]);

    const handleRefresh = () => fetchCredits(currentPage, { silent: true });

    const updateUrlPage = useCallback(
        (page: number) => {
            const params = new URLSearchParams(searchParams.toString());
            if (page > 1) params.set("page", page.toString());
            else params.delete("page");
            const qs = params.toString();
            router.push(qs ? `/billing?${qs}` : "/billing");
        },
        [router, searchParams],
    );

    const handlePageChange = (page: number) => {
        const nextPage = Math.max(1, page);
        setCurrentPage(nextPage);
        updateUrlPage(nextPage);
    };

    const handlePurchaseCredits = async () => {
        if (!canPurchaseCredits) return;
        setPurchasing(true);
        try {
            const response = await createMpsCreditPurchaseUrlApiV1OrganizationsUsageMpsCreditsPurchaseUrlPost();
            const checkoutUrl = response.data?.checkout_url;
            if (!checkoutUrl) throw new Error("Missing checkout URL");
            window.location.href = checkoutUrl;
        } catch (error) {
            console.error("Failed to create purchase URL:", error);
            toast.error("Failed to open checkout");
            setPurchasing(false);
        }
    };

    if (loading) {
        return (
            <div className="container mx-auto p-6 space-y-6">
                <div className="space-y-2">
                    <Skeleton className="h-9 w-40" />
                    <Skeleton className="h-5 w-96 max-w-full" />
                </div>
                <div className="grid gap-4 md:grid-cols-2">
                    <Skeleton className="h-36 rounded-lg" />
                    <Skeleton className="h-36 rounded-lg" />
                </div>
                <Skeleton className="h-80 rounded-lg" />
            </div>
        );
    }

    const balanceLabel = isBillingV2 ? "Balance" : "Credits remaining";
    const spentLabel = isBillingV2 ? "Total spent" : "Credits used";

    return (
        <div className="container mx-auto p-6 space-y-6">
            {/* Header */}
            <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                <div>
                    <h1 className="text-3xl font-bold mb-2">Billing</h1>
                    <p className="text-muted-foreground">
                        {isBillingV2
                            ? "Your balance, payments, and what each call cost."
                            : "Credits, balance, and account usage for your organization."}
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Button variant="outline" onClick={handleRefresh} disabled={refreshing}>
                        <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? "animate-spin" : ""}`} />
                        Refresh
                    </Button>
                    {canPurchaseCredits && (
                        <Button onClick={handlePurchaseCredits} disabled={purchasing}>
                            <CreditCard className="h-4 w-4 mr-2" />
                            {purchasing ? "Opening..." : "Add Credits"}
                        </Button>
                    )}
                </div>
            </div>

            {/* Suspended account notice (v2) */}
            {isBillingV2 && isBlocked && (
                <NoticeBanner
                    tone="rose"
                    icon={<ShieldAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />}
                    title="Your account is on hold"
                >
                    New calls and chats are paused. Please contact your account administrator to
                    restore service.
                </NoticeBanner>
            )}

            {/* Low-balance warning (v2, R8.5) */}
            {isBillingV2 && lowBalance && !isBlocked && (
                <NoticeBanner
                    tone="amber"
                    icon={<TriangleAlert className="mt-0.5 h-4 w-4 flex-shrink-0" />}
                    title="Low balance"
                >
                    Your balance is running low. Top up with your account administrator to avoid
                    interruptions to your calls and chats.
                </NoticeBanner>
            )}

            {/* OSS banner: point at the operator, not a self-service gateway (R8.6) */}
            {isOssMode && (
                <NoticeBanner
                    tone="sky"
                    icon={<Info className="mt-0.5 h-4 w-4 flex-shrink-0" />}
                    title="Top-ups are handled by your account administrator"
                >
                    This self-hosted workspace has no online checkout. To add funds, contact the
                    administrator who manages your account — they record your payment and your
                    balance updates here automatically.
                </NoticeBanner>
            )}

            {/* Summary cards */}
            <div className="grid gap-4 md:grid-cols-2">
                <Card className={cn(isBillingV2 && (isBlocked || lowBalance) && "border-amber-300 dark:border-amber-900/60")}>
                    <CardHeader className="pb-2">
                        <CardDescription>{balanceLabel}</CardDescription>
                        <CardTitle className="flex items-center gap-2 text-3xl">
                            <Wallet className="h-6 w-6 text-muted-foreground" />
                            {isBillingV2 ? (
                                <span className={cn(lowBalance && "text-amber-600 dark:text-amber-400")}>
                                    {formatCurrency(balance, currency)}
                                </span>
                            ) : (
                                formatCredits(balance)
                            )}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        {isBillingV2 && (
                            <p className="text-sm text-muted-foreground">
                                Available in your wallet
                                {currency !== "USD" && " · shown in your billing currency"}
                            </p>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="pb-2">
                        <CardDescription>{spentLabel}</CardDescription>
                        <CardTitle className="text-3xl">
                            {isBillingV2 ? formatCurrency(totalSpent, currency) : formatCredits(totalSpent)}
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <p className="text-sm text-muted-foreground">
                            {isBillingV2 ? "Usage charged to date" : "Current allocation usage"}
                        </p>
                    </CardContent>
                </Card>
            </div>

            {/* Ledger (v2) or quota bar (legacy) */}
            {isBillingV2 ? (
                <Card>
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Receipt className="h-5 w-5 text-muted-foreground" />
                            Transaction history
                        </CardTitle>
                        <CardDescription>
                            Payments received and usage charged, newest first.
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {ledgerEntries.length > 0 ? (
                            <div className="bg-card border rounded-lg overflow-x-auto shadow-sm">
                                <Table>
                                    <TableHeader>
                                        <TableRow className="bg-muted/50">
                                            <TableHead>Date</TableHead>
                                            <TableHead>Activity</TableHead>
                                            <TableHead>Details</TableHead>
                                            <TableHead>Run</TableHead>
                                            <TableHead className="text-right">Amount</TableHead>
                                            <TableHead className="text-right">Balance</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {ledgerEntries.map((entry) => (
                                            <LedgerRow key={entry.id} entry={entry} currency={currency} />
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        ) : (
                            <div className="rounded-lg border border-dashed p-10 text-center">
                                <Receipt className="mx-auto h-8 w-8 text-muted-foreground/40" />
                                <p className="mt-3 font-medium">No transactions yet</p>
                                <p className="mt-1 text-sm text-muted-foreground">
                                    Payments and call usage will appear here.
                                </p>
                            </div>
                        )}

                        {/* No column total by design: rows round independently (design.md §3). */}
                        {ledgerTotalPages > 1 && (
                            <div className="flex items-center justify-between mt-6">
                                <p className="text-sm text-muted-foreground">
                                    Page {ledgerPage} of {ledgerTotalPages} ({ledgerTotalCount} total)
                                </p>
                                <div className="flex gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => handlePageChange(ledgerPage - 1)}
                                        disabled={ledgerPage <= 1 || loading || refreshing}
                                    >
                                        <ChevronLeft className="h-4 w-4" />
                                        Previous
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => handlePageChange(ledgerPage + 1)}
                                        disabled={ledgerPage >= ledgerTotalPages || loading || refreshing}
                                    >
                                        Next
                                        <ChevronRight className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            ) : (
                <Card>
                    <CardHeader>
                        <CardTitle>Credit Usage</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <Progress value={usagePercent} />
                        <div className="flex justify-between text-sm text-muted-foreground">
                            <span>{usagePercent}% used</span>
                            <span>
                                {formatCredits(balance)} of {formatCredits(totalQuota)} remaining
                            </span>
                        </div>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}

function NoticeBanner({
    tone,
    icon,
    title,
    children,
}: {
    tone: "amber" | "rose" | "sky";
    icon: React.ReactNode;
    title: string;
    children: React.ReactNode;
}) {
    const tones = {
        amber: "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-200",
        rose: "border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-200",
        sky: "border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-200",
    }[tone];

    return (
        <div className={cn("flex gap-3 rounded-lg border p-4", tones)}>
            {icon}
            <div className="text-sm">
                <p className="font-medium">{title}</p>
                <p className="mt-1 opacity-90">{children}</p>
            </div>
        </div>
    );
}

function LedgerRow({
    entry,
    currency,
}: {
    entry: MpsCreditLedgerEntryResponse;
    currency: string;
}) {
    const kind = entryKind(entry);
    const delta = entry.credits_delta ?? 0;
    const isCredit = delta >= 0;
    const href = runHref(entry);
    const tokens = formatTokens(entry.tokens_charged);

    const icon =
        kind === "adjustment" ? (
            <Scale className="h-3.5 w-3.5" />
        ) : isCredit ? (
            <ArrowDownLeft className="h-3.5 w-3.5" />
        ) : (
            <ArrowUpRight className="h-3.5 w-3.5" />
        );

    const iconTone = isCredit
        ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
        : "bg-rose-500/10 text-rose-600 dark:text-rose-400";

    return (
        <TableRow>
            <TableCell className="whitespace-nowrap text-muted-foreground">
                {formatDateTime(entry.created_at)}
            </TableCell>
            <TableCell>
                <span className="flex items-center gap-2 font-medium">
                    <span className={cn("flex h-6 w-6 items-center justify-center rounded-md", iconTone)}>
                        {icon}
                    </span>
                    {entryLabel(entry)}
                </span>
            </TableCell>
            <TableCell className="text-muted-foreground">
                {kind === "grant" && entry.amount_minor != null ? (
                    // Grant: tendered amount verbatim, in its original currency (R8.3).
                    <span>Paid {formatMinor(entry.amount_minor, entry.amount_currency)}</span>
                ) : kind === "debit" && tokens ? (
                    // Debit: the token count it was priced from (R8.4).
                    <span>{tokens}</span>
                ) : entry.memo ? (
                    <span>{entry.memo}</span>
                ) : (
                    <span className="text-muted-foreground/50">—</span>
                )}
            </TableCell>
            <TableCell>
                {entry.workflow_run_id ? (
                    href ? (
                        <Link className="font-medium text-primary hover:underline" href={href}>
                            #{entry.workflow_run_id}
                            <ExternalLink className="ml-1 inline h-3 w-3" />
                        </Link>
                    ) : (
                        <span className="text-muted-foreground">#{entry.workflow_run_id}</span>
                    )
                ) : (
                    <span className="text-muted-foreground/50">—</span>
                )}
            </TableCell>
            <TableCell
                className={cn(
                    "text-right font-medium whitespace-nowrap tabular-nums",
                    isCredit ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400",
                )}
            >
                {formatCurrencyPrecise(delta, currency, { signed: true })}
            </TableCell>
            <TableCell className="text-right whitespace-nowrap tabular-nums text-muted-foreground">
                {formatCurrencyPrecise(entry.balance_after, currency)}
            </TableCell>
        </TableRow>
    );
}
