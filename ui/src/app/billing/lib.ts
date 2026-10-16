import type { MpsCreditLedgerEntryResponse } from "@/client/types.gen";

/**
 * Presentation helpers for the customer billing page.
 *
 * Every money figure arriving from the backend is ALREADY in the account's
 * display currency (the backend does all FX with the exact Decimal balance).
 * The frontend only formats — it never converts. Nothing here says "credits".
 */

/**
 * Format a currency amount with the account's currency symbol (₹, $, …).
 *
 * By default shows exactly 2 decimals — right for summary figures like the
 * balance and total-spent cards. Pass `maxFractionDigits` higher for the
 * transaction table, where sub-cent charges (e.g. a 480-token knowledge-base
 * debit of $0.000012) must stay visible instead of rounding to $0.00.
 */
export function formatCurrency(
    amount: number | null | undefined,
    currency: string | null | undefined,
    { signed = false, maxFractionDigits = 2 }: { signed?: boolean; maxFractionDigits?: number } = {},
): string {
    const value = amount ?? 0;
    const formatted = new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: currency || "USD",
        minimumFractionDigits: 2,
        maximumFractionDigits: Math.max(2, maxFractionDigits),
    }).format(Math.abs(value));

    if (value < 0) return `−${formatted}`;
    if (signed && value > 0) return `+${formatted}`;
    return formatted;
}

/**
 * Full-precision currency for the transaction table: 2 decimals minimum, up to
 * 6 maximum. Clean amounts stay clean ($1.00), tiny charges show in full
 * ($0.000012) — matching the operator console's AgentX ledger.
 */
export function formatCurrencyPrecise(
    amount: number | null | undefined,
    currency: string | null | undefined,
    { signed = false }: { signed?: boolean } = {},
): string {
    return formatCurrency(amount, currency, { signed, maxFractionDigits: 6 });
}

/** Format an integer minor-unit amount (paise/cents) in its own currency. */
export function formatMinor(
    amountMinor: number | null | undefined,
    currency: string | null | undefined,
): string {
    if (amountMinor == null) return "—";
    return formatCurrency(amountMinor / 100, currency);
}

const integerFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

/** "102,305 tokens" — the single total the wallet prices a debit from. */
export function formatTokens(tokens: number | null | undefined): string | null {
    if (tokens == null) return null;
    return `${integerFormat.format(tokens)} tokens`;
}

export function formatDateTime(value: string): string {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export type EntryKind = "grant" | "debit" | "adjustment" | "other";

export function entryKind(entry: MpsCreditLedgerEntryResponse): EntryKind {
    switch (entry.entry_type) {
        case "grant":
            return "grant";
        case "debit":
            return "debit";
        case "adjustment":
            return "adjustment";
        default:
            return "other";
    }
}

/** Human label for the Activity column, taking KB vs call into account. */
export function entryLabel(entry: MpsCreditLedgerEntryResponse): string {
    switch (entryKind(entry)) {
        case "grant":
            return "Payment received";
        case "debit":
            return entry.origin === "knowledge_base" ? "Knowledge base" : "Usage";
        case "adjustment":
            return "Adjustment";
        default:
            return (entry.entry_type || "Entry").replace(/_/g, " ");
    }
}

/** Build the run link for a debit row, when the run is known. */
export function runHref(entry: MpsCreditLedgerEntryResponse): string | null {
    if (!entry.workflow_id || !entry.workflow_run_id) return null;
    return `/workflow/${entry.workflow_id}/run/${entry.workflow_run_id}`;
}

export function getPageFromSearchParams(searchParams: {
    get: (name: string) => string | null;
}): number {
    const raw = searchParams.get("page");
    const page = raw ? Number.parseInt(raw, 10) : 1;
    return Number.isFinite(page) && page > 0 ? page : 1;
}
