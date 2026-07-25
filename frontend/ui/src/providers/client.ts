import { Client } from "@langchain/langgraph-sdk";

// The LangGraph SDK builds requests with `new URL(apiUrl + path)`, which throws
// for relative URLs. When apiUrl is relative (e.g. "/api" for the Next.js proxy),
// resolve it against the current origin so the same build works on localhost and
// over the LAN without hardcoding a server IP.
export function resolveApiUrl(apiUrl: string): string {
  if (!apiUrl || /^https?:\/\//i.test(apiUrl)) return apiUrl;
  if (typeof window === "undefined") return apiUrl;
  const base = window.location.origin;
  return apiUrl.startsWith("/") ? `${base}${apiUrl}` : `${base}/${apiUrl}`;
}

export function createClient(
  apiUrl: string,
  apiKey: string | undefined,
  authScheme: string | undefined,
) {
  return new Client({
    apiKey,
    apiUrl: resolveApiUrl(apiUrl),
    ...(authScheme && {
      defaultHeaders: {
        "X-Auth-Scheme": authScheme,
      },
    }),
  });
}
