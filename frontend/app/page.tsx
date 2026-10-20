"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
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

const SESSION_KEY = "shipops-session";

function randomId(): string {
  try {
    return crypto.randomUUID().replace(/-/g, "");
  } catch {
    return Math.random().toString(36).slice(2);
  }
}

function sessionId(fresh = false): string {
  try {
    const existing = sessionStorage.getItem(SESSION_KEY);
    if (existing && !fresh) return existing;
    const id = randomId();
    sessionStorage.setItem(SESSION_KEY, id);
    return id;
  } catch {
    return randomId();
  }
}

let nextId = 1;

// Markdown merges single newlines; agent replies (email drafts, signatures) need them kept.
const keepLineBreaks = (text: string) => text.replace(/([^\n])\n(?!\n)/g, "$1  \n");

function AgentMessage({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked (insecure origin, permissions): nothing useful to show */
    }
  }
  return (
    <div className="msg agent">
      <div className="md">
        <ReactMarkdown>{keepLineBreaks(text)}</ReactMarkdown>
      </div>
      <button type="button" className="copy" onClick={copy} aria-label="Copy this reply">
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

// Memoised so typing in the composer doesn't re-parse every reply's Markdown.
const Messages = memo(function Messages({
  items,
  onDecide,
}: {
  items: Item[];
  onDecide: (approved: boolean) => void;
}) {
  return items.map((it) =>
    it.kind === "docket" ? (
      <Docket
        key={it.id}
        summary={it.pending.summary}
        state={it.state}
        trackingNumber={it.trackingNumber}
        onDecide={onDecide}
      />
    ) : it.kind === "agent" ? (
      <AgentMessage key={it.id} text={it.text} />
    ) : (
      <div key={it.id} className={`msg ${it.kind}`} role={it.kind === "error" ? "alert" : undefined}>
        {it.text}
      </div>
    ),
  );
});

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
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setSession(sessionId());
    api.health().then(setHealth, (e: Error) => setHealthError(e.message));
    api.samples().then(setSamples, () => setSamples([]));
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, busy]);

  // Grow the composer with its content (CSS caps the height).
  useEffect(() => {
    const el = composer.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight + el.offsetHeight - el.clientHeight}px`; // + borders
  }, [draft]);

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
      } else if (res.reply?.trim()) {
        next.push({ id: nextId++, kind: "agent", text: res.reply });
      } else {
        next.push({
          id: nextId++,
          kind: "note",
          text: "The agent finished without writing a reply. The trace shows what it did.",
        });
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
    if (busy) return;
    const pdfs = files.filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    if (pdfs.length < files.length) push({ kind: "error", text: "Only PDF files can be checked." });
    const limitMb = health?.max_upload_mb ?? 5;
    const tooBig = pdfs.filter((f) => f.size > limitMb * 1024 * 1024);
    if (tooBig.length) push({ kind: "error", text: `${tooBig.map((f) => f.name).join(", ")} is over ${limitMb} MB.` });
    const ok = pdfs.filter((f) => !tooBig.includes(f));
    if (!ok.length) return;
    setBusy(true);
    let read = 0;
    // One unreadable file shouldn't stop the others.
    for (const f of ok) {
      try {
        const rec = await api.upload(f);
        setDocs((prev) => [rec, ...prev.filter((d) => d.document_id !== rec.document_id)]);
        read++;
      } catch (e) {
        push({ kind: "error", text: `${f.name}: ${e instanceof Error ? e.message : String(e)}` });
      }
    }
    if (read) {
      push({ kind: "note", text: `Read ${read} document${read > 1 ? "s" : ""}. Ask the agent to check them, or see the summary on the right.` });
    }
    setBusy(false);
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

  function newConversation() {
    if (busy) return;
    // The old conversation (and any booking waiting for approval) is left behind on the
    // server, where idle sessions expire. Uploaded documents stay usable.
    setSession(sessionId(true));
    setItems([]);
    setTurns([]);
    setDraft("");
    setShowTrace(false);
    composer.current?.focus();
  }

  // Stable handlers for the memoised children; they always call the latest closure.
  const latest = useRef({ decide, upload, loadSample, send });
  useEffect(() => {
    latest.current = { decide, upload, loadSample, send };
  });
  const onDecide = useCallback((approved: boolean) => void latest.current.decide(approved), []);
  const onUpload = useCallback((files: File[]) => void latest.current.upload(files), []);
  const onSample = useCallback((id: string) => void latest.current.loadSample(id), []);
  const onAsk = useCallback(
    (d: DocumentRecord) => void latest.current.send(`Please check ${d.document_id} (${d.filename}) for problems.`),
    [],
  );

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
                  : health.llm_provider === "replay"
                    ? "Replay of recorded model responses"
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
          {items.length > 0 && (
            <button
              type="button"
              className="btn quiet nav-link"
              onClick={newConversation}
              disabled={busy}
              aria-label="Start a new conversation"
            >
              <span className="label-long">New chat</span>
              <span className="label-short">New</span>
            </button>
          )}
          <Link className="btn quiet nav-link" href="/evals" aria-label="Eval results">
            <span className="label-long">Eval results</span>
            <span className="label-short">Evals</span>
          </Link>
          <button
            type="button"
            className="btn quiet trace-toggle"
            aria-expanded={showTrace}
            aria-label={showTrace ? "Back to chat" : "Trace & documents"}
            onClick={() => setShowTrace((v) => !v)}
          >
            <span className="label-long">{showTrace ? "Back to chat" : "Trace & documents"}</span>
            <span className="label-short">{showTrace ? "Chat" : "Trace"}</span>
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
                {health?.notice && <p className="msg note notice">{health.notice}</p>}
              </div>
            )}
            <Messages items={items} onDecide={onDecide} />
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
              ref={composer}
              rows={1}
              value={draft}
              placeholder="Ask the agent…"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
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
            onUpload={onUpload}
            onSample={onSample}
            onAsk={onAsk}
          />
        </aside>
      </main>
    </div>
  );
}
