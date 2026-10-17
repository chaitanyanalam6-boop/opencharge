// Inline SVG only (brief §6: no icon fonts). All icons here are decorative: the text next to
// them carries the meaning, so they are hidden from screen readers.

type IconProps = { size?: number };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
  focusable: false,
});

export function SearchIcon({ size = 20 }: IconProps) {
  return (
    <svg {...base(size)}>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

export function FileIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

export function CheckIcon({ size = 16 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="m5 12 4.5 4.5L19 7" />
    </svg>
  );
}

export function AlertIcon({ size = 18 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M12 3 2 20h20z" />
      <path d="M12 10v4M12 17.5v.01" />
    </svg>
  );
}

export function ExternalIcon({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </svg>
  );
}

export function ChevronIcon({ size = 16, open = false }: IconProps & { open?: boolean }) {
  return (
    <svg {...base(size)} style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform 120ms" }}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="var(--teal)" />
      <path d="M9 21h4v-6H9zm5 0h4V11h-4zm5 0h4v-9h-4z" fill="#fff" />
    </svg>
  );
}
