import type { SVGProps } from "react";

/**
 * Agent Cockpit brand mark as an inline SVG.
 *
 * Vendor-neutral instrument bezel with a horizon line and a climb indicator.
 * Inherits `currentColor` so callers control the tint via the standard
 * Tailwind text-color utilities (e.g. `text-primary`). Mirrors the
 * `GithubIcon` pattern for brand icons that lucide-react does not ship.
 */
export function CockpitLogo({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      role="img"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      {...props}
    >
      <title>Agent Cockpit</title>
      <rect x="2.5" y="2.5" width="19" height="19" rx="5" />
      <path d="M6 14.5h12" />
      <path d="M8.5 11.5 12 7.5l3.5 4" />
    </svg>
  );
}
