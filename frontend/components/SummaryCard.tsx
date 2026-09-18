"use client";

import { Fragment } from "react";

const LABELS: Record<string, string> = {
  pickup_location: "Pickup",
  drop_location: "Drop",
  date: "Date",
  time: "Time",
  load_description: "What's being moved",
  country_code: "Country code",
  contact_number: "Contact number",
  special_instructions: "Special instructions",
  suggested_vehicle: "Suggested vehicle",
};

export default function SummaryCard({
  fields,
  missingFields,
  isComplete,
}: {
  fields: Record<string, string>;
  missingFields: string[];
  isComplete: boolean;
}) {
  const entries = Object.entries(fields);
  if (entries.length === 0 && missingFields.length === 0) return null;

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4 text-sm">
      <div className="font-medium mb-2 text-neutral-300">
        {isComplete ? "Booking confirmed" : "Booking so far"}
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {entries.map(([key, value]) => (
          <Fragment key={key}>
            <dt className="text-neutral-500">{LABELS[key] || key}</dt>
            <dd className="text-neutral-100">{value}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}
