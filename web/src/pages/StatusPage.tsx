import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";

import { api } from "../api/client";
import type { HospitalStatus, StatusResponse } from "../api/types";
import { CheckIcon, SearchIcon } from "../components/Icons";
import { Notice, Pill, Skeleton, StatusPill, ui } from "../components/ui";
import { compactCount, count, date, monthRange } from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "./StatusPage.module.css";

// Plain-language meaning of each validation rule.
const RULE_TEXT: Record<string, string> = {
  code_present: "The item had no billing code at all (often a supply, or a placeholder like N/A).",
  code_format_valid: "The billing code didn't have the shape its declared type requires.",
  payer_resolved_if_negotiated: "A negotiated rate named an insurer we can't yet match to a known insurer.",
  rate_non_negative: "The price was negative.",
  price_type_known: "The price type wasn't one CMS defines.",
  row_misaligned: "The row had the wrong number of cells, so we couldn't tell which value was which.",
  schema_drift: "The value sits in a column the hospital's previous file didn't have.",
};

const FRESHNESS_LABEL: Record<string, string> = {
  fresh: "recent",
  aging: "aging",
  stale: "over 6 months old",
  unknown: "date unknown",
};

export function StatusPage() {
  const status = useApi(() => api.status(), "status");
  const { hash } = useLocation();

  useEffect(() => {
    if (hash && status.state === "ready") document.querySelector(hash)?.scrollIntoView();
  }, [hash, status.state]);

  return (
    <div className={`container ${styles.page}`}>
      <BackToSearch />
      <header className={styles.header}>
        <h1>How current are these prices?</h1>
        <p>Where our prices come from, how old each hospital's file is, and which hospitals to read with care.</p>
      </header>
      {status.state === "loading" && <Skeleton height={400} />}
      {status.state === "error" && (
        <Notice tone="danger">
          <p>{status.error.message}</p>
        </Notice>
      )}
      {status.state === "ready" && <Status s={status.data} />}
      <BackToSearch bottom />
    </div>
  );
}

function BackToSearch({ bottom = false }: { bottom?: boolean }) {
  return (
    <div className={bottom ? styles.backBottom : styles.backTop}>
      <Link to="/" className={ui.button}>
        <SearchIcon size={18} /> Look up a procedure
      </Link>
    </div>
  );
}

