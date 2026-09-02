import { cn } from '@/lib/utils'

export type GradeLetter = 'A' | 'B' | 'C' | 'D' | 'E'

// The letter sits on the band's own colour, so each pairing has to clear AA on
// its own -- and which letter colour does that differs by BAND and by THEME
// (white works on light's dark-green A but not on its mid-orange D, where it
// measured 3.25:1). Both halves are therefore tokens, defined per theme in
// index.css, rather than literals here: a component cannot know which theme it
// is rendering into, so it must not be the thing that decides.
const GRADE_CLASS: Record<GradeLetter, string> = {
  A: 'bg-grade-a text-grade-a-fg',
  B: 'bg-grade-b text-grade-b-fg',
  C: 'bg-grade-c text-grade-c-fg',
  D: 'bg-grade-d text-grade-d-fg',
  E: 'bg-grade-e text-grade-e-fg',
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
