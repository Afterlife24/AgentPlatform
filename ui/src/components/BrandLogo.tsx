import { cn } from "@/lib/utils";

// Reusable Afterlife wordmark as text — no image dependency.
export function BrandLogo({
  className,
  inverse = false,
  mark = false,
}: {
  className?: string;
  inverse?: boolean;
  mark?: boolean;
}) {
  if (mark) {
    return (
      <span className={cn("font-bold select-none text-foreground", className, inverse && "text-white")}>
        A
      </span>
    );
  }
  return (
    <span className={cn("font-bold tracking-tight select-none text-foreground", className, inverse && "text-white")}>
      afterlife
    </span>
  );
}
