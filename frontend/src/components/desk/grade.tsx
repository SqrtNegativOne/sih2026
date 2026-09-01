import { cn } from '@/lib/utils'

export type GradeLetter = 'A' | 'B' | 'C' | 'D' | 'E'

// The letter is set on the band's own colour, so each pairing has to clear AA
// on its own. B and C already used a dark letter for that reason; D did not,
// and white on --grade-d (#d9741f) measured 3.25:1 -- under the 4.5:1 floor
// for a 10px glyph, and the one grade a reader most needs to distinguish from
// E beside it. A dark letter on the same orange clears it comfortably.
const GRADE_CLASS: Record<GradeLetter, string> = {
  A: 'bg-grade-a text-white',
  B: 'bg-grade-b text-[#12250c]',
  C: 'bg-grade-c text-[#3a2c02]',
  D: 'bg-grade-d text-[#3a1c02]',
  E: 'bg-grade-e text-white',
}

/** Map a 0–1 quality fraction (1 = best) onto the A–E scale. */
export function scoreToGrade(fraction: number): GradeLetter {
  if (fraction >= 0.85) return 'A'
  if (fraction >= 0.7) return 'B'
  if (fraction >= 0.5) return 'C'
  if (fraction >= 0.3) return 'D'
  return 'E'
}

export function Grade({ letter, className }: { letter: GradeLetter; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex h-4 w-4 items-center justify-center rounded-sm text-caption font-bold leading-none',
        GRADE_CLASS[letter],
        className,
      )}
    >
      {letter}
    </span>
  )
}

/** A grade chip derived from a 0–1 score, with the numeric score beside it. */
export function ScoreGrade({ fraction }: { fraction: number }) {
  return (
    <span className="inline-flex items-center gap-1">
      <Grade letter={scoreToGrade(fraction)} />
      <span className="desk-num text-muted-foreground">{fraction.toFixed(2)}</span>
    </span>
  )
}
