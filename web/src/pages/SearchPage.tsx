import { Link } from "react-router-dom";

import { api } from "../api/client";
import { SearchBox } from "../components/SearchBox";
import { Skeleton } from "../components/ui";
import { compactCount, date } from "../lib/format";
import { useApi } from "../lib/useApi";
import styles from "./SearchPage.module.css";

const EXAMPLES = [
  "knee MRI near Baltimore with Aetna",
  "CT scan of the head without contrast in DC",
  "colonoscopy with CareFirst",
  "70450",
];

const STEPS = [
  {
    title: "Hospitals publish the file",
    body: "Federal rules require every hospital to post its gross charges, cash prices and negotiated rates in a machine-readable file.",
  },
  {
    title: "We read every row",
    body: "Each file is downloaded, checksummed, parsed and validated. Rows that fail a named check are set aside, not guessed at.",
  },
  {
    title: "You see the source",
    body: "The model only turns your question into filters. Every price you see is read from a hospital file, with a link to its row.",
  },
];

export function SearchPage() {
  const status = useApi(() => api.status(), "status");

  return (
    <>
      <section className={styles.hero}>
        <div className="container">
          <p className={styles.eyebrow}>Maryland and Washington, DC</p>
          <h1 className={styles.title}>Real hospital prices, straight from the hospital's own file.</h1>
          <p className={styles.lead}>
            Ask in plain words. We show what hospitals publish for that procedure, and when a hospital doesn't
            publish a price, we say so.
          </p>
          <div className={styles.search}>
            <SearchBox large />
          </div>
          <ul className={styles.examples} aria-label="Example searches">
            {EXAMPLES.map((example) => (
              <li key={example}>
                <Link to={`/results?q=${encodeURIComponent(example)}`} className={`${styles.chip} mono`}>
                  {example}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section aria-label="What's in the data" className="container">
        <dl className={styles.stats}>
          {status.state === "ready" ? (
            <>
              <Stat label="Hospitals" value={String(status.data.kpis.hospitals_tracked)} />
              <Stat label="Price lines" value={compactCount(status.data.kpis.price_lines_loaded)} />
              <Stat
                label="CMS shoppable services covered"
                value={`${status.data.kpis.shoppable_services_covered} of ${status.data.kpis.shoppable_services_total}`}
              />
              <Stat label="Last refresh" value={date(status.data.kpis.last_refresh)} />
            </>
          ) : status.state === "loading" ? (
            [0, 1, 2, 3].map((i) => (
              <div key={i} className={styles.stat}>
                <Skeleton height={14} width="60%" />
                <Skeleton height={28} width="40%" />
              </div>
            ))
          ) : (
            <p className={styles.statsError}>Live data figures are unavailable right now.</p>
          )}
        </dl>
      </section>

      <section className={styles.band} aria-labelledby="how">
        <div className="container">
          <h2 id="how" className={styles.bandTitle}>
            How a number gets here
          </h2>
          <ol className={styles.steps}>
            {STEPS.map((step, i) => (
              <li key={step.title} className={styles.step}>
                <span className={`${styles.stepNumber} mono`} aria-hidden="true">
                  0{i + 1}
                </span>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="container">
        <div className={styles.honesty}>
          <div>
            <h2>When we don't have it, we say so.</h2>
            <p>
              Some hospitals publish a percentage instead of a price, leave insurers out, or change their file
              layout. We show those gaps rather than estimate around them, and the data status page shows
              exactly what was loaded, what was set aside, and why.
            </p>
          </div>
          <Link to="/status" className={styles.honestyLink}>
            See the data status
          </Link>
        </div>
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className={styles.stat}>
      <dt>{label}</dt>
      <dd className="mono">{value}</dd>
    </div>
  );
}
