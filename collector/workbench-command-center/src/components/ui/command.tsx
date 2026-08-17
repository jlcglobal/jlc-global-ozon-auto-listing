import { Command as CommandPrimitive } from "cmdk";
import { cn } from "@/lib/utils";

const Command = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof CommandPrimitive>) => (
  <CommandPrimitive className={cn("flex h-full w-full flex-col overflow-hidden rounded-xl bg-[#071A16] text-emerald-50", className)} {...props} />
);
const CommandInput = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof CommandPrimitive.Input>) => (
  <div className="flex items-center border-b border-white/10 px-3">
    <CommandPrimitive.Input className={cn("flex h-11 w-full bg-transparent py-3 text-sm outline-none placeholder:text-emerald-100/35", className)} {...props} />
  </div>
);
const CommandList = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof CommandPrimitive.List>) => (
  <CommandPrimitive.List className={cn("max-h-[320px] overflow-y-auto overflow-x-hidden", className)} {...props} />
);
const CommandEmpty = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof CommandPrimitive.Empty>) => (
  <CommandPrimitive.Empty className={cn("py-6 text-center text-sm text-emerald-100/45", className)} {...props} />
);
const CommandGroup = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof CommandPrimitive.Group>) => (
  <CommandPrimitive.Group className={cn("overflow-hidden p-1 text-emerald-100/80", className)} {...props} />
);
const CommandItem = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof CommandPrimitive.Item>) => (
  <CommandPrimitive.Item className={cn("relative flex cursor-default select-none items-center gap-2 rounded-lg px-2 py-2 text-sm outline-none aria-selected:bg-emerald-400/10 aria-selected:text-emerald-100", className)} {...props} />
);

export { Command, CommandInput, CommandList, CommandEmpty, CommandGroup, CommandItem };
