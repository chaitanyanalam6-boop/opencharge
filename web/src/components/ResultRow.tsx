import { useId, useState } from "react";
import { Link } from "react-router-dom";

import type { HospitalPrices, Price, PriceType } from "../api/types";
import { dollars, PRICE_TYPE_LABEL } from "../lib/format";
import { ChevronIcon } from "./Icons";
import styles from "./ResultRow.module.css";
import { Pill } from "./ui";

type Props = {
  row: HospitalPrices;
  primary: PriceType;
  scale: { min: number; max: number };
  rank: "lowest" | "highest" | "middle" | "single";
  payerName: string | null;
  payerId: string | null;
};

export function fileName(url: string): string {
  return decodeURIComponent(url.split("/").pop() ?? url);
}

export function ResultRow({ row, primary, scale, rank, payerName, payerId }: Props) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const summary = row.prices[primary];
  const typical = summary?.typical ?? null;
  const shown = typical?.rate_cents != null ? typical : null;
  const detailHref = `${row.detail_url.replace("/api/", "/")}${payerId ? `&payer=${payerId}` : ""}`;
  const position = shown && scale.max > scale.min ? (shown.rate_cents! - scale.min) / (scale.max - scale.min) : 1;
  const others = (Object.keys(row.prices) as PriceType[]).filter((t) => t !== primary);

  return (
    <li className={styles.row}>
      <div className={styles.main}>
        <div className={styles.who}>
          <h3 className={styles.name}>
            <Link to={detailHref}>{row.hospital.name}</Link>
          </h3>
          <p className={styles.meta}>
            {row.hospital.distance_miles != null && <span>{row.hospital.distance_miles} mi</span>}
            <span>
              {row.hospital.city}, {row.hospital.state}
            </span>
            {shown && <span>{shown.setting}</span>}
          </p>
          {shown && <p className={styles.description}>Published as "{shown.description}"</p>}
          {shown && (
            <p className={`${styles.source} mono`}>
              <Link to={detailHref}>
                {fileName(shown.provenance.source_file_url)} · row {shown.provenance.row_number.toLocaleString()}
              </Link>
            </p>
          )}
        </div>

        <div className={styles.priceCol}>
          {shown ? (
            <>
              <div className={styles.priceLine}>
                {rank === "lowest" && <Pill tone="solid">Lowest</Pill>}
                {rank === "highest" && <Pill tone="amber">Highest</Pill>}
                <span className={`${styles.price} mono`}>{dollars(shown.rate_cents)}</span>
              </div>
              <p className={styles.priceCaption}>
                Typical {PRICE_TYPE_LABEL[primary].toLowerCase()}
                {primary === "negotiated" && shown.payer_name ? `, ${shown.payer_name}` : ""}
              </p>
            </>
          ) : (
            <>
              <span className={`${styles.price} ${styles.unpublished} mono`}>not published</span>
              <p className={styles.priceCaption}>
                No {primary === "negotiated" && payerName ? `${payerName} rate` : PRICE_TYPE_LABEL[primary].toLowerCase()}
              </p>
            </>
          )}
        </div>
      </div>

      {shown && (
        <div
          className={styles.track}
          role="img"
          aria-label={`${dollars(shown.rate_cents)}, ${rank === "single" ? "the only price" : `${Math.round(position * 100)}% of the way from the lowest to the highest price shown`}`}
        >
          <div
            className={`${styles.fill} ${rank === "lowest" ? styles.fillLowest : rank === "highest" ? styles.fillHighest : styles.fillMiddle}`}
            style={{ width: `${Math.max(4, position * 100)}%` }}
          />
        </div>
      )}

      <div className={styles.footer}>
        {others.length > 0 && (
          <p className={styles.others}>
            {others.map((t) => {
              const s = row.prices[t];
              return (
                <span key={t}>
                  {PRICE_TYPE_LABEL[t]} <span className="mono">{dollars(s?.typical?.rate_cents)}</span>
                </span>
              );
            })}
          </p>
        )}
        {summary && summary.count > 1 && (
          <button
            type="button"
            className={styles.variantsButton}
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={() => setOpen(!open)}
          >
            {summary.count} published lines · {dollars(summary.lowest?.rate_cents)} to{" "}
            {dollars(summary.highest?.rate_cents)}
            <ChevronIcon open={open} />
          </button>
        )}
      </div>

      {open && summary && (
        <div id={detailsId} className={styles.variants}>
          <p className={styles.variantsNote}>
            This hospital lists the code several ways (for example limited, one side, both sides). Each price
            is its own row in the file.
          </p>
          <ul>
            {(["lowest", "typical", "highest"] as const).map((k) => {
              const p: Price | null = summary[k];
              if (!p) return null;
              return (
                <li key={k}>
                  <span className={styles.variantKind}>{k}</span>
                  <span className={`${styles.variantPrice} mono`}>{dollars(p.rate_cents)}</span>
                  <span className={styles.variantDesc}>{p.description}</span>
                  <span className={`${styles.variantRow} mono`}>row {p.provenance.row_number.toLocaleString()}</span>
                </li>
              );
            })}
          </ul>
          <Link to={detailHref}>See every payer and the source row</Link>
        </div>
      )}
    </li>
  );
}
