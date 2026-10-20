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

type Step = "details" | "otp";

export default function SignupPage() {
  const [step, setStep] = useState<Step>("details");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSendOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < 8) {
      toast.error("Password must be at least 8 characters");
      return;
    }
    if (password !== confirmPassword) {
      toast.error("Passwords do not match");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/signup/send-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, name: name.trim() || null }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(detailFromError(data, "Failed to send verification code"));
        return;
      }
      toast.success("Verification code sent to your email");
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
      const res = await fetch("/api/v1/auth/signup/verify-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, otp, name: name.trim() || null }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(detailFromError(data, "Verification failed"));
        return;
      }
      await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: data.token, user: data.user }),
      });
      window.location.href = "/after-sign-in";
    } catch {
      toast.error("An error occurred. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell enterpriseSlot={<AuthEnterpriseCTA />}>
      {step === "details" && (
        <>
          <div className="space-y-1.5 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Create an account</h1>
            <p className="text-sm text-muted-foreground">Enter your details to get started</p>
          </div>
          <form onSubmit={handleSendOtp} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                type="text"
                placeholder="Your name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
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
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirmPassword">Confirm password</Label>
              <Input
                id="confirmPassword"
                type="password"
                placeholder="Confirm your password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
              />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "Sending code..." : "Continue"}
            </Button>
          </form>
          <p className="text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/auth/login" className="text-primary underline-offset-4 hover:underline">
              Sign in
            </Link>
          </p>
        </>
      )}

      {step === "otp" && (
        <>
          <div className="space-y-1.5 text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Verify your email</h1>
            <p className="text-sm text-muted-foreground">
              We sent a 6-digit code to{" "}
              <span className="font-medium text-foreground">{email}</span>
            </p>
          </div>
          <form onSubmit={handleVerifyOtp} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="otp">Verification code</Label>
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
              {loading ? "Verifying..." : "Create account"}
            </Button>
          </form>
          <p className="text-center text-sm text-muted-foreground">
            Wrong email?{" "}
            <button
              type="button"
              onClick={() => setStep("details")}
              className="text-primary underline-offset-4 hover:underline"
            >
              Go back
            </button>
          </p>
        </>
      )}
    </AuthShell>
  );
}
