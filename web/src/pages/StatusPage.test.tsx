import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { HospitalStatus, StatusResponse } from "../api/types";
import { StatusPage } from "./StatusPage";

function hospital(over: Partial<HospitalStatus>): HospitalStatus {
  return {
    id: "h",
    name: "Hospital",
    format: "csv_tall",
    in_service_area: true,
    published_date: "2026-01-01",
    age_days: 289,
    freshness: "stale",
    rows_in: 100,
    rows_quarantined: 0,
    quarantine_pct: 0,
    status: "Loaded",
    tone: "green",
    status_reason: null,
    last_fetch: null,
    current_file: null,
    shoppable_services_covered: 30,
    ...over,
  };
}

const STATUS: StatusResponse = {
  freshness: {
    hospitals: 2,
    published_from: "2026-01-01",
    published_to: "2026-09-01",
    oldest_file_days: 289,
    oldest_hospital: "Mercy Medical Center",
    any_stale: true,
  },
  coverage: { services_total: 70, median_per_hospital: 44.5, min_per_hospital: 30, max_per_hospital: 57, covered_by_any: 69 },
  hospitals: [
    hospital({ id: "mercy", name: "Mercy Medical Center" }),
    hospital({
      id: "gbmc",
      name: "Greater Baltimore Medical Center",
      published_date: "2026-07-01",
      age_days: 108,
      freshness: "aging",
      quarantine_pct: 53.2,
      status: "Needs review",
      tone: "red",
      status_reason: "53.2% of its rows were set aside.",
      shoppable_services_covered: 57,
    }),
  ],
  out_of_area: [hospital({ id: "novant", name: "Novant Health Charlotte Orthopedic Hospital", in_service_area: false, format: "json" })],
  needs_attention: [
    {
      hospital_id: "gbmc",
      hospital_name: "Greater Baltimore Medical Center",
      tone: "red",
      kind: "quarantine",
      summary: "We set aside 53.2% of Greater Baltimore Medical Center's rows, mostly items listed without a billing code.",
      detected_at: null,
    },
  ],
  quarantine: { rows_in: 200, rows_loaded: 147, rows_quarantined: 53, quarantined_pct: 26.5, by_rule: { code_present: 53 } },
  recent_runs: [],
  last_refresh: "2026-10-17T00:00:00Z",
  answer_quality: null,
  notice: "",
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(STATUS), { status: 200 })));
});

async function renderPage() {
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <StatusPage />
    </MemoryRouter>,
  );
  return screen.findByText(/2 hospitals · prices published between Jan and Sep 2026/);
}

it("leads with freshness, and marks the oldest file amber when a file is stale", async () => {
  await renderPage();
  const oldest = screen.getByText("289", { selector: "p" });
  expect(oldest.className).toMatch(/stale/);
  expect(screen.getByText(/days since the oldest file was published/)).toBeInTheDocument();
});

it("states per-hospital coverage before the any-hospital figure", async () => {
  await renderPage();
  const line = screen.getByText(/Each hospital publishes prices for/);
  expect(line.textContent).toMatch(/30 to 57.*median 44\.5.*69 of the 70/s);
});

it("keeps out-of-area hospitals out of the main table", async () => {
  await renderPage();
  const table = screen.getByRole("table");
  expect(within(table).queryByText(/Novant/)).not.toBeInTheDocument();
  expect(screen.getByText(/Out of area:/).parentElement?.textContent).toMatch(/Novant/);
});

it("lists a hospital with most rows set aside under 'Read with care', never 'nothing needs attention'", async () => {
  await renderPage();
  expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
  expect(screen.getByText(/mostly items listed without a billing code/)).toBeInTheDocument();
  expect(screen.queryByText(/fewer than 10% of rows set aside/)).not.toBeInTheDocument();
});

it("hides the answer quality card until there are real numbers", async () => {
  await renderPage();
  expect(screen.queryByText("Answer quality")).not.toBeInTheDocument();
});

it("links back to search at the top and the bottom", async () => {
  await renderPage();
  expect(screen.getAllByRole("link", { name: /Look up a procedure/ })).toHaveLength(2);
});

it("puts 'Published' right after the hospital name", async () => {
  await renderPage();
  const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(headers.slice(0, 2)).toEqual(["Hospital", "Published"]);
  expect(headers.at(-1)).toBe("Format");
});
