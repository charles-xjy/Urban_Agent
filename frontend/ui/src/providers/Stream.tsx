import React, {
  createContext,
  useContext,
  ReactNode,
  useState,
  useEffect,
  useCallback,
  useRef,
} from "react";
import { useStream } from "@langchain/langgraph-sdk/react";
import { type Message } from "@langchain/langgraph-sdk";
import {
  uiMessageReducer,
  isUIMessage,
  isRemoveUIMessage,
  type UIMessage,
  type RemoveUIMessage,
} from "@langchain/langgraph-sdk/react-ui";
import { useQueryState } from "nuqs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { LangGraphLogoSVG } from "@/components/icons/langgraph";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ArrowRight } from "lucide-react";
import { PasswordInput } from "@/components/ui/password-input";
import { getApiKey } from "@/lib/api-key";
import { useThreads } from "./Thread";
import { createClient, resolveApiUrl } from "./client";
import { toast } from "sonner";

export type StateType = {
  messages: Message[];
  ui?: UIMessage[];
  plan?: Array<{ id: string; topic: string; description: string }>;
  findings?: string[];
  report?: string;
};

/** Researcher 执行中的实时进度事件（后端 get_stream_writer 发出）。 */
export type ResearchProgressEvent = {
  type: "research_progress";
  task_id: string;
  topic: string;
  stage: string;
  detail: string;
  round: number;
};

/** 单个 task 的进度条目。 */
export type ResearchProgressItem = {
  task_id: string;
  topic: string;
  stage: string;
  detail: string;
  round: number;
  history: Array<{
    stage: string;
    detail: string;
    round: number;
  }>;
  /** 最后一次更新时间（用于排序/清理）。 */
  ts: number;
};

const useTypedStream = useStream<
  StateType,
  {
    UpdateType: {
      messages?: Message[] | Message | string;
      ui?: (UIMessage | RemoveUIMessage)[] | UIMessage | RemoveUIMessage;
      context?: Record<string, unknown>;
    };
    CustomEventType: UIMessage | RemoveUIMessage | ResearchProgressEvent;
  }
>;

type StreamContextType = ReturnType<typeof useTypedStream> & {
  researchProgress: Record<string, ResearchProgressItem>;
};
const StreamContext = createContext<StreamContextType | undefined>(undefined);

async function sleep(ms = 4000) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function checkGraphStatus(
  apiUrl: string,
  apiKey: string | null,
  authScheme?: string,
): Promise<boolean> {
  try {
    const headers = new Headers();
    if (apiKey) headers.set("X-Api-Key", apiKey);
    if (authScheme) headers.set("X-Auth-Scheme", authScheme);

    const res = await fetch(`${apiUrl}/info`, {
      headers,
    });

    return res.ok;
  } catch (e) {
    console.error(e);
    return false;
  }
}

const StreamSession = ({
  children,
  apiKey,
  apiUrl,
  assistantId,
  authScheme,
  onThreadCreated,
}: {
  children: ReactNode;
  apiKey: string | null;
  apiUrl: string;
  assistantId: string;
  authScheme?: string;
  onThreadCreated: () => void;
}) => {
  const [threadId, setThreadId] = useQueryState("threadId");
  const { getThreads, setThreads } = useThreads();
  // researcher 实时进度：task_id -> 最新进度条目。不进 graph state / checkpoint。
  const [researchProgress, setResearchProgress] = useState<
    Record<string, ResearchProgressItem>
  >({});
  const streamValue = useTypedStream({
    apiUrl,
    apiKey: apiKey ?? undefined,
    assistantId,
    ...(authScheme && {
      defaultHeaders: {
        "X-Auth-Scheme": authScheme,
      },
    }),
    threadId: threadId ?? null,
    fetchStateHistory: true,
    onCustomEvent: (event, options) => {
      if (isUIMessage(event) || isRemoveUIMessage(event)) {
        options.mutate((prev) => {
          const ui = uiMessageReducer(prev.ui ?? [], event);
          return { ...prev, ui };
        });
        return;
      }
      // researcher 实时进度事件：按 task_id 聚合到本地 state。
      if (
        event &&
        typeof event === "object" &&
        (event as ResearchProgressEvent).type === "research_progress"
      ) {
        const ev = event as ResearchProgressEvent;
        setResearchProgress((prev) => {
          // stage === "complete" 表示该 task 已结束，清掉进度（卡片接管展示）。
          if (ev.stage === "complete") {
            const next = { ...prev };
            delete next[ev.task_id];
            return next;
          }
          const previous = prev[ev.task_id];
          const previousHistory = previous?.history ?? [];
          const lastEvent = previousHistory.at(-1);
          const history =
            lastEvent?.stage === ev.stage && lastEvent.detail === ev.detail
              ? previousHistory
              : [
                  ...previousHistory,
                  {
                    stage: ev.stage,
                    detail: ev.detail,
                    round: ev.round,
                  },
                ];
          return {
            ...prev,
            [ev.task_id]: {
              task_id: ev.task_id,
              topic: ev.topic,
              stage: ev.stage,
              detail: ev.detail,
              round: ev.round,
              history,
              ts: Date.now(),
            },
          };
        });
      }
    },
    onThreadId: (id) => {
      onThreadCreated();
      setThreadId(id);
      // Refetch threads list when thread ID changes.
      // Wait for some seconds before fetching so we're able to get the new thread that was created.
      sleep().then(() => getThreads().then(setThreads).catch(console.error));
    },
  });

  useEffect(() => {
    checkGraphStatus(apiUrl, apiKey, authScheme).then((ok) => {
      if (!ok) {
        toast.error("Failed to connect to LangGraph server", {
          description: () => (
            <p>
              Please ensure your graph is running at <code>{apiUrl}</code> and
              your API key is correctly set (if connecting to a deployed graph).
            </p>
          ),
          duration: 10000,
          richColors: true,
          closeButton: true,
        });
      }
    });
  }, [apiKey, apiUrl, authScheme]);

  // useStream exposes enumerable getters that subscribe to stream modes when
  // read. Spreading streamValue would read every getter, including
  // toolProgress, and incorrectly add the unsupported "tools" mode.
  const contextValue = Object.assign(
    Object.create(streamValue) as ReturnType<typeof useTypedStream>,
    { researchProgress },
  );

  return (
    <StreamContext.Provider value={contextValue}>
      {children}
    </StreamContext.Provider>
  );
};

