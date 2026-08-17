import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.14em]", {
  variants: {
    variant: {
      default: "border-emerald-400/20 bg-emerald-400/10 text-emerald-200",
      warning: "border-amber-400/20 bg-amber-400/10 text-amber-200",
      danger: "border-red-400/20 bg-red-400/10 text-red-200",
      muted: "border-white/10 bg-white/[0.04] text-emerald-100/55",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
