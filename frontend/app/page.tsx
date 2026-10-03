"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { Docket, type DocketState } from "@/components/Docket";
import { DocumentsPanel } from "@/components/DocumentsPanel";
import { TraceLog, type TraceTurn } from "@/components/TraceLog";
import {
  api,
  ApiError,
  type DocumentRecord,
  type Health,
  type PendingConfirmation,
  type Sample,
  type TurnResult,
} from "@/lib/api";

type TextItem = { id: number; kind: "user" | "agent" | "note" | "error"; text: string };
type Item =
  | TextItem
  | {
      id: number;
      kind: "docket";
      pending: PendingConfirmation;
      state: DocketState;
      trackingNumber?: string;
    };

const SUGGESTIONS = [
  "Where is order 4512 going to Lagos?",
  "Why is order 4518 stuck?",
  "Cheapest way to send 12 kg from Mumbai to Nairobi within 10 days?",
  "Ship order 4550 with Standard",
  "Draft a delay email for order 4521",
  "Where is order 4540?",
];


function newSessionId(): string {
  try {
    const existing = sessionStorage.getItem("shipops-session");
    if (existing) return existing;
    const id = crypto.randomUUID().replace(/-/g, "");
    sessionStorage.setItem("shipops-session", id);
    return id;
  } catch {
    return Math.random().toString(36).slice(2);
  }
}

let nextId = 1;

// Markdown merges single newlines; agent replies (email drafts, signatures) need them kept.
const keepLineBreaks = (text: string) => text.replace(/([^\n])\n(?!\n)/g, "$1  \n");

