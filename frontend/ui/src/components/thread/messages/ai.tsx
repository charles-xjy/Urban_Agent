import { parsePartialJson } from "@langchain/core/output_parsers";
import { useStreamContext } from "@/providers/Stream";
import { AIMessage, Checkpoint, Message } from "@langchain/langgraph-sdk";
import { useStream } from "@langchain/langgraph-sdk/react";
import { getContentString } from "../utils";
import { BranchSwitcher, CommandBar } from "./shared";
import { MarkdownText } from "../markdown-text";
import { LoadExternalComponent } from "@langchain/langgraph-sdk/react-ui";
import { cn } from "@/lib/utils";
import { ToolCalls, ToolResult } from "./tool-calls";
import { MessageContentComplex } from "@langchain/core/messages";
import { Fragment } from "react/jsx-runtime";
import { isAgentInboxInterruptSchema } from "@/lib/agent-inbox-interrupt";
import { ThreadView } from "../agent-inbox";
import { useQueryState, parseAsBoolean } from "nuqs";
import { GenericInterruptView } from "./generic-interrupt";
import { useArtifact } from "../artifact";
import { useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  ListChecks,
  Search,
} from "lucide-react";
/** 去除思考过程，只返回 </think> 之后的正文 */
function stripThinkContent(text: string): string {
  const closeTag = "</think>";
  const idx = text.indexOf(closeTag);
  if (idx === -1) return text;
  return text.slice(idx + closeTag.length).trimStart();
}

/** 从卫星影像输出中提取 .jpg/.png 文件名 */
function extractImageFiles(text: string): string[] {
  const matches = text.match(/[\w一-鿿\-（）()]+_\d{4}\.(jpg|jpeg|png)/gi) ?? [];
  return [...new Set(matches)];
}

type AgentSearchResult = {
  title: string;
  url: string;
  snippet: string;
  source_label: string;
};

type AgentSearchGroup = {
  query: string;
  total: number;
  error: string;
  results: AgentSearchResult[];
};

type AgentCardPayload = {
  version: number;
  process: string[];
  searches: AgentSearchGroup[];
  tools: Array<{ tool: string; summary: string }>;
  findings: string;
};

function parseAgentCardPayload(content: string): AgentCardPayload {
  try {
    const parsed = JSON.parse(content) as Partial<AgentCardPayload>;
    if (parsed.version === 2) {
      return {
        version: 2,
        process: Array.isArray(parsed.process) ? parsed.process : [],
        searches: Array.isArray(parsed.searches) ? parsed.searches : [],
        tools: Array.isArray(parsed.tools) ? parsed.tools : [],
        findings: typeof parsed.findings === "string" ? parsed.findings : "",
      };
    }
  } catch {
    // 兼容旧线程的纯文本卡片。
  }

  const process = [
    ...content.matchAll(/•\s*(正在搜索：[^\n]+|正在分析卫星影像[.…]*|正在查询 POI 数据[^\n]*)/g),
  ].map((match) => match[1].trim());
  const markerIndexes = ["【研究结论", "根据已有证据"]
    .map((marker) => content.indexOf(marker))
    .filter((index) => index >= 0);
  const findingsStart =
    markerIndexes.length > 0 ? Math.min(...markerIndexes) : -1;

  return {
    version: 1,
    process: [...new Set(process)],
    searches: [],
    tools: [],
    findings:
      findingsStart >= 0
        ? content.slice(findingsStart).trim()
        : "旧版研究记录未结构化；请重新运行该任务以查看分区结果。",
  };
}

