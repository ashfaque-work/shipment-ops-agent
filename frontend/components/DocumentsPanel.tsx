"use client";

import { memo, useRef, useState } from "react";
import type { DocumentRecord, Sample } from "@/lib/api";

const TYPE_LABEL: Record<string, string> = {
  commercial_invoice: "Commercial invoice",
  bill_of_lading: "Bill of lading",
};

export const DocumentsPanel = memo(function DocumentsPanel({
  docs,
  samples,
  busy,
  maxMb,
  onUpload,
  onSample,
  onAsk,
}: {
  docs: DocumentRecord[];
  samples: Sample[];
  busy: boolean;
  maxMb: number;
  onUpload: (files: File[]) => void;
  onSample: (id: string) => void;
  onAsk: (doc: DocumentRecord) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  return (
    <section className="panel docs" aria-label="Shipping documents">
      <h2>
        Documents <small>PDF, up to {maxMb} MB</small>
      </h2>
      <div
        className={`drop${over ? " over" : ""}`}
        role="button"
        tabIndex={0}
        aria-disabled={busy}
        onClick={() => !busy && input.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !busy) {
            e.preventDefault();
            input.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (!busy) onUpload(Array.from(e.dataTransfer.files));
        }}
      >
        Drop an invoice or bill of lading here, or <strong>choose a file</strong>
        <input
          ref={input}
          type="file"
          accept="application/pdf"
          multiple
          className="visually-hidden"
          onChange={(e) => {
            onUpload(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
      </div>

      {samples.length > 0 && (
        <div className="samples">
          <p>No documents handy? Try a sample:</p>
          {samples.map((s) => (
            <button
              key={s.id}
              type="button"
              className="chip"
              disabled={busy}
              onClick={() => onSample(s.id)}
            >
              {s.title}
            </button>
          ))}
        </div>
      )}

      {docs.length > 0 && (
        <ul className="doc-list">
          {docs.map((d) => {
            const errors = d.issues;
            return (
              <li key={d.document_id} className="doc">
                <div className="top">
                  <span className="fname" title={d.filename}>
                    {d.filename}
                  </span>
                  <span className="data">{d.document_id}</span>
                </div>
                <div className="meta">
                  {d.doc_type ? TYPE_LABEL[d.doc_type] : "Unrecognised document"}
                  {d.scanned ? ", scanned" : ""}
                  {d.method === "rules" ? ", read by rule parser" : d.method.startsWith("llm") ? ", read by Gemini" : ""}
                </div>
                {d.warnings.map((w) => (
                  <p key={w} className="meta">
                    {w}
                  </p>
                ))}
                {errors.length > 0 ? (
                  <ul>
                    {errors.map((i, n) => (
                      <li key={`${n}-${i.field}-${i.code}`} className={i.severity}>
                        {i.message}
                      </li>
                    ))}
                  </ul>
                ) : (
                  d.doc_type && <p className="clean">No problems within this document. Ask the agent to cross-check it against the others.</p>
                )}
                <button type="button" className="ask" disabled={busy} onClick={() => onAsk(d)}>
                  Ask the agent about this document
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
});
