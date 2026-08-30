import { useCallback, useEffect, useRef, useState } from 'react'

interface Size {
  width: number
  height: number
}

/**
 * Track a DOM element's content-box size via ResizeObserver. Returns the
 * current size and a ref callback to attach to the element.
 */
export function useElementSize<T extends HTMLElement>(): [Size, (node: T | null) => void] {
  const [size, setSize] = useState<Size>({ width: 0, height: 0 })
  const observed = useRef<T | null>(null)
  const observer = useRef<ResizeObserver | null>(null)

  const ref = useCallback((node: T | null) => {
    observer.current?.disconnect()
    observed.current = node
    if (node) {
      observer.current = new ResizeObserver((entries) => {
        const entry = entries[0]
        if (entry) {
          setSize({
            width: entry.contentRect.width,
            height: entry.contentRect.height,
          })
        }
      })
      observer.current.observe(node)
    }
  }, [])

  useEffect(() => () => observer.current?.disconnect(), [])

  return [size, ref]
}
