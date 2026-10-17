import { Link, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { HospitalPriceDetail, PayerRow, PriceSummary } from "../api/types";
import { CheckIcon, ExternalIcon, FileIcon } from "../components/Icons";
import { Notice, Skeleton, ui } from "../components/ui";
import { count, date, dollars, shortHash } from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "./PricePage.module.css";

export function PricePage() {
  const { hospitalId = "", code = "" } = useParams();
  const [params] = useSearchParams();
  const codeType = params.get("code_type") ?? "CPT";
  const payerId = params.get("payer");
  const detail = useApi(() => api.hospitalPrice(hospitalId, code, codeType), `${hospitalId}/${code}/${codeType}`);

  return (
    <div className={`container ${styles.page}`}>
      <nav aria-label="Breadcrumb" className={styles.crumbs}>
        <Link to={`/results?q=${encodeURIComponent(code)}`}>All hospitals for {code}</Link>
      </nav>
      {detail.state === "loading" && <Skeleton height={320} />}
      {detail.state === "error" && (
        <Notice tone="amber">
          <p>{detail.error.message}</p>
        </Notice>
      )}
      {detail.state === "ready" && <Detail d={detail.data} payerId={payerId} />}
    </div>
  );
}

function Detail({ d, payerId }: { d: HospitalPriceDetail; payerId: string | null }) {
  const yourRow = payerId
    ? (d.payers.find((p) => p.payer_id === payerId && p.published) ??
      d.payers.find((p) => p.payer_id === "all-payers" && p.published) ??
      null)
    : null;
  const yourName = payerId ? (d.payers.find((p) => p.payer_id === payerId)?.payer_name ?? payerId) : null;
  const published = d.payers.filter((p) => p.published);
  const unpublished = d.payers.filter((p) => !p.published);

  return (
    <>
      <header className={styles.header}>
        <p className={`${styles.code} mono`}>
          {d.code_type} {d.code}
        </p>
        <h1 className={styles.title}>{d.hospital.name}</h1>
        <p className={styles.sub}>
          {d.hospital.city}, {d.hospital.state} · published as{" "}
          {d.descriptions.length === 1 ? (
            <span className="mono">"{d.descriptions[0]}"</span>
          ) : (
            `${d.descriptions.length} variants`
          )}
        </p>
      </header>

      <section aria-label="Prices" className={styles.tiles}>
        <div className={`${styles.tile} ${styles.tileYours}`}>
          <p className={styles.tileLabel}>{yourName ? `Your negotiated rate (${yourName})` : "Negotiated rates"}</p>
          {yourName ? (
            <>
              <p className={`${styles.tileValue} mono ${yourRow ? "" : styles.notPublished}`}>
                {yourRow ? dollars(yourRow.rate_cents) : "not published"}
              </p>
              <p className={styles.tileNote}>
                {yourRow
                  ? yourRow.payer_id === "all-payers"
                    ? "Published once for every insurer"
                    : `${yourRow.plan_name ?? ""} · lowest of this plan's lines`
                  : "This hospital publishes no rate for this insurer and code"}
              </p>
            </>
          ) : (
            <>
              <p className={`${styles.tileValue} mono`}>{published.length}</p>
              <p className={styles.tileNote}>plans with a published rate, listed below</p>
            </>
          )}
        </div>
        <SummaryTile label="Cash price" summary={d.cash} />
        <SummaryTile label="Gross charge" summary={d.gross} muted />
      </section>

      <div className={styles.columns}>
        <div className={styles.left}>
          <section aria-labelledby="payers-title">
            <h2 id="payers-title" className={styles.h2}>
              Every insurer in this hospital's file
            </h2>
            <p className={styles.help}>
              {published.length} plan{published.length === 1 ? "" : "s"} with a rate for this code;{" "}
              {unpublished.length} listed elsewhere in the file with none for this code.
            </p>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Insurer</th>
                    <th scope="col">Plan</th>
                    <th scope="col" className="right">
                      Rate
                    </th>
                    <th scope="col">Method</th>
                    <th scope="col" className="right">
                      Source row
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {[...published, ...unpublished].map((p) => (
                    <PayerTableRow key={`${p.payer_id}-${p.plan_name}`} p={p} highlight={p === yourRow} />
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="raw-title" className={styles.rawSection}>
            <h2 id="raw-title" className={styles.h2}>
              The raw row, and what we stored
            </h2>
            <p className={styles.help}>
              Row {count(Number(d.example_line.raw.row_number))} of {d.source_file.file_name}, column{" "}
              <span className="mono">{String(d.example_line.raw.column)}</span>.
            </p>
            <div className={styles.rawGrid}>
              <figure className={styles.rawBlock}>
                <figcaption>As the hospital wrote it</figcaption>
                <pre className="mono">
                  {formatBlock(d.example_line.source_row ?? (d.example_line.raw as Record<string, unknown>))}
                </pre>
              </figure>
              <figure className={styles.rawBlock}>
                <figcaption>Normalized record</figcaption>
                <pre className="mono">{formatBlock(d.example_line.normalized as Record<string, unknown>)}</pre>
              </figure>
            </div>
          </section>
        </div>

        <aside className={styles.right}>
          <section className={`${ui.card} ${styles.provenance}`} aria-labelledby="prov-title">
            <h2 id="prov-title" className={styles.h3}>
              <FileIcon /> Provenance
            </h2>
            <dl>
              <Fact label="Source file">
                <span className={`mono ${styles.wrap}`}>{d.source_file.file_name}</span>
              </Fact>
              <Fact label="Published by the hospital">{date(d.source_file.published_date)}</Fact>
              <Fact label="Fetched">{date(d.source_file.fetched_at)}</Fact>
              <Fact label="Row number">
                <span className="mono">{count(Number(d.example_line.raw.row_number))}</span>
              </Fact>
              <Fact label="sha256">
                <span className="mono" title={d.source_file.sha256}>
                  {shortHash(d.source_file.sha256)}
                </span>
              </Fact>
              <Fact label="Parser · schema">
                <span className="mono">
                  {d.source_file.parser_name} · v{d.source_file.schema_version}
                </span>
              </Fact>
            </dl>
            <p className={styles.checksTitle}>Validation checks passed</p>
            <ul className={styles.checks}>
              {d.validation_checks.map((c) => (
                <li key={c} className="mono">
                  <CheckIcon /> {c}
                </li>
              ))}
            </ul>
            <a className={ui.button} href={d.source_file.url} target="_blank" rel="noopener noreferrer">
              Open the original file <ExternalIcon />
            </a>
            {d.source_file.source_page_url && (
              <a className={styles.pageLink} href={d.source_file.source_page_url} target="_blank" rel="noopener noreferrer">
                Hospital's price transparency page
              </a>
            )}
            <p className={styles.fileSize}>
              {(d.source_file.byte_size / 1_000_000).toFixed(1)} MB; large files may not open in a browser.
            </p>
          </section>

          <Notice tone="amber">
            <p className={styles.caveatTitle}>Before you rely on this</p>
            <ul className={styles.caveats}>
              {d.caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </Notice>
        </aside>
      </div>
    </>
  );
}

function SummaryTile({ label, summary, muted = false }: { label: string; summary: PriceSummary; muted?: boolean }) {
  const typical = summary.typical;
  return (
    <div className={`${styles.tile} ${muted ? styles.tileMuted : ""}`}>
      <p className={styles.tileLabel}>{label}</p>
      <p className={`${styles.tileValue} mono ${typical?.rate_cents == null ? styles.notPublished : ""}`}>
        {dollars(typical?.rate_cents)}
      </p>
      <p className={styles.tileNote}>
        {summary.count > 1
          ? `Typical of ${summary.count} lines · ${dollars(summary.lowest?.rate_cents)} to ${dollars(summary.highest?.rate_cents)}`
          : summary.count === 1
            ? `Row ${count(typical?.provenance.row_number ?? 0)}`
            : "Not in this hospital's file"}
      </p>
    </div>
  );
}

function PayerTableRow({ p, highlight }: { p: PayerRow; highlight: boolean }) {
  return (
    <tr className={highlight ? styles.highlight : undefined}>
      <td>{p.payer_name ?? p.payer_id ?? "Unknown"}</td>
      <td>{p.plan_name}</td>
      <td className={`right mono ${p.published && p.rate_cents != null ? styles.rate : styles.notPublished}`}>
        {p.published ? (p.rate_cents != null ? dollars(p.rate_cents) : p.percentage ? `${p.percentage}% of charges` : "not published") : "not published"}
      </td>
      <td className={styles.method}>{p.methodology ?? ""}</td>
      <td className={`right mono ${styles.rowRef}`}>{p.provenance ? count(p.provenance.row_number) : "—"}</td>
    </tr>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.fact}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function formatBlock(record: Record<string, unknown>): string {
  const width = Math.max(...Object.keys(record).map((k) => k.length));
  return Object.entries(record)
    .map(([k, v]) => `${k.padEnd(width)}  ${Array.isArray(v) ? `[${v.join(", ")}]` : v === null ? "null" : String(v)}`)
    .join("\n");
}
