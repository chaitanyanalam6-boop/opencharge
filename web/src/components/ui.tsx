import type { ReactNode } from "react";

import { AlertIcon } from "./Icons";
import styles from "./ui.module.css";

export { styles as ui };

type Tone = "teal" | "amber" | "danger" | "grey" | "solid";

const PILL_TONE: Record<Tone, string> = {
  teal: styles.pillTeal,
  amber: styles.pillAmber,
  danger: styles.pillDanger,
  grey: styles.pillGrey,
  solid: styles.pillSolid,
};

export function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`${styles.pill} ${PILL_TONE[tone]}`}>{children}</span>;
}

/** Status pills carry their meaning in words; the tone (set by the API) only reinforces it. */
export function StatusPill({ status, tone }: { status: string; tone: "green" | "amber" | "red" | "grey" }) {
  const pillTone: Tone = tone === "green" ? "teal" : tone === "amber" ? "amber" : tone === "red" ? "danger" : "grey";
  return <Pill tone={pillTone}>{status}</Pill>;
}

export function Notice({
  tone,
  children,
  icon = true,
}: {
  tone: "amber" | "danger" | "teal";
  children: ReactNode;
  icon?: boolean;
}) {
  const toneClass = tone === "amber" ? styles.noticeAmber : tone === "danger" ? styles.noticeDanger : styles.noticeTeal;
  return (
    <div className={`${styles.notice} ${toneClass}`} role={tone === "danger" ? "alert" : undefined}>
      {icon && <AlertIcon />}
      <div>{children}</div>
    </div>
  );
}

export function Skeleton({ height = 18, width = "100%" }: { height?: number; width?: string | number }) {
  return <div className={styles.skeleton} style={{ height, width }} aria-hidden="true" />;
}
