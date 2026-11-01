"use client";

import { useParams } from "next/navigation";
import { Suspense } from "react";
import { Spinner } from "@/components/ui";
import { Workspace } from "@/components/workspace/workspace";

export default function BotPage() {
  const { id } = useParams<{ id: string }>();
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center gap-2 text-muted">
          <Spinner /> Loading…
        </div>
      }
    >
      <Workspace key={id} botId={id} />
    </Suspense>
  );
}
