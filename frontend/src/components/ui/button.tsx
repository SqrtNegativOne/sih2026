import { cva, type VariantProps } from 'class-variance-authority'
import type { ButtonHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * The desk's action control.
 *
 * Before this existed, "run the expensive real computation" was spelled seven
 * different ways across the app -- h-5/h-6/h-7 boxes, px-2/px-3 padding,
 * rounded/rounded-sm/rounded-sm corners and caption/body text, chosen per
 * panel. The result was that Portfolio's "Run analysis", Fragility's "Run
 * sweep" and Backhaul's "Score every port" -- three instances of the same
 * gesture -- were three visibly different buttons, which reads as three
 * different kinds of thing.
 *
 * Variants encode intent, not colour:
 *   primary   the one main action on a screen (solid)
 *   action    a panel-level action -- the common case (outlined, market tone)
 *   danger    destructive or resetting (outlined, risk tone)
 *   ghost     low-emphasis chrome, on a light ground
 *   onDark    the same, on the navy top bar
 *
 * Disabled state is deliberately legible rather than invisible: 45% opacity
 * still reads, and `cursor-not-allowed` plus a `title` explains why. Several
 * controls here have no backend behind them at all and must say so instead of
 * silently doing nothing (the F-35 / P7 convention).
 */
const button = cva(
  'inline-flex shrink-0 items-center justify-center gap-1 whitespace-nowrap rounded-sm font-semibold ' +
    'transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-1 ' +
    'focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-45 ' +
    'aria-disabled:cursor-not-allowed aria-disabled:opacity-45',
  {
    variants: {
      variant: {
        primary: 'bg-primary text-primary-foreground hover:bg-primary/90 active:bg-primary/95',
        action:
          'border border-market/50 bg-market/10 text-market-on-soft hover:border-market hover:bg-market/20',
        danger: 'border border-risk/40 bg-risk-soft text-risk hover:border-risk hover:bg-risk/15',
        ghost: 'text-muted-foreground hover:bg-accent hover:text-foreground',
        onDark: 'bg-white text-primary hover:bg-white/90',
      },
      size: {
        // Every box is a multiple of 4. `xs` is for a control that lives
        // inside a 28px panel header and must clear it; `md` is the default
        // for a standalone action; `lg` is the empty state's single call to
        // action, which is the only button on its screen.
        xs: 'h-5 px-2 text-micro uppercase tracking-wide',
        sm: 'h-6 px-2 text-caption uppercase tracking-wide',
        md: 'h-7 px-3 text-body',
        lg: 'h-8 px-4 text-lead',
        icon: 'h-6 w-6 p-0',
      },
    },
    defaultVariants: { variant: 'action', size: 'sm' },
  },
)

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

export function Button({ className, variant, size, type = 'button', ...props }: ButtonProps) {
  return <button type={type} className={cn(button({ variant, size }), className)} {...props} />
}

