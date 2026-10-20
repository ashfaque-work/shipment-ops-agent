"use client";

import type { ShipmentSummary } from "@/lib/api";

export type DocketState = "pending" | "booking" | "booked" | "cancelled";

function addressLines(p: ShipmentSummary["recipient"]): string {
  return [p.street1, p.street2, [p.city, p.postal_code].filter(Boolean).join(" "), p.country]
    .filter(Boolean)
    .join(", ");
}

function money(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount);
  } catch {
    // Not an ISO 4217 code: show it as given rather than crash the page.
    return `${currency} ${amount.toFixed(2)}`;
  }
}

function longDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "long", timeZone: "UTC" });
}

export function Docket({
  summary,
  state,
  trackingNumber,
  onDecide,
}: {
  summary: ShipmentSummary;
  state: DocketState;
  trackingNumber?: string;
  onDecide: (approved: boolean) => void;
}) {
  const r = summary.recipient;
  const pkg = summary.package;
  const dims = pkg.dimensions
    ? `${pkg.dimensions.length_cm} × ${pkg.dimensions.width_cm} × ${pkg.dimensions.height_cm} cm`
    : null;
  const settled = state === "booked" || state === "cancelled";

  return (
    <section className={`docket${settled ? " settled" : ""}`} aria-label="Confirm shipment">
      <header>
        <h3>
          {state === "booked"
            ? "Shipment booked"
            : state === "cancelled"
              ? "Booking cancelled"
              : "Book this shipment?"}
        </h3>
        {summary.reference && <span className="ref data">{summary.reference}</span>}
      </header>
      <dl>
        <dt>Ship to</dt>
        <dd>
          <strong>{r.name}</strong>
          {r.company ? `, ${r.company}` : ""}
          <br />
          {addressLines(r)}
          {r.phone && (
            <>
              <br />
              {r.phone}
            </>
          )}
        </dd>
        <dt>From</dt>
        <dd>
          {summary.shipper.company ?? summary.shipper.name}, {summary.shipper.city}
        </dd>
        <dt>Package</dt>
        <dd>
          {pkg.description}
          <br />
          {pkg.weight_kg} kg{dims ? `, ${dims}` : ""}, declared {money(pkg.declared_value, pkg.currency)}
        </dd>
        <dt>Service</dt>
        <dd>
          {summary.service_name}
          <br />
          {summary.transit_days} days, arrives by {longDate(summary.estimated_delivery)}
        </dd>
        <dt>Price</dt>
        <dd className="price">{money(summary.price, summary.currency)}</dd>
        {trackingNumber && (
          <>
            <dt>Tracking</dt>
            <dd className="data">{trackingNumber}</dd>
          </>
        )}
      </dl>
      {!settled && (
        <p className="warning">Booking buys a carrier label. It can’t be undone from here.</p>
      )}
      <div className="actions">
        <button
          type="button"
          className="btn book"
          disabled={state !== "pending"}
          onClick={() => onDecide(true)}
        >
          {state === "booking" ? "Booking…" : "Book shipment"}
        </button>
        <button
          type="button"
          className="btn quiet"
          disabled={state !== "pending"}
          onClick={() => onDecide(false)}
        >
          Cancel
        </button>
      </div>
      {state === "booked" && (
        <div className="stamp booked" role="status">
          Booked
        </div>
      )}
      {state === "cancelled" && (
        <div className="stamp void" role="status">
          Cancelled
        </div>
      )}
    </section>
  );
}
