"use client";

import { CreditCard, ExternalLink, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { getBillingCreditsApiV1OrganizationsBillingCreditsGet } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useAppConfig } from "@/context/AppConfigContext";
import { useAuth } from "@/lib/auth";

import DocumentList from "./DocumentList";
import DocumentUpload from "./DocumentUpload";

export default function FilesPage() {
    const { user, redirectToLogin, loading } = useAuth();
    const { config } = useAppConfig();
    const router = useRouter();
    const isOSS = config?.deploymentMode === "oss";
    const [refreshKey, setRefreshKey] = useState(0);
    const [isUploadOpen, setIsUploadOpen] = useState(false);

    // Prepaid-wallet gate (OSS only). When the balance is empty/blocked, the
    // backend refuses document processing with a 402 — but that only surfaces
    // after the user has already picked a file. We fetch the balance up front
    // so clicking "Upload Document" can show a clear block dialog immediately,
    // instead of a late, vague error.
    const [isBlocked, setIsBlocked] = useState(false);
    const [balanceDialogOpen, setBalanceDialogOpen] = useState(false);
    const hasFetchedBalance = useRef(false);

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    const fetchBalance = useCallback(async () => {
        // Enforcement is OSS-only; skip the probe entirely otherwise.
        if (!isOSS) return;
        try {
            const response = await getBillingCreditsApiV1OrganizationsBillingCreditsGet();
            const credits = response.data;
            if (!credits) return;
            // Mirror the backend rule: blocked account OR a non-positive
            // balance (remaining_credits is already in display currency).
            const balance = credits.remaining_credits ?? 0;
            setIsBlocked(Boolean(credits.is_blocked) || balance <= 0);
        } catch {
            // On a probe failure, don't block — the backend 402 is still the
            // authoritative safety net on the actual upload.
            setIsBlocked(false);
        }
    }, [isOSS]);

    useEffect(() => {
        if (loading || !user || hasFetchedBalance.current) return;
        hasFetchedBalance.current = true;
        fetchBalance();
    }, [loading, user, fetchBalance]);

    const handleUploadClick = () => {
        if (isBlocked) {
            setBalanceDialogOpen(true);
            return;
        }
        setIsUploadOpen(true);
    };

    const handleUploadSuccess = () => {
        setRefreshKey(prev => prev + 1);
        setIsUploadOpen(false);
        // A successful upload just spent credits; refresh the gate so the next
        // click reflects the new balance.
        hasFetchedBalance.current = false;
        fetchBalance();
    };

    if (loading || !user) {
        return (
            <div className="container mx-auto px-4 py-8">
                <div className="space-y-4">
                    <Skeleton className="h-12 w-64" />
                    <Skeleton className="h-64 w-full" />
                </div>
            </div>
        );
    }

    return (
        <div className="container mx-auto px-4 py-8">
            <div className="mb-8">
                <h1 className="text-3xl font-bold mb-2">Knowledge Base Files</h1>
                <p className="text-muted-foreground">
                    Upload and manage documents for your voice agents to reference.{" "}
                    <a href="https://docs.afterlife.ai" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                        Learn more <ExternalLink className="h-3 w-3" />
                    </a>
                </p>
            </div>

            <Card>
                <CardHeader>
                    <div className="flex justify-between items-center">
                        <div>
                            <CardTitle>Your Documents</CardTitle>
                            <CardDescription>
                                Documents shared across all agents in your organization
                            </CardDescription>
                        </div>
                        <Button onClick={handleUploadClick}>
                            <Upload className="w-4 h-4 mr-2" />
                            Upload Document
                        </Button>
                    </div>
                </CardHeader>
                <CardContent>
                    <DocumentList refreshTrigger={refreshKey} />
                </CardContent>
            </Card>

            <Dialog open={isUploadOpen} onOpenChange={setIsUploadOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Upload Document</DialogTitle>
                        <DialogDescription>
                            Upload a PDF or document file to add to your knowledge base
                        </DialogDescription>
                    </DialogHeader>
                    <DocumentUpload onUploadSuccess={handleUploadSuccess} />
                </DialogContent>
            </Dialog>

            {/* Balance block: shown instead of the upload dialog when the
                prepaid wallet is empty/blocked, so the user learns up front
                rather than after choosing a file. */}
            <Dialog open={balanceDialogOpen} onOpenChange={setBalanceDialogOpen}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <CreditCard className="h-5 w-5 text-orange-500" />
                            Insufficient Balance
                        </DialogTitle>
                        <DialogDescription className="pt-2">
                            Your account balance is empty. Please top up your balance
                            before uploading documents to your knowledge base.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setBalanceDialogOpen(false)}>
                            Cancel
                        </Button>
                        <Button onClick={() => router.push("/billing")}>
                            Go to Billing
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
