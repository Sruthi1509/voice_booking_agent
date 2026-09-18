"use client";

import { useState } from "react";

export const SERVICEABLE_LOCATIONS = [
  "Koramangala",
  "Whitefield",
  "Indiranagar",
  "HSR Layout",
  "BTM Layout",
  "Electronic City",
  "Marathahalli",
  "Jayanagar",
  "JP Nagar",
  "Yelahanka",
  "Hebbal",
  "Malleswaram",
  "Rajajinagar",
  "Banashankari",
  "Bellandur",
  "Sarjapur Road",
  "KR Puram",
  "MG Road",
  "Domlur",
  "Richmond Town",
  "Vijayanagar",
  "Basavanagudi",
  "CV Raman Nagar",
  "Hennur",
];

export default function ServiceableLocations() {
  const [isOpen, setIsOpen] = useState(true);
  const [search, setSearch] = useState("");

  const filtered = SERVICEABLE_LOCATIONS.filter((loc) =>
    loc.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 backdrop-blur-md p-4 mb-6 shadow-xl transition-all duration-300">
      <div className="flex items-center justify-between cursor-pointer select-none" onClick={() => setIsOpen(!isOpen)}>
        <div className="flex items-center gap-2">
          <div className="h-7 w-7 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 font-medium text-xs">
            📍
          </div>
          <div>
            <h2 className="text-sm font-semibold text-neutral-200 flex items-center gap-2">
              Serviceable Service Areas (Bengaluru)
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">
                {SERVICEABLE_LOCATIONS.length} Localities
              </span>
            </h2>
            <p className="text-xs text-neutral-400">
              Pickup and drop locations currently supported by the booking agent
            </p>
          </div>
        </div>
        <button
          type="button"
          className="text-xs text-neutral-400 hover:text-neutral-200 transition-colors px-2 py-1 rounded bg-neutral-800/80 border border-neutral-700/50"
        >
          {isOpen ? "Hide" : "Show"}
        </button>
      </div>

      {isOpen && (
        <div className="mt-4 pt-3 border-t border-neutral-800/80">
          <div className="mb-3 flex items-center justify-between gap-2">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search area (e.g. Koramangala, Whitefield)..."
              className="w-full text-xs bg-neutral-950/80 border border-neutral-800 rounded-lg px-3 py-1.5 text-neutral-200 placeholder-neutral-500 outline-none focus:border-emerald-500/60 transition"
            />
          </div>

          <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto pr-1 custom-scrollbar">
            {filtered.length > 0 ? (
              filtered.map((location) => (
                <span
                  key={location}
                  className="inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-md bg-neutral-800/80 border border-neutral-700/60 text-neutral-200 hover:border-emerald-500/50 hover:bg-emerald-950/20 hover:text-emerald-300 transition-all cursor-default"
                >
                  <span className="text-[10px] text-emerald-400">●</span>
                  {location}
                </span>
              ))
            ) : (
              <p className="text-xs text-neutral-500 py-1">No matching locality found.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
