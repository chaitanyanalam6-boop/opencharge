// Mirrors src/opencharge/api/schemas.py. Money is integer cents; null means "not published".

export type PriceType = "gross" | "cash" | "negotiated" | "min" | "max";
export type CodeType = "CPT" | "HCPCS" | "DRG";
export type Setting = "inpatient" | "outpatient" | "both";

export interface Provenance {
  source_file_id: string;
  source_file_url: string;
  published_date: string | null;
  fetched_at: string;
  row_number: number;
  locator: string;
  parser: string;
  schema_version: string;
  checksum: string;
}

export interface Price {
  price_type: PriceType;
  rate_cents: number | null;
  rate_raw: string | null;
  percentage: string | null;
  methodology: string | null;
  payer_id: string | null;
  payer_name: string | null;
  plan_name: string | null;
  setting: string;
  description: string;
  issues: string[];
  provenance: Provenance;
}

export interface PriceSummary {
  count: number;
  with_rate: number;
  lowest: Price | null;
  typical: Price | null;
  highest: Price | null;
}

export interface Hospital {
  id: string;
  name: string;
  city: string;
  state: string;
  distance_miles: number | null;
}

export interface HospitalPrices {
  hospital: Hospital;
  prices: Partial<Record<PriceType, PriceSummary>>;
  description_variants: number;
  detail_url: string;
}

export interface Gap {
  hospital: Hospital;
  kind: "not_in_file" | "payer_not_listed" | "no_rate_published";
  detail: string;
}

export interface Headline {
  price_type: PriceType;
  basis: string;
  lowest_hospital: string;
  lowest_cents: number;
  highest_hospital: string;
  highest_cents: number;
  spread_cents: number;
  hospitals_with_price: number;
}

export interface Interpretation {
  code_type: CodeType | null;
  code: string | null;
  description_terms: string[];
  payer: string | null;
  setting: Setting | null;
  price_types: PriceType[];
  near_zip: string | null;
  radius_miles: number;
  confidence: number;
  unresolved: string[];
}

export interface SearchResponse {
  status: "ok" | "needs_confirmation";
  question: string | null;
  interpretation: Interpretation;
  message: string | null;
  headline: Headline | null;
  hospitals: HospitalPrices[];
  gaps: Gap[];
  hospitals_in_range: number | null;
  notice: string;
}

export interface PayerRow {
  payer_id: string | null;
  payer_name: string | null;
  plan_name: string | null;
  rate_cents: number | null;
  percentage: string | null;
  methodology: string | null;
  published: boolean;
  provenance: Provenance | null;
}

export interface SourceFile {
  id: string;
  hospital_id: string;
  url: string;
  source_page_url: string | null;
  file_name: string;
  published_date: string | null;
  fetched_at: string;
  age_days: number | null;
  sha256: string;
  byte_size: number;
  format: string;
  parser_name: string;
  schema_version: string | null;
  status: string;
  rows_in: number | null;
  rows_loaded: number | null;
  rows_quarantined: number | null;
}

export interface HospitalPriceDetail {
  hospital: Hospital;
  code_type: CodeType;
  code: string;
  descriptions: string[];
  gross: PriceSummary;
  cash: PriceSummary;
  payers: PayerRow[];
  source_file: SourceFile;
  validation_checks: string[];
  caveats: string[];
  example_line: {
    source_row: Record<string, string> | null;
    raw: Record<string, string | number | null>;
    normalized: Record<string, string | number | string[] | null>;
  };
  notice: string;
}

export interface HospitalStatus {
  id: string;
  name: string;
  format: string;
  in_service_area: boolean;
  status: "Loaded" | "Drift" | "Fetch failed" | "Not fetched" | string;
  last_fetch: { outcome: string; attempted_at: string; http_status: number | null; error: string | null } | null;
  current_file: SourceFile | null;
  shoppable_services_covered: number;
}

export interface StatusResponse {
  kpis: {
    hospitals_tracked: number;
    price_lines_loaded: number;
    rows_quarantined: number;
    quarantined_pct: number;
    shoppable_services_total: number;
    shoppable_services_covered: number;
    last_refresh: string | null;
  };
  hospitals: HospitalStatus[];
  quarantined_by_rule: Record<string, number>;
  needs_attention: {
    hospital_id: string;
    source_file_id: string;
    detected_at: string;
    added_columns: string[];
    removed_columns: string[];
    summary: string;
  }[];
  recent_runs: {
    id: string;
    started_at: string;
    finished_at: string | null;
    status: string;
    rows_in: number;
    rows_loaded: number;
    rows_quarantined: number;
  }[];
  answer_quality: Record<string, number> | null;
  notice: string;
}

export interface Payer {
  id: string;
  name: string;
}
