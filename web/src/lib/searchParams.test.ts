import type { Interpretation } from "../api/types";
import { interpretationToParams, paramsToInterpretation } from "./searchParams";

const knee: Interpretation = {
  code_type: "CPT",
  code: "73721",
  description_terms: ["73721"],
  payer: "aetna",
  setting: "outpatient",
  price_types: ["negotiated", "cash"],
  near_zip: "21201",
  radius_miles: 25,
  confidence: 1,
  unresolved: [],
};

it("a confirmed search survives a round trip through the URL", () => {
  const params = interpretationToParams(knee, "knee MRI near Baltimore with Aetna");
  expect(params.get("q")).toBe("knee MRI near Baltimore with Aetna");
  expect(paramsToInterpretation(params)).toEqual(knee);
});

it("a plain question needs the language model (no interpretation from the URL)", () => {
  expect(paramsToInterpretation(new URLSearchParams("q=knee+MRI"))).toBeNull();
});

it("a bare billing code is searched directly, without the language model", () => {
  const i = paramsToInterpretation(new URLSearchParams("q=70450"));
  expect(i?.code).toBe("70450");
  expect(i?.code_type).toBe("CPT");
  expect(i?.payer).toBeNull();
});

it("out-of-range or unknown values from a hand-edited URL fall back to safe defaults", () => {
  const i = paramsToInterpretation(new URLSearchParams("code=73721&type=CPT&radius=9999&types=estimate&setting=er"));
  expect(i?.radius_miles).toBe(25);
  expect(i?.price_types).toEqual(["negotiated", "cash", "gross"]);
  expect(i?.setting).toBeNull();
});
