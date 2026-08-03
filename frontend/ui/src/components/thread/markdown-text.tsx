"use client";

import "./markdown-styles.css";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeKatex from "rehype-katex";
import remarkMath from "remark-math";
import { FC, memo, useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import { SyntaxHighlighter } from "@/components/thread/syntax-highlighter";

import { TooltipIconButton } from "@/components/thread/tooltip-icon-button";
import { cn } from "@/lib/utils";

import "katex/dist/katex.min.css";

interface CodeHeaderProps {
  language?: string;
  code: string;
}

const useCopyToClipboard = ({
  copiedDuration = 3000,
}: {
  copiedDuration?: number;
} = {}) => {
  const [isCopied, setIsCopied] = useState<boolean>(false);

  const copyToClipboard = (value: string) => {
    if (!value) return;

    // navigator.clipboard 仅在 secure context（HTTPS / localhost）下存在，
    // 局域网走明文 HTTP 访问时为 undefined，回退到 execCommand 兜底。
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(value).then(() => {
        setIsCopied(true);
        setTimeout(() => setIsCopied(false), copiedDuration);
      });
    } else {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      setIsCopied(true);
      setTimeout(() => setIsCopied(false), copiedDuration);
    }
  };

  return { isCopied, copyToClipboard };
};

const CodeHeader: FC<CodeHeaderProps> = ({ language, code }) => {
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const onCopy = () => {
    if (!code || isCopied) return;
    copyToClipboard(code);
  };

  return (
    <div className="flex items-center justify-between gap-4 rounded-t-lg bg-zinc-900 px-4 py-2 text-sm font-semibold text-white">
      <span className="lowercase [&>span]:text-xs">{language}</span>
      <TooltipIconButton
        tooltip="Copy"
        onClick={onCopy}
      >
        {!isCopied && <CopyIcon />}
        {isCopied && <CheckIcon />}
      </TooltipIconButton>
    </div>
  );
};

