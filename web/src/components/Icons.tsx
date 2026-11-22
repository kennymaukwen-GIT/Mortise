// Inline SVG icons (no icon font, nothing loaded from elsewhere).
import type { SVGProps } from "react";

type P = SVGProps<SVGSVGElement> & { size?: number };

function base({ size = 20, ...rest }: P) {
  return { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true, focusable: false, ...rest };
}

export const ArrowCircle = (p: P) => (
  <svg {...base(p)} strokeWidth={1.8}>
    <circle cx="12" cy="12" r="10.6" />
    <path d="M7 12h9.5M12.5 7.8 16.7 12l-4.2 4.2" />
  </svg>
);
export const ChevronLeft = (p: P) => (
  <svg {...base(p)}>
    <path d="m15 18-6-6 6-6" />
  </svg>
);
export const ChevronRight = (p: P) => (
  <svg {...base(p)}>
    <path d="m9 18 6-6-6-6" />
  </svg>
);
export const Caret = (p: P) => (
  <svg {...base({ size: 10, ...p })} viewBox="0 0 10 10" stroke="none" fill="currentColor">
    <path d="M1 2.5h8L5 8.5z" />
  </svg>
);
export const Plus = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 5v14M5 12h14" />
  </svg>
);
export const Minus = (p: P) => (
  <svg {...base(p)}>
    <path d="M5 12h14" />
  </svg>
);
export const Fit = (p: P) => (
  <svg {...base(p)}>
    <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
  </svg>
);
export const Grid = (p: P) => (
  <svg {...base(p)}>
    <rect x="3.5" y="4" width="7" height="7" rx="1.2" />
    <rect x="13.5" y="4" width="7" height="7" rx="1.2" />
    <rect x="3.5" y="13" width="7" height="7" rx="1.2" />
    <rect x="13.5" y="13" width="7" height="7" rx="1.2" />
  </svg>
);
export const Upload = (p: P) => (
  <svg {...base(p)} strokeWidth={1.5}>
    <path d="M12 15.5V4M7.5 8.5 12 4l4.5 4.5" />
    <path d="M4 14.5v3.5A2 2 0 0 0 6 20h12a2 2 0 0 0 2-2v-3.5" />
  </svg>
);
export const Alert = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="9.5" />
    <path d="M12 7.5v5.5M12 16.5v.01" />
  </svg>
);
export const Info = (p: P) => (
  <svg {...base(p)}>
    <circle cx="12" cy="12" r="9.5" />
    <path d="M12 11v5.5M12 7.5v.01" />
  </svg>
);
export const Close = (p: P) => (
  <svg {...base(p)}>
    <path d="M6 6l12 12M18 6 6 18" />
  </svg>
);
export const Check = (p: P) => (
  <svg {...base(p)}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);
export const Portrait = (p: P) => (
  <svg {...base(p)} strokeWidth={1.8}>
    <rect x="6.5" y="3.5" width="11" height="17" rx="1.5" />
  </svg>
);
export const Landscape = (p: P) => (
  <svg {...base(p)} strokeWidth={1.8}>
    <rect x="3.5" y="6.5" width="17" height="11" rx="1.5" />
  </svg>
);
export const Download = (p: P) => (
  <svg {...base(p)}>
    <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 20h14" />
  </svg>
);
export const Pencil = (p: P) => (
  <svg {...base(p)}>
    <path d="M4 20h4L19 9l-4-4L4 16z" />
  </svg>
);
export const User = (p: P) => (
  <svg {...base(p)} strokeWidth={1.6}>
    <circle cx="12" cy="9" r="3.5" />
    <path d="M5.5 19c1.2-3 3.7-4.5 6.5-4.5s5.3 1.5 6.5 4.5" />
  </svg>
);

/** The Mortise mark: an organic black shape in a white rounded square (redrawn from the mockup). */
export function Logo({ size = 36 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 36 36" aria-hidden="true" focusable="false">
      <rect width="36" height="36" rx="9" fill="#fff" />
      <path
        fill="#000"
        d="M21.9 8.1C21.8 6.4 22.9 5.7 24.2 5.8C25.7 5.9 26.7 7 26.5 8.6C26.4 9.6 25.7 10.6 25.8 11.8C25.9 13 26.6 14 26.7 15.6C26.8 18 26.9 20 28 21.5C29.4 23 30 25 29.6 26.8C29.2 28.8 27.6 30.2 25.2 30C23.2 29.8 21.6 28.4 20.4 27C18.5 26.4 15.5 26.6 13.4 25.6C11.4 24.6 9.8 22.8 8.2 21.6C7 20.6 6 19.8 6 18.4C6 17 7.2 16 8 15C9 13.5 9.8 12 11.2 10.4C12.4 9.1 13.6 8.4 15.2 8.2C17.5 8 19.8 8.3 21.9 8.1Z"
      />
    </svg>
  );
}
