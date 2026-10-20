"use client";

import { memo } from "react";
import type { TraceEntry } from "@/lib/api";

export type TraceTurn = { id: number; label: string; entries: TraceEntry[] };

const TOOL_LABELS: Record<string, string> = {
  track_shipment: "Track shipment",
  get_order: "Look up order",
  compare_rates: "Compare rates",
  validate_address: "Validate address",
  create_shipment: "Create shipment",
  draft_customer_email: "Draft customer email",
  analyze_documents: "Analyse documents",
};

function Badge({ entry }: { entry: TraceEntry }) {
  if (entry.status === "awaiting_confirmation")
    return <span className="badge wait">waiting for you</span>;
  if (entry.status === "approved")
    return <span className={`badge ${entry.ok ? "ok" : "fail"}`}>{entry.ok ? "booked" : "failed"}</span>;
  if (entry.status === "declined") return <span className="badge fail">cancelled</span>;
  if (entry.status === "rejected") return <span className="badge fail">blocked</span>;
  return <span className={`badge ${entry.ok ? "ok" : "fail"}`}>{entry.ok ? "ok" : "error"}</span>;
}

function Step({ entry }: { entry: TraceEntry }) {
  if (entry.type === "llm") {
    const asked = entry.tool_calls?.length
      ? `asked for ${entry.tool_calls.map((t) => TOOL_LABELS[t] ?? t).join(", ").toLowerCase()}`
      : "wrote the reply";
    return (
      <li className="step llm">
        <div>
          <div className="line">
            <span className="name">
              {entry.ok ? `${entry.model ?? "model"} ${asked}` : "Model unavailable"}
            </span>
            {entry.ok && <span className="meta">{entry.latency_ms} ms</span>}
          </div>
          {entry.fallback_from && (
            <span className="meta">
              {entry.fallback_from} was rate-limited, answered by {entry.provider}
            </span>
          )}
          {!entry.ok && entry.error && <span className="meta">{entry.error}</span>}
        </div>
      </li>
    );
  }
  if (entry.type === "guard") {
    return (
      <li className="step llm">
        <span className="name">Stopped: too many steps for one request</span>
      </li>
    );
  }
  const error = !entry.ok && typeof entry.result?.message === "string" ? entry.result.message : null;
  return (
    <li className="step">
      <div>
        <div className="line">
          <span className="name">{TOOL_LABELS[entry.name ?? ""] ?? entry.name}</span>
          <span className="meta">
            <Badge entry={entry} /> {entry.duration_ms ?? 0} ms
          </span>
        </div>
        {error && <span className="meta">{error}</span>}
      </div>
      <details>
        <summary>Arguments and result</summary>
        <pre>{JSON.stringify(entry.args ?? {}, null, 2)}</pre>
        <pre>{JSON.stringify(entry.result ?? {}, null, 2)}</pre>
      </details>
    </li>
  );
}

// Memoised: the page re-renders on every keystroke in the composer, and each tool step
// pretty-prints its full arguments and carrier response.
export const TraceLog = memo(function TraceLog({ turns }: { turns: TraceTurn[] }) {
  return (
    <section className="panel" aria-label="Agent trace">
      <h2>
        What the agent did <small>tool calls, in order</small>
      </h2>
      {turns.length === 0 ? (
        <p className="log-empty">
          Each request shows up here: which model ran, which tools it called, with the exact
          arguments and carrier responses.
        </p>
      ) : (
        <ol className="log" aria-live="polite">
          {turns.map((t) => (
            <li key={t.id} style={{ listStyle: "none" }}>
              <p className="turn-label" title={t.label}>
                {t.label}
              </p>
              <ol className="log" style={{ padding: 0 }}>
                {t.entries.map((e, i) => (
                  <Step key={i} entry={e} />
                ))}
              </ol>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
});
