import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { api } from "../api/client";
import type { StatusResponse } from "../api/types";
import { CheckIcon } from "../components/Icons";
import { Notice, Pill, Skeleton, StatusPill, ui } from "../components/ui";
import { compactCount, count, date } from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "./StatusPage.module.css";

// Plain-language meaning of each validation rule, for people who aren't data engineers.
const RULE_TEXT: Record<string, string> = {
  code_present: "The item had no billing code at all (often a supply or a placeholder like N/A).",
  code_format_valid: "The billing code didn't have the shape its declared type requires.",
  payer_resolved_if_negotiated: "A negotiated rate named an insurer we can't yet match to a known insurer.",
  rate_non_negative: "The price was negative.",
  price_type_known: "The price type wasn't one CMS defines.",
  row_misaligned: "The row had the wrong number of cells, so we couldn't tell which value was which.",
  schema_drift: "The value sits in a column the hospital's previous file didn't have.",
};

export function StatusPage() {
  const status = useApi(() => api.status(), "status");
  const { hash } = useLocation();

  useEffect(() => {
    if (hash && status.state === "ready") document.querySelector(hash)?.scrollIntoView();
  }, [hash, status.state]);

  return (
    <div className={`container ${styles.page}`}>
      <header className={styles.header}>
        <h1>Data pipeline status</h1>
        <p>
          Public on purpose: what we loaded from each hospital, what we set aside and why, and what needs a person
          to look at it.
        </p>
      </header>
      {status.state === "loading" && <Skeleton height={400} />}
      {status.state === "error" && (
        <Notice tone="danger">
          <p>{status.error.message}</p>
        </Notice>
      )}
      {status.state === "ready" && <Status s={status.data} />}
    </div>
  );
}

function Status({ s }: { s: StatusResponse }) {
  const k = s.kpis;
  const lastRun = s.recent_runs[0];
  return (
    <>
      <dl className={styles.kpis}>
        <Kpi label="Hospitals tracked" value={String(k.hospitals_tracked)} note="in Maryland and DC" />
        <Kpi label="Price lines loaded" value={compactCount(k.price_lines_loaded)} note={count(k.price_lines_loaded)} />
        <Kpi
          label="Rows quarantined"
          value={compactCount(k.rows_quarantined)}
          note={`${k.quarantined_pct}% of intake`}
        />
        <Kpi
          label="CMS shoppable services"
          value={`${k.shoppable_services_covered}/${k.shoppable_services_total}`}
          note="priced by at least one hospital"
        />
      </dl>

      <section aria-labelledby="runs-title" className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 id="runs-title">Hospitals and their latest files</h2>
          {lastRun && (
            <p className={styles.muted}>
              Last run {date(lastRun.finished_at ?? lastRun.started_at)} · {lastRun.status}
            </p>
          )}
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Hospital</th>
                <th scope="col">Format</th>
                <th scope="col">Published</th>
                <th scope="col" className="right">
                  Rows in
                </th>
                <th scope="col" className="right">
                  Quarantined
                </th>
                <th scope="col" className="right">
                  Of CMS's 70
                </th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {s.hospitals.map((h) => {
                const f = h.current_file;
                const pct = f?.rows_in ? ((f.rows_quarantined ?? 0) / f.rows_in) * 100 : 0;
                return (
                  <tr key={h.id}>
                    <td>
                      <span className={styles.hospital}>{h.name}</span>
                      {!h.in_service_area && (
                        <span className={styles.outOfArea}>
                          Out of area: parsed to test the JSON format, never shown in search
                        </span>
                      )}
                      {h.last_fetch?.error && <span className={styles.fetchError}>{h.last_fetch.error}</span>}
                    </td>
                    <td className="mono">{h.format.replace("_", " ")}</td>
                    <td>{f ? date(f.published_date) : "—"}</td>
                    <td className="right mono">{f?.rows_in != null ? count(f.rows_in) : "—"}</td>
                    <td className="right mono">
                      {f?.rows_quarantined != null ? count(f.rows_quarantined) : "—"}
                      {f?.rows_in ? <span className={styles.pct}> {pct.toFixed(1)}%</span> : null}
                    </td>
                    <td className="right mono">{h.shoppable_services_covered}</td>
                    <td>
                      <StatusPill status={h.status} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <div className={styles.cards}>
        <section className={ui.card} aria-labelledby="quality-title">
          <h2 id="quality-title" className={styles.cardTitle}>
            Answer quality
          </h2>
          {s.answer_quality ? (
            <ul className={styles.bars}>
              {Object.entries(s.answer_quality).map(([name, value]) => (
                <li key={name}>
                  <div className={styles.barLabel}>
                    <span>{name.replace(/_/g, " ")}</span>
                    <span className="mono">{Math.round(value * 100)}%</span>
                  </div>
                  <div className={styles.barTrack} role="img" aria-label={`${name}: ${Math.round(value * 100)}%`}>
                    <div className={styles.barFill} style={{ width: `${value * 100}%` }} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>
              No evaluation has been run yet. Once the 50-question test set runs, its code, payer and refusal
              accuracy appear here.
            </p>
          )}
        </section>

        <section className={ui.card} aria-labelledby="attention-title">
          <h2 id="attention-title" className={styles.cardTitle}>
            Needs attention
          </h2>
          {s.needs_attention.length ? (
            <ul className={styles.attention}>
              {s.needs_attention.map((d) => (
                <li key={d.source_file_id}>
                  <Pill tone="amber">Drift</Pill>
                  <p>
                    <strong>{s.hospitals.find((h) => h.id === d.hospital_id)?.name ?? d.hospital_id}</strong>:{" "}
                    {d.summary}
                  </p>
                  <p className={styles.muted}>Detected {date(d.detected_at)}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.allClear}>
              <CheckIcon /> Nothing needs attention: no unreviewed file layout changes.
            </p>
          )}
        </section>
      </div>

      <section aria-labelledby="quarantine-title" className={styles.section}>
        <h2 id="quarantine-title">Why rows were set aside</h2>
        <p className={styles.muted}>
          Rows that fail a check are kept, with the original values, in a quarantine table. Nothing is guessed or
          filled in.
        </p>
        <ul className={styles.rules}>
          {Object.entries(s.quarantined_by_rule).map(([rule, n]) => (
            <li key={rule}>
              <span className="mono">{count(n)}</span>
              <div>
                <span className={`mono ${styles.ruleName}`}>{rule}</span>
                <p>{RULE_TEXT[rule] ?? ""}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section id="gaps" aria-labelledby="gaps-title" className={styles.section}>
        <h2 id="gaps-title">Why prices go missing</h2>
        <div className={styles.reasons}>
          <Reason title="Not in the file">
            The hospital's file doesn't list that billing code. It may still offer the service; it just hasn't
            published a price for it under that code.
          </Reason>
          <Reason title="No rate for your insurer">
            The hospital lists the code, but not for that insurer. Some hospitals, like Mercy Medical Center,
            publish only gross and cash prices.
          </Reason>
          <Reason title="A percentage, not a price">
            Some rates are published as a percentage of charges ("92.3% of billed charges"). We show the percentage;
            we never turn it into a dollar figure.
          </Reason>
          <Reason title="Set aside by a check">
            Rows that fail a validation rule above are quarantined rather than shown with a guess.
          </Reason>
        </div>
      </section>
    </>
  );
}

function Kpi({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className={styles.kpi}>
      <dt>{label}</dt>
      <dd>
        <span className="mono">{value}</span>
        <span className={styles.kpiNote}>{note}</span>
      </dd>
    </div>
  );
}

function Reason({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className={styles.reason}>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
