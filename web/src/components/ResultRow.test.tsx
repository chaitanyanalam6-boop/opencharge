import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { HospitalPrices, Price } from "../api/types";
import { ResultRow } from "./ResultRow";

function price(cents: number | null, row: number, description = "MRI LWR EXT JOINT WO CON"): Price {
  return {
    price_type: "negotiated",
    rate_cents: cents,
    rate_raw: cents === null ? null : String(cents / 100),
    percentage: null,
    methodology: "fee schedule",
    payer_id: "aetna",
    payer_name: "Aetna",
    plan_name: "PPO",
    setting: "outpatient",
    description,
    issues: [],
    provenance: {
      source_file_id: "f1",
      source_file_url: "https://h.example/files/123_test_standardcharges.csv",
      published_date: "2026-04-01",
      fetched_at: "2026-10-16T00:00:00Z",
      row_number: row,
      locator: "standard_charge|negotiated_dollar",
      parser: "cms_csv_tall_v3",
      schema_version: "3.0.0",
      checksum: "a".repeat(64),
    },
  };
}

function row(lines: Price[]): HospitalPrices {
  return {
    hospital: { id: "h1", name: "Test Hospital", city: "Baltimore", state: "MD", distance_miles: 2.5 },
    prices: { negotiated: { count: lines.length, with_rate: lines.length, lowest: lines[0], typical: lines[0], highest: lines[lines.length - 1] } },
    description_variants: 1,
    detail_url: "/api/hospital/h1/price/73721?code_type=CPT",
  };
}

function renderRow(r: HospitalPrices, rank: "lowest" | "highest" | "middle" | "single") {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <ul>
        <ResultRow row={r} primary="negotiated" scale={{ min: 50000, max: 150000 }} rank={rank} payerName="Aetna" payerId="aetna" />
      </ul>
    </MemoryRouter>,
  );
}

it("shows the price, its source file and row, and links to the provenance page", () => {
  renderRow(row([price(85000, 4012)]), "single");
  expect(screen.getByText("$850.00")).toBeInTheDocument();
  const source = screen.getByRole("link", { name: /123_test_standardcharges\.csv · row 4,012/ });
  expect(source).toHaveAttribute("href", "/hospital/h1/price/73721?code_type=CPT&payer=aetna");
});

it("labels the lowest and highest with text, not colour alone", () => {
  renderRow(row([price(50000, 4)]), "lowest");
  expect(screen.getByText("Lowest")).toBeInTheDocument();
});

it("says 'not published' rather than $0 when a hospital has no rate", () => {
  renderRow(row([price(null, 4)]), "single");
  expect(screen.getByText("not published")).toBeInTheDocument();
  expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
});
