import { Check, Link2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'

/**
 * Copies the current URL, which `lib/deep-link.ts` keeps describing whatever
 * is on screen.
 *
 * It reads `window.location.href` rather than rebuilding the link from props
 * on purpose: the URL is already the single source of truth for "where am I",
 * and a button that reconstructs it separately is one refactor away from
 * copying a link to a different quote than the one being looked at.
 *
 * What the recipient gets is the CARGO, not a frozen answer — the link
 * re-solves against current data when it opens. That is the honest behaviour
 * for a decision tool, and it is also why the confirmation says "Link copied"
 * rather than anything implying the numbers travel with it. The one-page brief
 * beside this button is the artefact for "these were the numbers at 14:05".
 */
export function CopyLinkButton() {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (timer.current != null) window.clearTimeout(timer.current)
    }
  }, [])

  function copy() {
    const url = window.location.href
    // navigator.clipboard is unavailable on plain http:// origins other than
    // localhost, which is exactly how this desk gets demoed on a LAN. Falling
    // back to a real prompt is better than a button that silently does
    // nothing: the user can still copy the URL, and they can see what it is.
    const ok = () => {
      setCopied(true)
      if (timer.current != null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 2000)
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(ok, () => window.prompt('Copy this link:', url))
    } else {
      window.prompt('Copy this link:', url)
    }
  }

  return (
    <Button
      size="sm"
      onClick={copy}
      title="Copy a link to this quote. It re-prices against current data when opened."
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-go" aria-hidden="true" />
      ) : (
        <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      {copied ? 'Link copied' : 'Copy link'}
    </Button>
  )
}
