import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * tailwind-merge has to be told about the desk's own type scale.
 *
 * The scale adds seven font-size utilities (`text-micro` … `text-display`,
 * defined as `--text-*` theme keys in index.css). tailwind-merge only knows
 * Tailwind's stock sizes, so it classified these as *text colours* and treated
 * them as conflicting with real colour classes — and since cva emits variant
 * classes before size classes, the size won and the colour was silently
 * dropped.
 *
 * That is not a cosmetic detail: it rendered the primary button's label in the
 * body foreground on a primary-blue fill — measured at a 2.48:1 contrast
 * ratio, well under the 4.5:1 AA floor — while the markup still said
 * `text-primary-foreground`. Declaring the scale as a font-size group fixes
 * every such collision at once, rather than per call site.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        {
          text: ["micro", "caption", "body", "lead", "figure", "figure-lg", "display"],
        },
      ],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
