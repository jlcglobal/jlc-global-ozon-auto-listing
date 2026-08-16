import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

const Tabs = TabsPrimitive.Root;

const TabsList = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>) => (
  <TabsPrimitive.List className={cn("inline-flex h-10 items-center justify-center rounded-xl border border-gray-200 bg-gray-50 p-1", className)} {...props} />
);

const TabsTrigger = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>) => (
  <TabsPrimitive.Trigger
    className={cn("inline-flex items-center justify-center rounded-lg px-3 py-1.5 text-xs font-medium text-slate-8000 transition-all data-[state=active]:bg-emerald-400/14 data-[state=active]:text-emerald-100", className)}
    {...props}
  />
);

const TabsContent = ({ className, ...props }: React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>) => (
  <TabsPrimitive.Content className={cn("mt-2", className)} {...props} />
);

export { Tabs, TabsList, TabsTrigger, TabsContent };
