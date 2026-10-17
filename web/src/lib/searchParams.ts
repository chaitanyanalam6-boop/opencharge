import type { Interpretation, PriceType, Setting } from "../api/types";
import { codeTypeOf } from "./format";

const ALL_TYPES: PriceType[] = ["negotiated", "cash", "gross", "min", "max"];

/** A confirmed search lives in the URL, so results are shareable and the back button works. */
export function interpretationToParams(i: Interpretation, question?: string | null): URLSearchParams {
  const p = new URLSearchParams();
  if (question) p.set("q", question);
  if (i.code && i.code_type) {
    p.set("code", i.code);
    p.set("type", i.code_type);
  }
  if (i.payer) p.set("payer", i.payer);
  if (i.near_zip) p.set("zip", i.near_zip);
  p.set("radius", String(i.radius_miles));
  if (i.setting) p.set("setting", i.setting);
  p.set("types", i.price_types.join(","));
  return p;
}

/** Rebuild a confirmed interpretation from the URL, or null if the URL holds only a question. */
export function paramsToInterpretation(p: URLSearchParams): Interpretation | null {
  const q = p.get("q") ?? "";
  let code = p.get("code");
  let codeType = p.get("type");
  if (!code) {
    // A bare billing code typed into the search box needs no language model.
    const inferred = codeTypeOf(q);
    if (!inferred) return null;
    code = q.trim().toUpperCase();
    codeType = inferred;
  }
  const types = (p.get("types") ?? "negotiated,cash,gross")
    .split(",")
    .filter((t): t is PriceType => (ALL_TYPES as string[]).includes(t));
  const setting = p.get("setting");
  const radius = Number(p.get("radius") ?? 25);
  return {
    code_type: codeType as Interpretation["code_type"],
    code,
    description_terms: [code.toLowerCase()],
    payer: p.get("payer"),
    setting: setting === "inpatient" || setting === "outpatient" || setting === "both" ? (setting as Setting) : null,
    price_types: types.length ? types : ["negotiated", "cash", "gross"],
    near_zip: p.get("zip"),
    radius_miles: Number.isFinite(radius) && radius >= 1 && radius <= 100 ? radius : 25,
    confidence: 1,
    unresolved: [],
  };
}
