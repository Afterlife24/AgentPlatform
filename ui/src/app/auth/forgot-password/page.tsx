"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { detailFromError } from "@/lib/apiError";
import { AuthEnterpriseCTA } from "@/components/auth/AuthEnterpriseCTA";
import { AuthShell } from "@/components/auth/AuthShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Step = "email" | "otp" | "password" | "done";

export default function ForgotPasswordPage() {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSendOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(detailFromError(data, "Something went wrong"));
        return;
      }
      toast.success("If that email is registered, a reset code has been sent");
      setStep("otp");
    } catch {
      toast.error("An error occurred. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (otp.length !== 6) {
      toast.error("Enter the 6-digit code from your email");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/forgot-password/verify-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, otp }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(detailFromError(data, "Invalid or expired code"));
        return;
      }
      setStep("password");
    } catch {
      toast.error("An error occurred. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error("Passwords do not match");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/forgot-password/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, otp, new_password: newPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(detailFromError(data, "Failed to reset password"));
        return;
      }
      setStep("done");
    } catch {
      toast.error("An error occurred. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell enterpriseSlot={<AuthEnterpriseCTA />}>
      {step === "email" && (
        <>
          <div className="space-y-1.5 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Forgot password?</h1>
            <p className="text-sm text-muted-foreground">
              Enter your email and we&apos;ll send you a reset code
            </p>
          </div>
          <form onSubmit={handleSendOtp} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Sending code..." : "Send reset code"}
            </Button>
          </form>
          <p className="text-center text-sm text-muted-foreground">
            Remember your password?{" "}
            <Link href="/auth/login" className="text-primary underline-offset-4 hover:underline">
              Sign in
            </Link>
          </p>
        </>
      )}

      {step === "otp" && (
        <>
          <div className="space-y-1.5 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Enter reset code</h1>
            <p className="text-sm text-muted-foreground">
              We sent a 6-digit code to{" "}
              <span className="font-medium text-foreground">{email}</span>
            </p>
          </div>
          <form onSubmit={handleVerifyOtp} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="otp">Reset code</Label>
              <Input
                id="otp"
                type="text"
                inputMode="numeric"
                maxLength={6}
                placeholder="000000"
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
                required
                className="text-center text-2xl tracking-widest"
              />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Verifying..." : "Verify code"}
            </Button>
          </form>
          <p className="text-center text-sm text-muted-foreground">
            Wrong email?{" "}
            <button
              type="button"
              onClick={() => setStep("email")}
              className="text-primary underline-offset-4 hover:underline"
            >
              Go back
            </button>
          </p>
        </>
      )}

      {step === "password" && (
        <>
          <div className="space-y-1.5 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Set new password</h1>
            <p className="text-sm text-muted-foreground">Choose a strong password</p>
          </div>
          <form onSubmit={handleResetPassword} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="newPassword">New password</Label>
              <Input
                id="newPassword"
                type="password"
                placeholder="At least 8 characters"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirmPassword">Confirm new password</Label>
              <Input
                id="confirmPassword"
                type="password"
                placeholder="Confirm your new password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
              />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Saving..." : "Save new password"}
            </Button>
          </form>
        </>
      )}

      {step === "done" && (
        <>
          <div className="space-y-1.5 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Password updated</h1>
            <p className="text-sm text-muted-foreground">
              Your password has been reset successfully.
            </p>
          </div>
          <Link href="/auth/login">
            <Button className="w-full">Sign in</Button>
          </Link>
        </>
      )}
    </AuthShell>
  );
}
