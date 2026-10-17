import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { Interpretation, Payer, PriceType, SearchResponse } from "../api/types";
import { InterpretationPanel } from "../components/InterpretationPanel";
import { ResultRow } from "../components/ResultRow";
import { SearchBox } from "../components/SearchBox";
import { Notice, Skeleton, ui } from "../components/ui";
import { dollars, PRICE_TYPE_LABEL } from "../lib/format";
import { interpretationToParams, paramsToInterpretation } from "../lib/searchParams";
import { useApi } from "../lib/useApi";
import styles from "./ResultsPage.module.css";

export function ResultsPage() {
  const [params, setParams] = useSearchParams();
  const question = params.get("q");
  const structured = paramsToInterpretation(params);
  const key = params.toString();

  const result = useApi<SearchResponse>(
    structured ? () => api.run(structured) : question ? () => api.search(question) : null,
    key,
  );
  const payers = useApi(() => api.payers(), "payers");
  const payerList: Payer[] = payers.state === "ready" ? payers.data : [];

  function apply(next: Interpretation) {
    setParams(interpretationToParams(next, question));
  }

  return (
    <div className={`container ${styles.page}`}>
      <div className={styles.searchAgain}>
        <SearchBox initial={question ?? ""} key={question ?? ""} />
      </div>

      {!question && !structured && (
        <Notice tone="teal">Type a question above, for example "knee MRI near Baltimore with Aetna".</Notice>
      )}

      {result.state === "loading" && (question || structured) && <LoadingState />}

      {result.state === "error" && (
        <ErrorState status={result.error.status} message={result.error.message} />
      )}

      {result.state === "ready" && (
        <>
          <InterpretationPanel
            interpretation={result.data.interpretation}
            payers={payerList}
            question={result.data.question ?? question}
            needsConfirmation={result.data.status === "needs_confirmation"}
            onApply={apply}
            key={key}
          />
          {result.data.status === "ok" && (
            <Results data={result.data} payers={payerList} onApply={apply} />
          )}
        </>
      )}
    </div>
  );
}

