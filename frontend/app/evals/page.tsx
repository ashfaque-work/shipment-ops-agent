import type { Metadata } from "next";
import Link from "next/link";
import summary from "@/public/eval-summary.json";

export const metadata: Metadata = {
  title: "Eval results · Shipment Ops Agent",
  description: "How the agent scores on tool use, multi-turn scenarios, safety and documents.",
};

type Row = {
  model: string;
  detail: string;
  hard_set: boolean;
  [key: string]: string | boolean | null;
};

export default function EvalsPage() {
  const rows = summary.rows as Row[];
  const main = rows.filter((r) => !r.hard_set);
  const hard = rows.filter((r) => r.hard_set);

  const table = (title: string, body: Row[]) => (
    <section className="evals-block" aria-label={title}>
      <h2>{title}</h2>
      <div className="evals-scroll">
        <table className="evals-table">
          <thead>
            <tr>
              <th scope="col">Model</th>
              {summary.columns.map((c) => (
                <th scope="col" key={c.key}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((r) => (
              <tr key={`${r.model}-${r.detail}`}>
                <th scope="row">
                  {r.model}
                  <span>{r.detail}</span>
                </th>
                {summary.columns.map((c) => (
                  <td key={c.key} className={c.key === "unconfirmed" ? "zero" : undefined}>
                    {(r[c.key] as string | null) ?? "not run"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );

  return (
    <div className="shell evals-shell">
      <header className="masthead">
        <h1 className="wordmark">Shipment Ops</h1>
        <span className="who">Eval results</span>
        <div className="status">
          <Link className="btn quiet nav-link" href="/">
            Back to the agent
          </Link>
        </div>
      </header>
      <main className="evals">
        <p className="evals-lede">
          Every number below is reproduced in CI from recorded model responses. A shipment
          booked without explicit approval would show up as a non-zero in “Unconfirmed bookings”.
        </p>
        {table("Original evaluation", main)}
        {hard.length > 0 && table("Hard set (separate, harder cases)", hard)}
        <ul className="evals-notes">
          {summary.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      </main>
    </div>
  );
}
