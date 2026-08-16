import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-medium", {
  variants: {
    variant: {
      default: "border-emerald-400/30 bg-emerald-50 text-emerald-700",
      warning: "border-amber-400/30 bg-amber-50 text-amber-700",
      danger: "border-red-400/30 bg-red-50 text-red-700",
      muted: "border-gray-200 bg-gray-50 text-slate-400",
    },
  },
  defaultVariants: { variant: "default" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