const defaultComponents: any = {
  h1: ({ className, ...props }: { className?: string }) => (
    <h1
      className={cn(
        "mb-8 scroll-m-20 text-4xl font-extrabold tracking-tight last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h2: ({ className, ...props }: { className?: string }) => (
    <h2
      className={cn(
        "mt-8 mb-4 scroll-m-20 text-3xl font-semibold tracking-tight first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h3: ({ className, ...props }: { className?: string }) => (
    <h3
      className={cn(
        "mt-6 mb-4 scroll-m-20 text-2xl font-semibold tracking-tight first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h4: ({ className, ...props }: { className?: string }) => (
    <h4
      className={cn(
        "mt-6 mb-4 scroll-m-20 text-xl font-semibold tracking-tight first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h5: ({ className, ...props }: { className?: string }) => (
    <h5
      className={cn(
        "my-4 text-lg font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h6: ({ className, ...props }: { className?: string }) => (
    <h6
      className={cn("my-4 font-semibold first:mt-0 last:mb-0", className)}
      {...props}
    />
  ),
  p: ({ className, ...props }: { className?: string }) => (
    <p
      className={cn("mt-5 mb-5 leading-7 first:mt-0 last:mb-0", className)}
      {...props}
    />
  ),
  a: ({ className, ...props }: { className?: string }) => (
    <a
      className={cn(
        "text-primary font-medium underline underline-offset-4",
        className,
      )}
      {...props}
    />
  ),
  blockquote: ({ className, ...props }: { className?: string }) => (
    <blockquote
      className={cn("border-l-2 pl-6 italic", className)}
      {...props}
    />
  ),
  ul: ({ className, ...props }: { className?: string }) => (
    <ul
      className={cn("my-5 ml-6 list-disc [&>li]:mt-2", className)}
      {...props}
    />
  ),
  ol: ({ className, ...props }: { className?: string }) => (
    <ol
      className={cn("my-5 ml-6 list-decimal [&>li]:mt-2", className)}
      {...props}
    />
  ),
  hr: ({ className, ...props }: { className?: string }) => (
    <hr
      className={cn("my-5 border-b", className)}
      {...props}
    />
  ),
  table: ({ className, ...props }: { className?: string }) => (
    <table
      className={cn(
        "my-5 w-full border-separate border-spacing-0 overflow-y-auto",
        className,
      )}
      {...props}
    />
  ),
  th: ({ className, ...props }: { className?: string }) => (
    <th
      className={cn(
        "bg-muted px-4 py-2 text-left font-bold first:rounded-tl-lg last:rounded-tr-lg [&[align=center]]:text-center [&[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }: { className?: string }) => (
    <td
      className={cn(
        "border-b border-l px-4 py-2 text-left last:border-r [&[align=center]]:text-center [&[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  tr: ({ className, ...props }: { className?: string }) => (
    <tr
      className={cn(
        "m-0 border-b p-0 first:border-t [&:last-child>td:first-child]:rounded-bl-lg [&:last-child>td:last-child]:rounded-br-lg",
        className,
      )}
      {...props}
    />
  ),
  sup: ({ className, ...props }: { className?: string }) => (
    <sup
      className={cn("[&>a]:text-xs [&>a]:no-underline", className)}
      {...props}
    />
  ),
  pre: ({ className, ...props }: { className?: string }) => (
    <pre
      className={cn(
        "max-w-4xl overflow-x-auto rounded-lg bg-black text-white",
        className,
      )}
      {...props}
    />
  ),
  code: ({
    className,
    children,
    ...props
  }: {
    className?: string;
    children: React.ReactNode;
  }) => {
    const match = /language-(\w+)/.exec(className || "");

    if (match) {
      const language = match[1];
      const code = String(children).replace(/\n$/, "");

      return (
        <>
          <CodeHeader
            language={language}
            code={code}
          />
          <SyntaxHighlighter
            language={language}
            className={className}
          >
            {code}
          </SyntaxHighlighter>
        </>
      );
    }

    return (
      <code
        className={cn("rounded font-semibold", className)}
        {...props}
      >
        {children}
      </code>
    );
  },
};

type SourceDisplayEntry = {
  number: string;
  title: string;
  url: string;
};

function getSourceHref(source: SourceDisplayEntry): {
  href: string;
  isSearchFallback: boolean;
} {
  if (source.url) {
    return { href: source.url, isSearchFallback: false };
  }

  // 兼容旧报告中只写了裸域名、没有 http(s):// 的来源。
  const domainMatch = source.title.match(
    /(?:^|[\s（(])((?:[\w-]+\.)+[a-z]{2,}(?:\/[^\s）)]*)?)/i,
  );
  if (domainMatch?.[1]) {
    return {
      href: `https://${domainMatch[1].replace(/[.,;，。；]+$/, "")}`,
      isSearchFallback: false,
    };
  }

  // 历史消息无法重新取得当时的原始 URL 时，至少让来源不再是死文本。
  // 新报告会由服务端从原始检索结果回填直链，通常不会走到这里。
  return {
    href: `https://www.bing.com/search?q=${encodeURIComponent(source.title)}`,
    isSearchFallback: true,
  };
}

/**
 * 把来源区段从 Markdown 正文中拆出，交给专用行组件渲染。
 * 这样不会生成 ul/ol 的 marker，序号、标题和链接也能保持在同一行。
 */
function splitSourceList(markdown: string): {
  report: string;
  sources: SourceDisplayEntry[];
} {
  const sourceHeading =
    /(^|\n)[ \t]*(?:(?:#{1,6}[ \t]+)?来源[ \t]*[:：]?|【[ \t]*来源[ \t]*】)[ \t]*(?:\n|(?=(?:[-*][ \t]*)?\[\d+\]))/gm;
  let lastMatch: RegExpExecArray | null = null;
  let match: RegExpExecArray | null;

  while ((match = sourceHeading.exec(markdown)) !== null) {
    lastMatch = match;
  }

  if (!lastMatch) return { report: markdown, sources: [] };

  const headingStart = lastMatch.index + (lastMatch[1] ? 1 : 0);
  const sourceStart = lastMatch.index + lastMatch[0].length;
  const report = markdown.slice(0, headingStart).trimEnd();
  const sourceText = markdown.slice(sourceStart).trim();
  if (!sourceText) return { report: markdown, sources: [] };

  const entries = sourceText
    .split(/(?=\[\d+\][ \t]+)/)
    .map((entry) =>
      entry
        .trim()
        .replace(/^[-*][ \t]*/, "")
        .replace(/\s*[-*]\s*$/, ""),
    )
    .filter((entry) => /^\[\d+\]/.test(entry));

  const sources = entries.map((entry, index) => {
    const numbered = entry.match(/^\[(\d+)\]\s*(.*)$/);
    const number = numbered?.[1] ?? String(index + 1);
    const body = (numbered?.[2] ?? entry).trim();
    const urlMatch = body.match(/https?:\/\/\S+/);
    const url = urlMatch?.[0].replace(/[.,;，。；)\]]+$/, "") ?? "";
    const title = (url ? body.replace(urlMatch?.[0] ?? "", "") : body)
      .replace(/\s*[-–—·]\s*$/, "")
      .trim();

    return { number, title, url };
  });

  return { report, sources };
}

const MarkdownTextImpl: FC<{ children: string }> = ({ children }) => {
  const { report, sources } = splitSourceList(children);

  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={defaultComponents}
      >
        {report}
      </ReactMarkdown>
      {sources.length > 0 && (
        <section className="mt-8">
          <h2 className="mb-4 scroll-m-20 text-3xl font-semibold tracking-tight">
            来源
          </h2>
          <div className="divide-border/50 border-border/50 bg-muted/10 divide-y rounded-lg border px-4">
            {sources.map((source, index) =>
              (() => {
                const { href, isSearchFallback } = getSourceHref(source);
                return (
                  <div
                    key={`${source.number}-${source.url}-${index}`}
                    className="flex items-start gap-2 py-3 text-sm leading-6"
                  >
                    <span className="text-foreground/50 shrink-0 font-medium">
                      [{source.number}]
                    </span>
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={
                        isSearchFallback
                          ? "搜索该来源（历史报告未保存原始链接）"
                          : "打开来源"
                      }
                      className="text-primary min-w-0 flex-1 font-medium break-words underline underline-offset-4"
                    >
                      {source.title || source.url}
                    </a>
                  </div>
                );
              })(),
            )}
          </div>
        </section>
      )}
    </div>
  );
};

export const MarkdownText = memo(MarkdownTextImpl);
