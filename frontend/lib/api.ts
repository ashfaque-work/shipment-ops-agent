export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(
  /\/$/,
  "",
);

export type TraceEntry = {
  type: "llm" | "tool" | "guard";
  ok: boolean;
  name?: string;
  id?: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
  status?: "awaiting_confirmation" | "rejected" | "approved" | "declined";
  duration_ms?: number;
  provider?: string;
  model?: string;
  latency_ms?: number;
  fallback_from?: string | null;
  tool_calls?: string[];
  error?: string;
};

export type Party = {
  name?: string;
  company?: string;
  street1?: string;
  street2?: string;
  city?: string;
  postal_code?: string;
  country?: string;
  phone?: string;
  email?: string;
};

export type ShipmentSummary = {
  shipper: Party;
  recipient: Party;
  package: {
    weight_kg: number;
    dimensions?: { length_cm: number; width_cm: number; height_cm: number } | null;
    description: string;
    declared_value: number;
    currency: string;
  };
  service_code: string;
  service_name: string;
  price: number;
  currency: string;
  transit_days: string;
  estimated_delivery: string;
  reference?: string | null;
};

export type PendingConfirmation = {
  type: "confirm_shipment";
  tool_call_id: string;
  summary: ShipmentSummary;
};

export type TurnResult = {
  session_id: string;
  reply: string | null;
  pending_confirmation: PendingConfirmation | null;
  trace: TraceEntry[];
};

export type DocIssue = {
  code: string;
  field: string;
  message: string;
  severity: "error" | "warning";
};

export type DocumentRecord = {
  document_id: string;
  filename: string;
  doc_type: "commercial_invoice" | "bill_of_lading" | null;
  method: string;
  scanned: boolean;
  fields: Record<string, unknown>;
  issues: DocIssue[];
  warnings: string[];
};

export type Sample = { id: string; title: string; prompt: string; files: number };

export type Health = {
  status: "ok" | "degraded";
  error: string | null;
  llm_provider: string;
  carrier: string;
  demo_mode: boolean;
  max_upload_mb: number;
  notice: string | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

// A turn can chain several model and carrier calls (the outage scenario alone waits out
// carrier retries), so allow minutes - but never leave the UI "working" forever.
const TIMEOUT_MS = 180_000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") {
      throw new ApiError("The agent took too long to answer. Please try again.", 0);
    }
    throw new ApiError(`Can't reach the agent API at ${API_URL}. Is the backend running?`, 0);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail =
        typeof body.detail === "string"
          ? body.detail
          : Array.isArray(body.detail) && typeof body.detail[0]?.msg === "string"
            ? body.detail[0].msg // FastAPI validation error: show the reason, not the JSON
            : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<Health>("/api/health"),
  chat: (session_id: string, message: string) =>
    request<TurnResult>("/api/chat", json({ session_id, message })),
  confirm: (session_id: string, approved: boolean) =>
    request<TurnResult>("/api/chat/confirm", json({ session_id, approved })),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DocumentRecord>("/api/documents", { method: "POST", body: form });
  },
  samples: () => request<Sample[]>("/api/samples"),
  loadSample: (id: string) =>
    request<{ id: string; prompt: string; documents: DocumentRecord[] }>(
      `/api/samples/${encodeURIComponent(id)}`,
      { method: "POST" },
    ),
};
