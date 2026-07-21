export const VALIDATION_GEH_GOOD_LT = 5;
export const VALIDATION_GEH_WARN_LT = 10;
export const VALIDATION_ERROR_GOOD_PCT = 5;
export const VALIDATION_ERROR_WARN_PCT = 15;

export type ValidationTone = "success" | "warning" | "danger" | "neutral";

export function gehTone(value: number | null | undefined): ValidationTone {
  if (value == null || !Number.isFinite(value)) return "neutral";
  if (value < VALIDATION_GEH_GOOD_LT) return "success";
  if (value < VALIDATION_GEH_WARN_LT) return "warning";
  return "danger";
}

export function errorTone(value: number | null | undefined): ValidationTone {
  if (value == null || !Number.isFinite(value)) return "neutral";
  const abs = Math.abs(value);
  if (abs <= VALIDATION_ERROR_GOOD_PCT) return "success";
  if (abs <= VALIDATION_ERROR_WARN_PCT) return "warning";
  return "danger";
}

const TONE_TEXT: Record<ValidationTone, string> = {
  success: "text-emerald-700",
  warning: "text-amber-700",
  danger: "text-rose-700",
  neutral: "text-slate-400",
};

const TONE_BADGE: Record<ValidationTone, string> = {
  success: "bg-emerald-100 text-emerald-700",
  warning: "bg-amber-100 text-amber-700",
  danger: "bg-rose-100 text-rose-700",
  neutral: "bg-slate-100 text-slate-500",
};

const TONE_DOT: Record<ValidationTone, string> = {
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
  neutral: "bg-slate-300",
};

const TONE_BAR: Record<ValidationTone, string> = {
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
  neutral: "bg-slate-300",
};

export function toneTextClass(tone: ValidationTone): string {
  return TONE_TEXT[tone];
}
export function toneBadgeClass(tone: ValidationTone): string {
  return TONE_BADGE[tone];
}
export function toneDotClass(tone: ValidationTone): string {
  return TONE_DOT[tone];
}
export function toneBarClass(tone: ValidationTone): string {
  return TONE_BAR[tone];
}