// Default values for the form
const DEFAULT_API_URL = "http://localhost:2024";
const DEFAULT_ASSISTANT_ID = "agent";
const AGENT_BUILDER_AUTH_SCHEME = "langsmith-api-key";

export const StreamProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  // Get environment variables
  const envApiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL;
  const envAssistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID;
  const envAuthScheme: string | undefined = process.env.NEXT_PUBLIC_AUTH_SCHEME;

  // Use URL params with env var fallbacks
  const [apiUrl, setApiUrl] = useQueryState("apiUrl", {
    defaultValue: envApiUrl || "",
  });
  const [assistantId, setAssistantId] = useQueryState("assistantId", {
    defaultValue: envAssistantId || "",
  });
  const [authScheme, setAuthScheme] = useQueryState("authScheme", {
    defaultValue: envAuthScheme || "",
  });
  const [threadId, setThreadId] = useQueryState("threadId");

  // sessionKey decides when StreamSession remounts. Remounting on explicit
  // navigation (switching threads / new chat) clears stale state, but we must
  // NOT remount when the stream itself creates a thread (first message) — that
  // would wipe the in-progress message and blank the page.
  const [sessionKey, setSessionKey] = useState(threadId ?? "new");
  const streamCreatedRef = useRef(false);
  const skipThreadValidationRef = useRef(false);
  const handleThreadCreated = useCallback(() => {
    streamCreatedRef.current = true;
    skipThreadValidationRef.current = true;
  }, []);
  useEffect(() => {
    if (streamCreatedRef.current) {
      streamCreatedRef.current = false;
      return;
    }
    setSessionKey(threadId ?? "new");
  }, [threadId]);

  const [isAgentBuilder, setIsAgentBuilder] = useState(
    () =>
      (authScheme || envAuthScheme || "").toLowerCase() ===
      AGENT_BUILDER_AUTH_SCHEME,
  );

  // For API key, use localStorage with env var fallback
  const [apiKey, _setApiKey] = useState(() => {
    const storedKey = getApiKey();
    return storedKey || "";
  });

  const setApiKey = (key: string) => {
    window.localStorage.setItem("lg:chat:apiKey", key);
    _setApiKey(key);
  };

  // Determine final values to use, prioritizing URL params then env vars
  const finalApiUrl = resolveApiUrl(apiUrl || envApiUrl || "");
  const finalAssistantId = assistantId || envAssistantId;
  const finalAuthScheme = authScheme || envAuthScheme || "";
  const [threadValidation, setThreadValidation] = useState<
    "checking" | "ready"
  >(threadId ? "checking" : "ready");

  useEffect(() => {
    if (!threadId || !finalApiUrl) {
      setThreadValidation("ready");
      return;
    }
    if (skipThreadValidationRef.current) {
      skipThreadValidationRef.current = false;
      setThreadValidation("ready");
      return;
    }

    const controller = new AbortController();
    setThreadValidation("checking");
    const client = createClient(
      finalApiUrl,
      apiKey || undefined,
      finalAuthScheme || undefined,
    );

    client.threads
      .getState(threadId, undefined, { signal: controller.signal })
      .then(() => setThreadValidation("ready"))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message =
          error instanceof Error ? error.message : String(error ?? "");
        const isMissingThread =
          message.includes("HTTP 404") ||
          message.includes("Thread with ID") ||
          message.toLowerCase().includes("not found");

        if (isMissingThread) {
          setThreadId(null);
          setThreadValidation("ready");
          toast.info("原对话已失效，已为你切换到新对话");
          return;
        }

        // 非 404 错误交给 StreamSession 的连接错误处理。
        setThreadValidation("ready");
      });

    return () => controller.abort();
  }, [
    apiKey,
    finalApiUrl,
    finalAuthScheme,
    setThreadId,
    threadId,
  ]);

  if (threadValidation === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        正在加载对话…
      </div>
    );
  }

  // Show the form if we: don't have an API URL, or don't have an assistant ID
  if (!finalApiUrl || !finalAssistantId) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 bg-background flex max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="mt-14 flex flex-col gap-2 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <LangGraphLogoSVG className="h-7" />
              <h1 className="text-xl font-semibold tracking-tight">
                城市变化研究智能体
              </h1>
            </div>
            <p className="text-muted-foreground">
              欢迎使用城市变化研究智能体！开始前，请输入部署地址以及 assistant / graph ID。
            </p>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();

              const form = e.target as HTMLFormElement;
              const formData = new FormData(form);
              const apiUrl = formData.get("apiUrl") as string;
              const assistantId = formData.get("assistantId") as string;
              const apiKey = formData.get("apiKey") as string;

              setApiUrl(apiUrl);
              setApiKey(apiKey);
              setAssistantId(assistantId);
              setAuthScheme(isAgentBuilder ? AGENT_BUILDER_AUTH_SCHEME : "");

              form.reset();
            }}
            className="bg-muted/50 flex flex-col gap-6 p-6"
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="apiUrl">
                Deployment URL<span className="text-rose-500">*</span>
              </Label>
              <p className="text-muted-foreground text-sm">
                This is the URL of your LangGraph deployment. Can be a local, or
                production deployment.
              </p>
              <Input
                id="apiUrl"
                name="apiUrl"
                className="bg-background"
                defaultValue={apiUrl || DEFAULT_API_URL}
                required
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="assistantId">
                Assistant / Graph ID<span className="text-rose-500">*</span>
              </Label>
              <p className="text-muted-foreground text-sm">
                This is the ID of the graph (can be the graph name), or
                assistant to fetch threads from, and invoke when actions are
                taken.
              </p>
              <Input
                id="assistantId"
                name="assistantId"
                className="bg-background"
                defaultValue={assistantId || DEFAULT_ASSISTANT_ID}
                required
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="apiKey">LangSmith API Key</Label>
              <p className="text-muted-foreground text-sm">
                This is <strong>NOT</strong> required if using a local LangGraph
                server. This value is stored in your browser's local storage and
                is only used to authenticate requests sent to your LangGraph
                server.
              </p>
              <PasswordInput
                id="apiKey"
                name="apiKey"
                defaultValue={apiKey ?? ""}
                className="bg-background"
                placeholder="lsv2_pt_..."
              />
            </div>

            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-4">
                <div className="flex flex-col gap-1">
                  <Label htmlFor="agentBuilderEnabled">
                    Built with Agent Builder
                  </Label>
                  <p className="text-muted-foreground text-sm">
                    Enable this for Agent Builder deployments.
                  </p>
                </div>
                <Switch
                  id="agentBuilderEnabled"
                  checked={isAgentBuilder}
                  onCheckedChange={setIsAgentBuilder}
                />
              </div>
            </div>

            <div className="mt-2 flex justify-end">
              <Button
                type="submit"
                size="lg"
              >
                Continue
                <ArrowRight className="size-5" />
              </Button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <StreamSession
      key={sessionKey}
      apiKey={apiKey}
      apiUrl={finalApiUrl}
      assistantId={finalAssistantId}
      authScheme={finalAuthScheme || undefined}
      onThreadCreated={handleThreadCreated}
    >
      {children}
    </StreamSession>
  );
};

// Create a custom hook to use the context
export const useStreamContext = (): StreamContextType => {
  const context = useContext(StreamContext);
  if (context === undefined) {
    throw new Error("useStreamContext must be used within a StreamProvider");
  }
  return context;
};

export default StreamContext;