function Status({ s }: { s: StatusResponse }) {
  const f = s.freshness;
  const c = s.coverage;
  const lastRun = s.recent_runs[0];
  const median = Number.isInteger(c.median_per_hospital) ? c.median_per_hospital : c.median_per_hospital.toFixed(1);

  return (
    <>
      {/* ---------------- For patients ---------------- */}
      <section className={styles.hero} aria-labelledby="freshness-title">
        <div>
          <p id="freshness-title" className={styles.heroLine}>
            {f.hospitals} hospitals
            {f.published_from && f.published_to && <> · prices published {monthRange(f.published_from, f.published_to)}</>}
          </p>
          <p className={styles.heroNote}>
            Hospitals must update their price files at least once a year. The older a file, the more likely its
            prices have changed.
          </p>
        </div>
        {f.oldest_file_days !== null && (
          <div className={styles.oldest}>
            <p className={`${styles.oldestNumber} mono ${f.any_stale ? styles.stale : ""}`}>{f.oldest_file_days}</p>
            <p className={styles.oldestLabel}>
              days since the oldest file was published
              {f.oldest_hospital && <span className={styles.oldestWho}>{f.oldest_hospital}</span>}
            </p>
          </div>
        )}
      </section>

      <p className={styles.coverage}>
        Each hospital publishes prices for{" "}
        <strong>
          {c.min_per_hospital} to {c.max_per_hospital}
        </strong>{" "}
        of the {c.services_total} common services CMS asks every hospital to list (median {median}). Across all{" "}
        {f.hospitals} hospitals, {c.covered_by_any} of the {c.services_total} have at least one price.
      </p>

      <section aria-labelledby="hospitals-title" className={styles.section}>
        <h2 id="hospitals-title">Each hospital's file</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Hospital</th>
                <th scope="col">Published</th>
                <th scope="col" className="right">
                  Of CMS's {c.services_total}
                </th>
                <th scope="col">Status</th>
                <th scope="col" className="right">
                  Rows set aside
                </th>
                <th scope="col">Format</th>
              </tr>
            </thead>
            <tbody>
              {s.hospitals.map((h) => (
                <HospitalRow key={h.id} h={h} />
              ))}
            </tbody>
          </table>
        </div>
        {s.out_of_area.length > 0 && (
          <p className={styles.outOfArea}>
            <strong>Out of area:</strong>{" "}
            {s.out_of_area.map((h, i) => (
              <span key={h.id}>
                {i > 0 && "; "}
                {h.name} ({h.format.replace("_", " ")}, {h.rows_in != null ? count(h.rows_in) : "no"} rows). We
                process this file only to test our reader for its format; it never appears in search results.
              </span>
            ))}
          </p>
        )}
      </section>

      <section className={`${ui.card} ${styles.attentionCard}`} aria-labelledby="attention-title">
        <h2 id="attention-title" className={styles.cardTitle}>
          Read with care
        </h2>
        {s.needs_attention.length ? (
          <ul className={styles.attention}>
            {s.needs_attention.map((a) => (
              <li key={`${a.hospital_id}-${a.kind}`}>
                <Pill tone={a.tone === "red" ? "danger" : "amber"}>{a.tone === "red" ? "Needs review" : "Gaps"}</Pill>
                <p>
                  <strong>{a.hospital_name}.</strong> {a.summary}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.allClear}>
            <CheckIcon /> Every hospital's file loaded with fewer than 10% of rows set aside, and no file layout
            changes are waiting for review.
          </p>
        )}
      </section>

      <section id="gaps" aria-labelledby="gaps-title" className={styles.section}>
        <h2 id="gaps-title">Why a price might be missing</h2>
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
            Rows that fail a check are kept out of search rather than shown with a guess. The details are below.
          </Reason>
        </div>
      </section>

      {/* ---------------- For technical readers ---------------- */}
      <div className={styles.divider} role="separator" aria-label="Technical details">
        <span>Technical details</span>
      </div>

      <section aria-labelledby="setaside-title" className={styles.section}>
        <h2 id="setaside-title">Rows we set aside</h2>
        <p className={styles.muted}>
          Setting rows aside is deliberate: when a row is missing a code, names an insurer we can't identify, or
          doesn't line up, we keep it out of search instead of guessing what it meant. Across in-area hospitals that
          is <span className="mono">{count(s.quarantine.rows_quarantined)}</span> of{" "}
          <span className="mono">{count(s.quarantine.rows_in)}</span> rows ({s.quarantine.quarantined_pct}%); the
          original values are kept.
        </p>
        <ul className={styles.rules}>
          {Object.entries(s.quarantine.by_rule).map(([rule, n]) => (
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

      <section aria-labelledby="pipeline-title" className={styles.section}>
        <h2 id="pipeline-title">Pipeline</h2>
        <dl className={styles.facts}>
          <div>
            <dt>Price lines loaded</dt>
            <dd className="mono">{compactCount(s.quarantine.rows_loaded)}</dd>
          </div>
          <div>
            <dt>Last run</dt>
            <dd>{lastRun ? `${date(lastRun.finished_at ?? lastRun.started_at)} · ${lastRun.status}` : "never"}</dd>
          </div>
          <div>
            <dt>Status thresholds</dt>
            <dd>Loaded under 10% set aside · gaps 10–50% · needs review over 50%</dd>
          </div>
        </dl>
      </section>

      {/* Hidden until the evaluation has produced real numbers. */}
      {s.answer_quality && (
        <section className={`${ui.card} ${styles.section}`} aria-labelledby="quality-title">
          <h2 id="quality-title" className={styles.cardTitle}>
            Answer quality
          </h2>
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
        </section>
      )}
    </>
  );
}

function HospitalRow({ h }: { h: HospitalStatus }) {
  return (
    <tr>
      <td>
        <span className={styles.hospital}>{h.name}</span>
        {h.status_reason && <span className={styles.reason}>{h.status_reason}</span>}
      </td>
      <td className={styles.published}>
        {h.published_date ? (
          <>
            {date(h.published_date)} ·{" "}
            <span className={`mono ${h.freshness === "stale" ? styles.stale : ""}`}>{h.age_days} days</span>
            <span className={`${styles.freshness} ${h.freshness === "stale" ? styles.stale : ""}`}>
              {FRESHNESS_LABEL[h.freshness]}
            </span>
          </>
        ) : (
          "—"
        )}
      </td>
      <td className="right mono">{h.shoppable_services_covered}</td>
      <td>
        <StatusPill status={h.status} tone={h.tone} />
      </td>
      <td className="right mono">
        {h.rows_quarantined != null ? count(h.rows_quarantined) : "—"}
        {h.quarantine_pct != null && <span className={styles.pct}> {h.quarantine_pct}%</span>}
      </td>
      <td className={`mono ${styles.format}`}>{h.format.replace("_", " ")}</td>
    </tr>
  );
}

function Reason({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className={styles.reasonCard}>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
