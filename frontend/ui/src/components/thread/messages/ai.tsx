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
  XCircle,
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
  const matches =
    text.match(/[\w一-鿿\-（）()]+_\d{4}\.(jpg|jpeg|png)/gi) ?? [];
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
  execution_id?: string;
  task_id?: string;
  status?: "completed" | "failed";
  events: Array<{
    sequence: number;
    stage: string;
    detail: string;
    content?: string;
    status: "running" | "finalizing" | "completed" | "failed";
  }>;
  process: string[];
  searches: AgentSearchGroup[];
  tools: Array<{ tool: string; summary: string }>;
  findings: string;
};

function parseAgentCardPayload(content: string): AgentCardPayload {
  try {
    const parsed = JSON.parse(content) as Partial<AgentCardPayload>;
    if (parsed.version === 2 || parsed.version === 3) {
      return {
        version: parsed.version,
        execution_id:
          typeof parsed.execution_id === "string"
            ? parsed.execution_id
            : undefined,
        task_id:
          typeof parsed.task_id === "string" ? parsed.task_id : undefined,
        status:
          parsed.status === "failed" || parsed.status === "completed"
            ? parsed.status
            : undefined,
        events: Array.isArray(parsed.events) ? parsed.events : [],
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
    ...content.matchAll(
      /•\s*(正在搜索：[^\n]+|正在分析卫星影像[.…]*|正在查询 POI 数据[^\n]*)/g,
    ),
  ].map((match) => match[1].trim());
  const markerIndexes = ["【研究结论", "根据已有证据"]
    .map((marker) => content.indexOf(marker))
    .filter((index) => index >= 0);
  const findingsStart =
    markerIndexes.length > 0 ? Math.min(...markerIndexes) : -1;

  return {
    version: 1,
    events: [],
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
function AgentOutputCard({
  topic,
  status,
  content,
}: {
  topic: string;
  status: string;
  content: string;
}) {
  const payload = parseAgentCardPayload(content);
  const stream = useStreamContext();
  const [legacyOpen, setLegacyOpen] = useState(false);
  const open = payload.execution_id
    ? (stream.agentCardOpen[payload.execution_id] ?? false)
    : legacyOpen;
  const toggleOpen = () => {
    if (payload.execution_id) {
      stream.toggleAgentCard(payload.execution_id);
    } else {
      setLegacyOpen((value) => !value);
    }
  };
  const isFailed = payload.status === "failed" || status === "执行失败";
  const imageFiles = extractImageFiles(content);
  const timelineEvents =
    payload.events.length > 0
      ? payload.events
      : payload.process.map((detail, index) => ({
          sequence: index + 1,
          stage: "process",
          detail,
          content: undefined,
          status: "completed" as const,
        }));
  const sourceCount = payload.searches.reduce(
    (total, group) => total + group.results.length,
    0,
  );
  const searchTaskCount =
    payload.searches.length ||
    payload.process.filter((step) => step.startsWith("正在搜索：")).length;

  return (
    <div className="border-border/50 bg-background overflow-hidden rounded-xl border text-sm shadow-sm">
      <button
        type="button"
        aria-expanded={open}
        className="hover:bg-muted/35 flex w-full items-center gap-2 px-4 py-3 text-left transition-colors"
        onClick={toggleOpen}
      >
        {open ? (
          <ChevronDown className="text-foreground/40 h-4 w-4 shrink-0" />
        ) : (
          <ChevronRight className="text-foreground/40 h-4 w-4 shrink-0" />
        )}
        {isFailed ? (
          <XCircle className="h-4 w-4 shrink-0 text-rose-500" />
        ) : (
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
        )}
        <span className="text-foreground/75 min-w-0 flex-1 truncate font-medium">
          {topic}
        </span>
        <span
          className={cn(
            "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
            isFailed
              ? "bg-rose-50 text-rose-700"
              : "bg-emerald-50 text-emerald-700",
          )}
        >
          {isFailed ? "执行失败" : "已完成"}
        </span>
      </button>

      {open && (
        <div className="border-border/40 bg-muted/10 flex flex-col gap-5 border-t px-4 py-4">
          <div className="grid grid-cols-3 gap-2">
            <div className="border-border/40 bg-background rounded-lg border px-3 py-2">
              <p className="text-foreground/45 text-[11px]">执行步骤</p>
              <p className="text-foreground/75 mt-0.5 text-base font-semibold">
                {payload.process.length}
              </p>
            </div>
            <div className="border-border/40 bg-background rounded-lg border px-3 py-2">
              <p className="text-foreground/45 text-[11px]">检索任务</p>
              <p className="text-foreground/75 mt-0.5 text-base font-semibold">
                {searchTaskCount}
              </p>
            </div>
            <div className="border-border/40 bg-background rounded-lg border px-3 py-2">
              <p className="text-foreground/45 text-[11px]">有效来源</p>
              <p className="text-foreground/75 mt-0.5 text-base font-semibold">
                {payload.version === 1 ? "—" : sourceCount}
              </p>
            </div>
          </div>

          {timelineEvents.length > 0 && (
            <section>
              <div className="text-foreground/55 mb-2 flex items-center gap-2 text-xs font-semibold">
                <ListChecks className="h-4 w-4" />
                执行过程
              </div>
              <div className="border-border/60 ml-1 border-l pl-4">
                {timelineEvents.map((event, index) => (
                  <div
                    key={`${event.sequence}-${event.stage}-${index}`}
                    className="relative pb-3 last:pb-0"
                  >
                    <span
                      className={cn(
                        "border-background ring-border absolute top-1.5 -left-[19px] h-2 w-2 rounded-full border-2 ring-1",
                        event.status === "failed"
                          ? "bg-rose-500"
                          : "bg-emerald-500",
                      )}
                    />
                    <p className="text-foreground/65 text-xs leading-5 whitespace-pre-wrap">
                      {event.detail}
                    </p>
                    {event.content &&
                      event.stage !== "finding" &&
                      event.content.trim() !== event.detail.trim() && (
                        <pre className="bg-muted/40 text-foreground/55 mt-1.5 max-h-52 overflow-auto rounded-md px-2.5 py-2 text-[11px] leading-5 whitespace-pre-wrap">
                          {event.content}
                        </pre>
                      )}
                  </div>
                ))}
              </div>
            </section>
          )}

          {payload.searches.length > 0 && (
            <section>
              <div className="text-foreground/55 mb-2 flex items-center gap-2 text-xs font-semibold">
                <Search className="h-4 w-4" />
                检索结果
              </div>
              <div className="flex flex-col gap-2">
                {payload.searches.map((group, index) => (
                  <details
                    key={`${group.query}-${index}`}
                    className="group border-border/40 bg-background rounded-lg border"
                  >
                    <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5">
                      <ChevronRight className="text-foreground/35 h-3.5 w-3.5 shrink-0 transition-transform group-open:rotate-90" />
                      <span className="text-foreground/70 min-w-0 flex-1 truncate text-xs font-medium">
                        {group.query || `检索任务 ${index + 1}`}
                      </span>
                      <span className="text-foreground/40 shrink-0 text-[11px]">
                        {group.results.length} 条
                      </span>
                    </summary>
                    <div className="border-border/30 flex flex-col gap-2 border-t px-3 py-3">
                      {group.error && (
                        <p className="text-xs text-rose-600">{group.error}</p>
                      )}
                      {group.results.map((result, resultIndex) => (
                        <div
                          key={`${result.url}-${resultIndex}`}
                          className="bg-muted/35 rounded-md px-3 py-2"
                        >
                          <div className="flex items-start gap-2">
                            <span className="text-foreground/35 mt-0.5 shrink-0 text-[11px] font-medium">
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
                                <p className="text-foreground/70 text-xs font-medium">
                                  {result.title || "未命名结果"}
                                </p>
                              )}
                              {result.source_label && (
                                <span className="bg-background text-foreground/45 mt-1 inline-block rounded px-1.5 py-0.5 text-[10px]">
                                  {result.source_label}
                                </span>
                              )}
                              {result.snippet && (
                                <p className="text-foreground/55 mt-1.5 line-clamp-3 text-[11px] leading-5">
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
              <div className="text-foreground/55 mb-2 flex items-center gap-2 text-xs font-semibold">
                <FileText className="h-4 w-4" />
                工具结果
              </div>
              <div className="flex flex-col gap-2">
                {payload.tools.map((tool, index) => (
                  <details
                    key={`${tool.tool}-${index}`}
                    className="border-border/40 bg-background rounded-lg border"
                  >
                    <summary className="text-foreground/65 cursor-pointer px-3 py-2 text-xs font-medium">
                      {tool.tool}
                    </summary>
                    <pre className="border-border/30 text-foreground/55 overflow-x-auto border-t px-3 py-2 text-[11px] leading-5 whitespace-pre-wrap">
                      {tool.summary}
                    </pre>
                  </details>
                ))}
              </div>
            </section>
          )}

          {imageFiles.length > 0 && (
            <section>
              <div className="text-foreground/55 mb-2 text-xs font-semibold">
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
                    <span className="absolute right-0 bottom-0 left-0 truncate rounded-b-md bg-black/55 px-1 py-0.5 text-center text-[10px] text-white">
                      {file.match(/_(\d{4})\./)?.[1] ?? file}
                    </span>
                  </a>
                ))}
              </div>
            </section>
          )}

          <section>
            <div className="text-foreground/55 mb-2 flex items-center gap-2 text-xs font-semibold">
              <FileText className="h-4 w-4" />
              研究结论
            </div>
            <div className="border-border/40 bg-background text-foreground/70 rounded-lg border px-4 py-3">
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

type AssistantMessageProps = {
  message: Message | undefined;
  isLoading: boolean;
  handleRegenerate: (parentCheckpoint: Checkpoint | null | undefined) => void;
};

function InternalAssistantMessage({
  message,
}: Pick<AssistantMessageProps, "message">) {
  const contentString = getContentString(message?.content ?? []);
  const prefixMatch = contentString.match(
    /^【(.+?) (执行结果|研究进度|执行失败)】\n([\s\S]*)$/,
  );
  const topic = prefixMatch?.[1] ?? "研究员";
  const status = prefixMatch?.[2] ?? "执行结果";
  const agentContent = stripThinkContent(
    prefixMatch?.[3] ?? contentString,
  ).trim();

  return (
    <div className="mr-auto w-full">
      <AgentOutputCard
        topic={topic}
        status={status}
        content={agentContent}
      />
    </div>
  );
}

export function AssistantMessage(props: AssistantMessageProps) {
  const msgName = (props.message as Record<string, unknown> | undefined)
    ?.name as string | undefined;
  if (msgName === "internal") {
    return <InternalAssistantMessage message={props.message} />;
  }
  return <RegularAssistantMessage {...props} />;
}

function RegularAssistantMessage({
  message,
  isLoading,
  handleRegenerate,
}: AssistantMessageProps) {
  const content = message?.content ?? [];
  const contentString = getContentString(content);
  const mainContent = stripThinkContent(contentString);
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
            {(() => {
              const renderText =
                isLastMessage && thread.streamingReport.length > 0 && isLoading
                  ? thread.streamingReport
                  : mainContent;
              return renderText.length > 0 ? (
                <div className="py-1">
                  <MarkdownText>{renderText}</MarkdownText>
                </div>
              ) : null;
            })()}

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
