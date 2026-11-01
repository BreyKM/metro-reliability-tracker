// Whole minutes from the API (#30): positive is late, negative is early.
export function late(minutes: number | null): string {
  if (minutes === null) return '-'
  if (minutes === 0) return 'on time'
  return minutes > 0 ? `+${minutes} min` : `${-minutes} min early`
}
