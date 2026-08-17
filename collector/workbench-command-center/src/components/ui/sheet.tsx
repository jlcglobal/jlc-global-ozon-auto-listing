import * as SheetPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

const Sheet = SheetPrimitive.Root;
const SheetTrigger = SheetPrimitive.Trigger;
const SheetClose = SheetPrimitive.Close;
const SheetPortal = SheetPrimitive.Portal;

const SheetOverlay = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof SheetPrimitive.Overlay>) => (
  <SheetPrimitive.Overlay className={cn("fixed inset-0 z-50 bg-black/55 backdrop-blur-xl", className)} {...props} />
);

const SheetContent = ({ className, children, ...props }: React.ComponentPropsWithoutRef<typeof SheetPrimitive.Content>) => (
  <SheetPortal>
    <SheetOverlay />
    <SheetPrimitive.Content
      className={cn(
        "fixed right-4 top-4 z-50 h-[calc(100vh-32px)] w-[min(720px,calc(100vw-32px))] overflow-hidden rounded-3xl border border-white/10 bg-[#071A16]/92 text-emerald-50 shadow-[0_0_70px_rgba(34,197,94,0.18)] backdrop-blur-2xl",
        className,
      )}
      {...props}
    >
      {children}
      <SheetPrimitive.Close className="absolute right-5 top-5 rounded-md p-1 text-emerald-100/50 hover:bg-white/10 hover:text-emerald-100">
        <X className="h-4 w-4" />
      </SheetPrimitive.Close>
    </SheetPrimitive.Content>
  </SheetPortal>
);

const SheetHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => <div className={cn("space-y-2", className)} {...props} />;
const SheetTitle = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof SheetPrimitive.Title>) => (
  <SheetPrimitive.Title className={cn("text-xl font-semibold tracking-tight", className)} {...props} />
);
const SheetDescription = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof SheetPrimitive.Description>) => (
  <SheetPrimitive.Description className={cn("text-sm text-emerald-100/52", className)} {...props} />
);

export { Sheet, SheetTrigger, SheetClose, SheetContent, SheetHeader, SheetTitle, SheetDescription };
