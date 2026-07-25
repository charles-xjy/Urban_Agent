"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Brain } from "lucide-react";
import { cn } from "@/lib/utils";
import { MarkdownText } from "../markdown-text";

interface ThinkingBlockProps {
  content: string;
}

export function ThinkingBlock({ content }: ThinkingBlockProps) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="my-1 overflow-hidden rounded-lg border border-border/40 bg-muted/30">
      <button
        onClick={() => setIsOpen((v) => !v)}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2",
          "text-sm text-muted-foreground transition-colors",
          "hover:bg-muted/50",
        )}
      >
        <Brain className="h-3.5 w-3.5 shrink-0" />
        <span className="font-medium">思考过程</span>
        {isOpen ? (
          <ChevronDown className="ml-auto h-3.5 w-3.5" />
        ) : (
          <ChevronRight className="ml-auto h-3.5 w-3.5" />
        )}
      </button>

      {isOpen && (
        <div className="border-t border-border/30 bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
          <MarkdownText>{content}</MarkdownText>
        </div>
      )}
    </div>
  );
}