/** 可折叠的 Researcher 输出卡片 */
function AgentOutputCard({ topic, status, content }: { topic: string; status: string; content: string }) {
  const [open, setOpen] = useState(false);
  const isComplete = status === "执行结果";
  const payload = parseAgentCardPayload(content);
  const imageFiles = extractImageFiles(content);
  const sourceCount = payload.searches.reduce(
    (total, group) => total + group.results.length,
    0,
  );
  const searchTaskCount =
    payload.searches.length ||
    payload.process.filter((step) => step.startsWith("正在搜索：")).length;

  return (
    <div className="overflow-hidden rounded-xl border border-border/50 bg-background text-sm shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-muted/35"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-foreground/40" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-foreground/40" />
        )}
        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
        <span className="min-w-0 flex-1 truncate font-medium text-foreground/75">
          {topic}
        </span>
        <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700">
          {isComplete ? "已完成" : "运行中"}
        </span>
      </button>

      {open && (
        <div className="flex flex-col gap-5 border-t border-border/40 bg-muted/10 px-4 py-4">
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-lg border border-border/40 bg-background px-3 py-2">
              <p className="text-[11px] text-foreground/45">执行步骤</p>
              <p className="mt-0.5 text-base font-semibold text-foreground/75">
                {payload.process.length}
              </p>
            </div>
            <div className="rounded-lg border border-border/40 bg-background px-3 py-2">
              <p className="text-[11px] text-foreground/45">检索任务</p>
              <p className="mt-0.5 text-base font-semibold text-foreground/75">
                {searchTaskCount}
              </p>
            </div>
            <div className="rounded-lg border border-border/40 bg-background px-3 py-2">
              <p className="text-[11px] text-foreground/45">有效来源</p>
              <p className="mt-0.5 text-base font-semibold text-foreground/75">
                {payload.version === 1 ? "—" : sourceCount}
              </p>
            </div>
          </div>

          {payload.process.length > 0 && (
            <section>
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-foreground/55">
                <ListChecks className="h-4 w-4" />
                执行过程
              </div>
              <div className="ml-1 border-l border-border/60 pl-4">
                {payload.process.map((step, index) => (
                  <div
                    key={`${step}-${index}`}
                    className="relative pb-3 last:pb-0"
                  >
                    <span className="absolute -left-[19px] top-1.5 h-2 w-2 rounded-full border-2 border-background bg-emerald-500 ring-1 ring-border" />
                    <p className="whitespace-pre-wrap text-xs leading-5 text-foreground/65">
                      {step}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {payload.searches.length > 0 && (
            <section>
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-foreground/55">
                <Search className="h-4 w-4" />
                检索结果
              </div>
              <div className="flex flex-col gap-2">
                {payload.searches.map((group, index) => (
                  <details
                    key={`${group.query}-${index}`}
                    className="group rounded-lg border border-border/40 bg-background"
                  >
                    <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5">
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-foreground/35 transition-transform group-open:rotate-90" />
                      <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground/70">
                        {group.query || `检索任务 ${index + 1}`}
                      </span>
                      <span className="shrink-0 text-[11px] text-foreground/40">
                        {group.results.length} 条
                      </span>
                    </summary>
                    <div className="flex flex-col gap-2 border-t border-border/30 px-3 py-3">
                      {group.error && (
                        <p className="text-xs text-rose-600">{group.error}</p>
                      )}
                      {group.results.map((result, resultIndex) => (
                        <div
                          key={`${result.url}-${resultIndex}`}
                          className="rounded-md bg-muted/35 px-3 py-2"
                        >
                          <div className="flex items-start gap-2">
                            <span className="mt-0.5 shrink-0 text-[11px] font-medium text-foreground/35">
                              {resultIndex + 1}
                            </span>
                            <div className="min-w-0 flex-1">
                              {result.url ? (
                                <a
                                  href={result.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-flex items-start gap-1 text-xs font-medium text-sky-700 hover:underline"
                                >
                                  <span>{result.title || result.url}</span>
                                  <ExternalLink className="mt-0.5 h-3 w-3 shrink-0" />
                                </a>
                              ) : (
                                <p className="text-xs font-medium text-foreground/70">
                                  {result.title || "未命名结果"}
                                </p>
                              )}
                              {result.source_label && (
                                <span className="mt-1 inline-block rounded bg-background px-1.5 py-0.5 text-[10px] text-foreground/45">
                                  {result.source_label}
                                </span>
                              )}
                              {result.snippet && (
                                <p className="mt-1.5 line-clamp-3 text-[11px] leading-5 text-foreground/55">
                                  {result.snippet}
                                </p>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </details>
                ))}
              </div>
            </section>
          )}

          {payload.tools.length > 0 && (
            <section>
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-foreground/55">
                <FileText className="h-4 w-4" />
                工具结果
              </div>
              <div className="flex flex-col gap-2">
                {payload.tools.map((tool, index) => (
                  <details
                    key={`${tool.tool}-${index}`}
                    className="rounded-lg border border-border/40 bg-background"
                  >
                    <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-foreground/65">
                      {tool.tool}
                    </summary>
                    <pre className="overflow-x-auto whitespace-pre-wrap border-t border-border/30 px-3 py-2 text-[11px] leading-5 text-foreground/55">
                      {tool.summary}
                    </pre>
                  </details>
                ))}
              </div>
            </section>
          )}

          {imageFiles.length > 0 && (
            <section>
              <div className="mb-2 text-xs font-semibold text-foreground/55">
                影像与附件
              </div>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 md:grid-cols-6">
                {imageFiles.map((file) => (
                  <a
                    key={file}
                    href={`/api/local-image?file=${encodeURIComponent(file)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group relative"
                  >
                    <img
                      src={`/api/local-image?file=${encodeURIComponent(file)}`}
                      alt={file}
                      className="aspect-square w-full rounded-md object-cover transition-opacity group-hover:opacity-80"
                    />
                    <span className="absolute bottom-0 left-0 right-0 truncate rounded-b-md bg-black/55 px-1 py-0.5 text-center text-[10px] text-white">
                      {file.match(/_(\d{4})\./)?.[1] ?? file}
                    </span>
                  </a>
                ))}
              </div>
            </section>
          )}

          <section>
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-foreground/55">
              <FileText className="h-4 w-4" />
              研究结论
            </div>
            <div className="rounded-lg border border-border/40 bg-background px-4 py-3 text-foreground/70">
              <MarkdownText>{payload.findings}</MarkdownText>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

function CustomComponent({
  message,
  thread,
}: {
  message: Message;
  thread: ReturnType<typeof useStreamContext>;
}) {
  const artifact = useArtifact();
  const { values } = useStreamContext();
  const customComponents = values.ui?.filter(
    (ui) => ui.metadata?.message_id === message.id,
  );

  if (!customComponents?.length) return null;
  return (
    <Fragment key={message.id}>
      {customComponents.map((customComponent) => (
        <LoadExternalComponent
          key={customComponent.id}
          stream={thread as unknown as ReturnType<typeof useStream>}
          message={customComponent}
          meta={{ ui: customComponent, artifact }}
        />
      ))}
    </Fragment>
  );
}

function parseAnthropicStreamedToolCalls(
  content: MessageContentComplex[],
): AIMessage["tool_calls"] {
  const toolCallContents = content.filter((c) => c.type === "tool_use" && c.id);

  return toolCallContents.map((tc) => {
    const toolCall = tc as Record<string, any>;
    let json: Record<string, any> = {};
    if (toolCall?.input) {
      try {
        json = parsePartialJson(toolCall.input) ?? {};
      } catch {
        // Pass
      }
    }
    return {
      name: toolCall.name ?? "",
      id: toolCall.id ?? "",
      args: json,
      type: "tool_call",
    };
  });
}

interface InterruptProps {
  interrupt?: unknown;
  isLastMessage: boolean;
  hasNoAIOrToolMessages: boolean;
}

function Interrupt({
  interrupt,
  isLastMessage,
  hasNoAIOrToolMessages,
}: InterruptProps) {
  const fallbackValue = Array.isArray(interrupt)
    ? interrupt
    : ((interrupt as { value?: unknown } | undefined)?.value ?? interrupt);

  return (
    <>
      {isAgentInboxInterruptSchema(interrupt) &&
        (isLastMessage || hasNoAIOrToolMessages) && (
          <ThreadView interrupt={interrupt} />
        )}
      {interrupt &&
      !isAgentInboxInterruptSchema(interrupt) &&
      (isLastMessage || hasNoAIOrToolMessages) ? (
        <GenericInterruptView interrupt={fallbackValue} />
      ) : null}
    </>
  );
}

export function AssistantMessage({
  message,
  isLoading,
  handleRegenerate,
}: {
  message: Message | undefined;
  isLoading: boolean;
  handleRegenerate: (parentCheckpoint: Checkpoint | null | undefined) => void;
}) {
  const msgName = (message as Record<string, unknown> | undefined)?.name as string | undefined;
  const content = message?.content ?? [];
  const contentString = getContentString(content);
  const mainContent = stripThinkContent(contentString);

  // Researcher 输出：折叠卡片
  if (msgName === "internal") {
    const prefixMatch = contentString.match(/^【(.+?) (执行结果|研究进度)】\n([\s\S]*)$/);
    const topic = prefixMatch?.[1] ?? "研究员";
    const status = prefixMatch?.[2] ?? "执行结果";
    const agentContent = stripThinkContent(prefixMatch?.[3] ?? contentString).trim();
    return (
      <div className="mr-auto w-full">
        <AgentOutputCard topic={topic} status={status} content={agentContent} />
      </div>
    );
  }
  const [hideToolCalls] = useQueryState(
    "hideToolCalls",
    parseAsBoolean.withDefault(false),
  );

  const thread = useStreamContext();
  const isLastMessage =
    thread.messages[thread.messages.length - 1].id === message?.id;
  const hasNoAIOrToolMessages = !thread.messages.find(
    (m) => m.type === "ai" || m.type === "tool",
  );
  const meta = message ? thread.getMessagesMetadata(message) : undefined;
  const threadInterrupt = thread.interrupt;

  const parentCheckpoint = meta?.firstSeenState?.parent_checkpoint;
  const anthropicStreamedToolCalls = Array.isArray(content)
    ? parseAnthropicStreamedToolCalls(content)
    : undefined;

  const hasToolCalls =
    message &&
    "tool_calls" in message &&
    message.tool_calls &&
    message.tool_calls.length > 0;
  const toolCallsHaveContents =
    hasToolCalls &&
    message.tool_calls?.some(
      (tc) => tc.args && Object.keys(tc.args).length > 0,
    );
  const hasAnthropicToolCalls = !!anthropicStreamedToolCalls?.length;
  const isToolResult = message?.type === "tool";

  if (isToolResult && hideToolCalls) {
    return null;
  }

  return (
    <div className="group mr-auto flex w-full items-start gap-2">
      <div className="flex w-full flex-col gap-2">
        {isToolResult ? (
          <>
            <ToolResult message={message} />
            <Interrupt
              interrupt={threadInterrupt}
              isLastMessage={isLastMessage}
              hasNoAIOrToolMessages={hasNoAIOrToolMessages}
            />
          </>
        ) : (
          <>
            {mainContent.length > 0 && (
              <div className="py-1">
                <MarkdownText>{mainContent}</MarkdownText>
              </div>
            )}

            {!hideToolCalls && (
              <>
                {(hasToolCalls && toolCallsHaveContents && (
                  <ToolCalls toolCalls={message.tool_calls} />
                )) ||
                  (hasAnthropicToolCalls && (
                    <ToolCalls toolCalls={anthropicStreamedToolCalls} />
                  )) ||
                  (hasToolCalls && (
                    <ToolCalls toolCalls={message.tool_calls} />
                  ))}
              </>
            )}

            {message && (
              <CustomComponent
                message={message}
                thread={thread}
              />
            )}
            <Interrupt
              interrupt={threadInterrupt}
              isLastMessage={isLastMessage}
              hasNoAIOrToolMessages={hasNoAIOrToolMessages}
            />
            <div
              className={cn(
                "mr-auto flex items-center gap-2 transition-opacity",
                "opacity-0 group-focus-within:opacity-100 group-hover:opacity-100",
              )}
            >
              <BranchSwitcher
                branch={meta?.branch}
                branchOptions={meta?.branchOptions}
                onSelect={(branch) => thread.setBranch(branch)}
                isLoading={isLoading}
              />
              <CommandBar
                content={contentString}
                isLoading={isLoading}
                isAiMessage={true}
                handleRegenerate={() => handleRegenerate(parentCheckpoint)}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function AssistantMessageLoading() {
  return (
    <div className="mr-auto flex items-start gap-2">
      <div className="bg-muted flex h-8 items-center gap-1 rounded-2xl px-4 py-2">
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_infinite] rounded-full"></div>
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_0.5s_infinite] rounded-full"></div>
        <div className="bg-foreground/50 h-1.5 w-1.5 animate-[pulse_1.5s_ease-in-out_1s_infinite] rounded-full"></div>
      </div>
    </div>
  );
}
