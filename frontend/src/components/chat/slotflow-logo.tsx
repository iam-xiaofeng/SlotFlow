/** SlotFlow brand mark: a rounded badge with three offset "slot" bars that step
 *  rightward then back, reading as flow. Self-contained colors so it renders
 *  identically in light and dark themes. */
export function SlotFlowLogo({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      role="img"
      aria-label="SlotFlow"
      className={className}
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect width="32" height="32" rx="8" fill="#2563EB" />
      <rect x="7" y="8.8" width="15" height="3.4" rx="1.7" fill="#ffffff" />
      <rect x="10" y="14.3" width="15" height="3.4" rx="1.7" fill="#ffffff" fillOpacity="0.85" />
      <rect x="7" y="19.8" width="10" height="3.4" rx="1.7" fill="#ffffff" fillOpacity="0.7" />
    </svg>
  );
}
