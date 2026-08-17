import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTime(value?: string) {
  if (!value || value === "unknown") return "--:--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export function truncate(value: string | undefined, length = 64) {
  if (!value) return "未命名商品";
  return value.length > length ? `${value.slice(0, length)}...` : value;
}
