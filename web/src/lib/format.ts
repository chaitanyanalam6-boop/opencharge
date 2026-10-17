// Formatting only: these functions never compute, round or average a price. Cents in,
// the same amount out as dollars.

export function dollars(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "not published";
  return (cents / 100).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  });
}

export function compactCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  return n.toLocaleString("en-US");
}

export function count(n: number): string {
  return n.toLocaleString("en-US");
}

export function date(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  // Dates without a time are calendar dates; render them without a timezone shift.
  const d = iso.length === 10 ? new Date(`${iso}T12:00:00`) : new Date(iso);
  return d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

export function shortHash(sha: string): string {
  return `${sha.slice(0, 8)}…${sha.slice(-6)}`;
}

export const PRICE_TYPE_LABEL: Record<string, string> = {
  negotiated: "Negotiated rate",
  cash: "Cash price",
  gross: "Gross charge",
  min: "De-identified minimum",
  max: "De-identified maximum",
};

/** "73721" -> CPT, "J1100" -> HCPCS, "470" -> DRG; anything else is not a code. */
export function codeTypeOf(text: string): "CPT" | "HCPCS" | "DRG" | null {
  const value = text.trim().toUpperCase();
  if (/^\d{4}[0-9FTUMA]$/.test(value)) return "CPT";
  if (/^[A-V]\d{4}$/.test(value)) return "HCPCS";
  if (/^\d{3}$/.test(value)) return "DRG";
  return null;
}
