export function moneyFromCents(cents) {
  const safe = Number.isFinite(Number(cents)) ? Math.round(Number(cents)) : 0
  const hasFraction = Math.abs(safe) % 100 !== 0
  return `$${(safe / 100).toLocaleString('en-US', {
    minimumFractionDigits: hasFraction ? 2 : 0,
    maximumFractionDigits: hasFraction ? 2 : 0,
  })}`
}
