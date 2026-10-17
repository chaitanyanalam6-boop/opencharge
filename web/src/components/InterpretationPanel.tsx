import { useState, type FormEvent } from "react";

import type { Interpretation, Payer, PriceType } from "../api/types";
import { codeTypeOf, PRICE_TYPE_LABEL } from "../lib/format";
import styles from "./InterpretationPanel.module.css";
import { ui } from "./ui";

type Props = {
  interpretation: Interpretation;
  payers: Payer[];
  question: string | null;
  needsConfirmation: boolean;
  onApply: (next: Interpretation) => void;
};

/**
 * The most important element on the results page (brief §6): it shows exactly which filters
 * the model chose, so a person can check them, and correct them, before trusting any number.
 */
export function InterpretationPanel({ interpretation: i, payers, question, needsConfirmation, onApply }: Props) {
  const [editing, setEditing] = useState(needsConfirmation && !i.code);
  const payerName = payers.find((p) => p.id === i.payer)?.name ?? i.payer;

  const chips: [string, string][] = [
    ["code", i.code ? `${i.code_type} ${i.code}` : "not chosen"],
    ["payer", payerName ?? "any"],
    ["radius", i.near_zip ? `${i.radius_miles}mi of ${i.near_zip}` : "whole service area"],
    ["setting", i.setting ?? "any"],
    ["price types", i.price_types.join(", ")],
  ];

  return (
    <section className={`${styles.panel} ${needsConfirmation ? styles.confirm : ""}`} aria-labelledby="interp-title">
      <div className={styles.top}>
        <div>
          <h2 id="interp-title" className={styles.title}>
            {needsConfirmation ? "Is this what you meant?" : "How we read your question"}
          </h2>
          {question && <p className={styles.question}>"{question}"</p>}
        </div>
        <div className={styles.actions}>
          {needsConfirmation && i.code && (
            <button type="button" className={ui.button} onClick={() => onApply(i)}>
              Search with this
            </button>
          )}
          <button
            type="button"
            className={`${ui.button} ${ui.secondary}`}
            aria-expanded={editing}
            aria-controls="correct-form"
            onClick={() => setEditing(!editing)}
          >
            {editing ? "Cancel" : "Correct this"}
          </button>
        </div>
      </div>

      <ul className={styles.chips} aria-label="Filters the model chose">
        {chips.map(([key, value]) => (
          <li key={key} className={`${styles.chip} mono`}>
            <span className={styles.key}>{key}</span> = {value}
          </li>
        ))}
      </ul>

      {i.unresolved.length > 0 && (
        <ul className={styles.unresolved}>
          {i.unresolved.map((u) => (
            <li key={u}>Not sure about: {u}</li>
          ))}
        </ul>
      )}

      <p className={styles.footnote}>
        The model only chose these filters; every price below is read from a hospital file.
        {needsConfirmation && ` Confidence ${Math.round(i.confidence * 100)}%, so we're asking before searching.`}
      </p>

      {editing && (
        <CorrectForm
          interpretation={i}
          payers={payers}
          onSubmit={(next) => {
            setEditing(false);
            onApply(next);
          }}
        />
      )}
    </section>
  );
}

function CorrectForm({
  interpretation: i,
  payers,
  onSubmit,
}: {
  interpretation: Interpretation;
  payers: Payer[];
  onSubmit: (next: Interpretation) => void;
}) {
  const [code, setCode] = useState(i.code ?? "");
  const [payer, setPayer] = useState(i.payer ?? "");
  const [zip, setZip] = useState(i.near_zip ?? "");
  const [radius, setRadius] = useState(i.radius_miles);
  const [types, setTypes] = useState<PriceType[]>(i.price_types);
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    const codeType = codeTypeOf(code);
    if (!codeType) return setError("Enter a CPT (73721), HCPCS (J1100) or MS-DRG (470) code.");
    if (zip && !/^\d{5}$/.test(zip)) return setError("ZIP codes have five digits.");
    if (!types.length) return setError("Choose at least one price type.");
    onSubmit({
      ...i,
      code: code.trim().toUpperCase(),
      code_type: codeType,
      payer: payer || null,
      near_zip: zip || null,
      radius_miles: radius,
      price_types: types,
      confidence: 1,
      unresolved: [],
    });
  }

  return (
    <form id="correct-form" className={styles.form} onSubmit={submit} noValidate>
      <div>
        <label className={ui.label} htmlFor="fix-code">
          Billing code
        </label>
        <input id="fix-code" className={`${ui.field} mono`} value={code} onChange={(e) => setCode(e.target.value)} />
      </div>
      <div>
        <label className={ui.label} htmlFor="fix-payer">
          Insurance
        </label>
        <select id="fix-payer" className={ui.field} value={payer} onChange={(e) => setPayer(e.target.value)}>
          <option value="">Any / none</option>
          {payers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className={ui.label} htmlFor="fix-zip">
          Near ZIP
        </label>
        <input
          id="fix-zip"
          className={`${ui.field} mono`}
          inputMode="numeric"
          value={zip}
          onChange={(e) => setZip(e.target.value)}
        />
      </div>
      <div>
        <label className={ui.label} htmlFor="fix-radius">
          Within (miles)
        </label>
        <select id="fix-radius" className={ui.field} value={radius} onChange={(e) => setRadius(Number(e.target.value))}>
          {[5, 10, 25, 50, 100].map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>
      <fieldset className={styles.types}>
        <legend className={ui.label}>Price types</legend>
        {(["negotiated", "cash", "gross"] as PriceType[]).map((t) => (
          <label key={t} className={styles.check}>
            <input
              type="checkbox"
              checked={types.includes(t)}
              onChange={(e) => setTypes(e.target.checked ? [...types, t] : types.filter((x) => x !== t))}
            />
            {PRICE_TYPE_LABEL[t]}
          </label>
        ))}
      </fieldset>
      <div className={styles.formActions}>
        {error && (
          <p role="alert" className={styles.error}>
            {error}
          </p>
        )}
        <button type="submit" className={ui.button}>
          Search with these filters
        </button>
      </div>
    </form>
  );
}