export default function Home() {
  const [session, setSession] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [turns, setTurns] = useState<TraceTurn[]>([]);
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSession(newSessionId());
    api.health().then(setHealth, (e: Error) => setHealthError(e.message));
    api.samples().then(setSamples, () => setSamples([]));
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, busy]);

  const push = (...add: Omit<TextItem, "id">[]) =>
    setItems((prev) => [...prev, ...add.map((a) => ({ ...a, id: nextId++ }))]);

  const applyTurn = useCallback((label: string, res: TurnResult) => {
    setTurns((prev) => [...prev, { id: nextId++, label, entries: res.trace }]);
    const created = res.trace.find(
      (t) => t.name === "create_shipment" && t.status === "approved" && t.ok,
    );
    setItems((prev) => {
      // Any docket still open is resolved by this turn: booked, or cancelled/superseded.
      const next: Item[] = prev.map((it) =>
        it.kind === "docket" && (it.state === "pending" || it.state === "booking")
          ? {
              ...it,
              state: created ? "booked" : "cancelled",
              trackingNumber: created
                ? String((created.result as Record<string, unknown>)?.tracking_number ?? "")
                : undefined,
            }
          : it,
      );
      if (res.pending_confirmation) {
        next.push({ id: nextId++, kind: "docket", pending: res.pending_confirmation, state: "pending" });
      } else if (res.reply) {
        next.push({ id: nextId++, kind: "agent", text: res.reply });
      }
      return next;
    });
  }, []);

  const fail = (e: unknown) =>
    push({ kind: "error", text: e instanceof ApiError || e instanceof Error ? e.message : String(e) });

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy || !session) return;
    setDraft("");
    setShowTrace(false); // on mobile, return to the conversation to show the answer
    push({ kind: "user", text: message });
    setBusy(true);
    try {
      applyTurn(message, await api.chat(session, message));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function decide(approved: boolean) {
    if (busy) return;
    setItems((prev) =>
      prev.map((it) =>
        it.kind === "docket" && it.state === "pending" && approved ? { ...it, state: "booking" } : it,
      ),
    );
    setBusy(true);
    try {
      applyTurn(approved ? "You approved the booking" : "You cancelled the booking", await api.confirm(session, approved));
    } catch (e) {
      setItems((prev) =>
        prev.map((it) => (it.kind === "docket" && it.state === "booking" ? { ...it, state: "pending" } : it)),
      );
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function upload(files: File[]) {
    const pdfs = files.filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    if (pdfs.length < files.length) push({ kind: "error", text: "Only PDF files can be checked." });
    const limitMb = health?.max_upload_mb ?? 5;
    const tooBig = pdfs.filter((f) => f.size > limitMb * 1024 * 1024);
    if (tooBig.length) push({ kind: "error", text: `${tooBig.map((f) => f.name).join(", ")} is over ${limitMb} MB.` });
    const ok = pdfs.filter((f) => !tooBig.includes(f));
    if (!ok.length) return;
    setBusy(true);
    try {
      for (const f of ok) {
        const rec = await api.upload(f);
        setDocs((prev) => [rec, ...prev.filter((d) => d.document_id !== rec.document_id)]);
      }
      push({ kind: "note", text: `Read ${ok.length} document${ok.length > 1 ? "s" : ""}. Ask the agent to check them, or see the summary on the right.` });
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function loadSample(id: string) {
    if (busy) return;
    setBusy(true);
    let prompt = "";
    try {
      const res = await api.loadSample(id);
      setDocs((prev) => [...res.documents, ...prev.filter((d) => !res.documents.some((r) => r.document_id === d.document_id))]);
      const refs = res.documents.map((d) => `${d.document_id} (${d.filename})`).join(" and ");
      prompt = `${res.prompt} Documents: ${refs}.`;
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
    if (prompt) await send(prompt);
  }

  const maxMb = health?.max_upload_mb ?? 5;
  const offline = healthError !== null || health?.status === "degraded";

  return (
    <div className={`shell${showTrace ? " show-trace" : ""}`}>
      <header className="masthead">
        <h1 className="wordmark">Shipment Ops</h1>
        <span className="who">Bengal Craft Exports dispatch desk</span>
        <div className="status">
          {health && (
            <span>
              <span
                className={`dot${offline ? " bad" : ""}`}
                role="img"
                aria-label={offline ? "Agent degraded" : "Agent online"}
              />
              <span className="status-text">
                {health.llm_provider === "offline"
                  ? "Rule-based mode (no LLM)"
                  : `Model: ${health.llm_provider}`}
                {", "}carrier: {health.carrier}
                {health.demo_mode ? ", demo" : ""}
              </span>
            </span>
          )}
          {healthError && (
            <span>
              <span className="dot bad" aria-hidden="true" />
              API offline
            </span>
          )}
          <button
            type="button"
            className="btn quiet trace-toggle"
            aria-expanded={showTrace}
            onClick={() => setShowTrace((v) => !v)}
          >
            {showTrace ? "Back to chat" : "Trace & documents"}
          </button>
        </div>
      </header>

      <main className="workspace">
        <section className="conversation" aria-label="Conversation">
          <div className="messages">
            {items.length === 0 && (
              <div className="empty">
                <h2>What needs shipping today?</h2>
                <p>
                  Ask about an order or tracking number, compare rates, book a shipment, or drop a
                  commercial invoice or bill of lading on the right. Bookings always wait for your
                  approval.
                </p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} type="button" className="chip" onClick={() => send(s)} disabled={busy || !session}>
                      {s}
                    </button>
                  ))}
                </div>
                {healthError && <p className="msg error">{healthError}</p>}
                {health?.error && <p className="msg error">{health.error}</p>}
              </div>
            )}
            {items.map((it) =>
              it.kind === "docket" ? (
                <Docket
                  key={it.id}
                  summary={it.pending.summary}
                  state={it.state}
                  trackingNumber={it.trackingNumber}
                  onDecide={decide}
                />
              ) : it.kind === "agent" ? (
                <div key={it.id} className="msg agent">
                  <div className="md">
                    <ReactMarkdown>{keepLineBreaks(it.text)}</ReactMarkdown>
                  </div>
                </div>
              ) : (
                <div key={it.id} className={`msg ${it.kind}`} role={it.kind === "error" ? "alert" : undefined}>
                  {it.text}
                </div>
              ),
            )}
            {busy && <p className="thinking">Working on it…</p>}
            <div ref={bottom} />
          </div>

          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void send(draft);
            }}
          >
            <label htmlFor="composer-input" className="visually-hidden">
              Message the agent
            </label>
            <textarea
              id="composer-input"
              rows={1}
              value={draft}
              placeholder="Ask the agent…"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(draft);
                }
              }}
              maxLength={4000}
            />
            <button type="submit" className="btn" disabled={busy || !draft.trim() || !session}>
              Send
            </button>
          </form>
        </section>

        <aside className="side">
          <TraceLog turns={turns} />
          <DocumentsPanel
            docs={docs}
            samples={samples}
            busy={busy}
            maxMb={maxMb}
            onUpload={upload}
            onSample={loadSample}
            onAsk={(d) => send(`Please check ${d.document_id} (${d.filename}) for problems.`)}
          />
        </aside>
      </main>
    </div>
  );
}