function Results({ data, payers, onApply }: { data: SearchResponse; payers: Payer[]; onApply: (i: Interpretation) => void }) {
  const i = data.interpretation;
  const primary: PriceType = data.headline?.price_type ?? i.price_types[0];
  const payerName = payers.find((p) => p.id === i.payer)?.name ?? null;
  const [showGaps, setShowGaps] = useState(false);

  const { scale, lowestId, highestId } = useMemo(() => {
    const priced = data.hospitals
      .map((h) => ({ id: h.hospital.id, cents: h.prices[primary]?.typical?.rate_cents ?? null }))
      .filter((p): p is { id: string; cents: number } => p.cents !== null);
    if (!priced.length) return { scale: { min: 0, max: 0 }, lowestId: null, highestId: null };
    const sorted = [...priced].sort((a, b) => a.cents - b.cents);
    return {
      scale: { min: sorted[0].cents, max: sorted[sorted.length - 1].cents },
      lowestId: sorted.length > 1 ? sorted[0].id : null,
      highestId: sorted.length > 1 ? sorted[sorted.length - 1].id : null,
    };
  }, [data, primary]);

  return (
    <div className={styles.layout}>
      <FilterRail interpretation={i} payers={payers} onApply={onApply} />

      <section aria-labelledby="results-title" className={styles.results}>
        <div className={styles.headline}>
          <h1 id="results-title" className={styles.headlineTitle}>
            {data.headline && data.headline.hospitals_with_price > 1 ? (
              <>
                {PRICE_TYPE_LABEL[primary]}s for {i.code_type} {i.code} differ by{" "}
                <span className="mono">{dollars(data.headline.spread_cents)}</span> between hospitals
              </>
            ) : data.headline ? (
              <>
                One hospital in range publishes a {PRICE_TYPE_LABEL[primary].toLowerCase()} for {i.code_type} {i.code}
              </>
            ) : (
              <>
                No hospital in range publishes a {PRICE_TYPE_LABEL[primary].toLowerCase()} for {i.code_type} {i.code}
              </>
            )}
          </h1>
          <p className={styles.headlineSub}>
            {data.hospitals.length} of {data.hospitals_in_range} hospitals in range publish a price
            {i.near_zip ? ` within ${i.radius_miles} miles of ${i.near_zip}` : ""}. Comparing each hospital's
            typical published line.
          </p>
        </div>

        {data.hospitals.length > 0 ? (
          <ol className={styles.list}>
            {data.hospitals.map((row) => (
              <ResultRow
                key={row.hospital.id}
                row={row}
                primary={primary}
                scale={scale}
                rank={
                  row.hospital.id === lowestId
                    ? "lowest"
                    : row.hospital.id === highestId
                      ? "highest"
                      : lowestId
                        ? "middle"
                        : "single"
                }
                payerName={payerName}
                payerId={i.payer}
              />
            ))}
          </ol>
        ) : (
          <p className={styles.empty}>No prices to show for these filters. Try a wider distance or another price type.</p>
        )}

        {data.gaps.length > 0 && (
          <div className={styles.gaps}>
            <div className={styles.gapsTop}>
              <p>
                <strong>
                  {data.gaps.length} hospital{data.gaps.length === 1 ? "" : "s"} in range
                </strong>{" "}
                {data.gaps.length === 1 ? "has" : "have"} no{" "}
                {i.payer && payerName ? `${payerName} rate` : "published price"} for this code.{" "}
                <Link to="/status#gaps">Why prices go missing</Link>
              </p>
              <button
                type="button"
                className={styles.gapsToggle}
                aria-expanded={showGaps}
                aria-controls="gap-list"
                onClick={() => setShowGaps(!showGaps)}
              >
                {showGaps ? "Hide" : "Show"} which
              </button>
            </div>
            {showGaps && (
              <ul id="gap-list" className={styles.gapList}>
                {data.gaps.map((g) => (
                  <li key={g.hospital.id}>
                    <strong>{g.hospital.name}</strong>
                    {g.hospital.distance_miles != null && ` (${g.hospital.distance_miles} mi)`}: {g.detail}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function FilterRail({
  interpretation: i,
  payers,
  onApply,
}: {
  interpretation: Interpretation;
  payers: Payer[];
  onApply: (i: Interpretation) => void;
}) {
  const types: PriceType[] = ["negotiated", "cash", "gross"];
  return (
    <aside className={styles.rail} aria-label="Filters">
      <div className={styles.railGroup}>
        <label className={ui.label} htmlFor="rail-payer">
          Insurance
        </label>
        <select
          id="rail-payer"
          className={ui.field}
          value={i.payer ?? ""}
          onChange={(e) => onApply({ ...i, payer: e.target.value || null })}
        >
          <option value="">Any insurer</option>
          {payers
            .filter((p) => p.id !== "all-payers")
            .map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
        </select>
      </div>

      <div className={styles.railGroup}>
        <label className={ui.label} htmlFor="rail-radius">
          Distance{i.near_zip ? ` from ${i.near_zip}` : ""}
        </label>
        {i.near_zip ? (
          <select
            id="rail-radius"
            className={ui.field}
            value={i.radius_miles}
            onChange={(e) => onApply({ ...i, radius_miles: Number(e.target.value) })}
          >
            {[5, 10, 25, 50, 100].map((r) => (
              <option key={r} value={r}>
                Within {r} miles
              </option>
            ))}
          </select>
        ) : (
          <p className={styles.railNote} id="rail-radius">
            Whole service area. Add a ZIP with "Correct this" to search nearby.
          </p>
        )}
      </div>

      <fieldset className={styles.railGroup}>
        <legend className={ui.label}>Price type</legend>
        {types.map((t) => (
          <label key={t} className={styles.check}>
            <input
              type="checkbox"
              checked={i.price_types.includes(t)}
              disabled={i.price_types.length === 1 && i.price_types.includes(t)}
              onChange={(e) =>
                onApply({
                  ...i,
                  price_types: e.target.checked ? [...i.price_types, t] : i.price_types.filter((x) => x !== t),
                })
              }
            />
            {PRICE_TYPE_LABEL[t]}
          </label>
        ))}
      </fieldset>

      <fieldset className={styles.railGroup}>
        <legend className={ui.label}>Setting</legend>
        {([null, "outpatient", "inpatient"] as const).map((s) => (
          <label key={s ?? "any"} className={styles.check}>
            <input type="radio" name="setting" checked={i.setting === s} onChange={() => onApply({ ...i, setting: s })} />
            {s ? s[0].toUpperCase() + s.slice(1) : "Any"}
          </label>
        ))}
      </fieldset>
    </aside>
  );
}

function LoadingState() {
  return (
    <div className={styles.loading} aria-live="polite" aria-busy="true">
      <p className="visually-hidden">Searching hospital files…</p>
      <Skeleton height={120} />
      <div className={styles.layout}>
        <Skeleton height={260} />
        <div>
          {[0, 1, 2].map((n) => (
            <div key={n} className={styles.loadingRow}>
              <Skeleton height={20} width="45%" />
              <Skeleton height={10} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ErrorState({ status, message }: { status: number; message: string }) {
  if (status === 503) {
    return (
      <Notice tone="amber">
        <p>
          <strong>Plain-language search isn't switched on for this server.</strong> {message}
        </p>
        <p className={styles.errorHint}>
          You can still search by billing code:{" "}
          <Link to="/results?q=73721">73721</Link> (knee MRI), <Link to="/results?q=70450">70450</Link> (head CT),{" "}
          <Link to="/results?q=45378">45378</Link> (colonoscopy), <Link to="/results?q=470">470</Link> (joint
          replacement, inpatient).
        </p>
      </Notice>
    );
  }
  return (
    <Notice tone={status === 0 ? "danger" : "amber"}>
      <p>{message}</p>
    </Notice>
  );
}
