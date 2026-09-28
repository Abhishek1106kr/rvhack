import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function Panel({
  title,
  meta,
  className,
  children,
}: {
  title: string;
  meta?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={cn("flex min-h-0 flex-col rounded-md border bg-card", className)}>
      <header className="flex items-baseline justify-between gap-2 border-b px-3 py-1.5">
        <h2 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
          {title}
        </h2>
        {meta && <div className="text-xs text-muted-foreground">{meta}</div>}
      </header>
      <div className="min-h-0 flex-1 overflow-auto p-3">{children}</div>
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>;
}
