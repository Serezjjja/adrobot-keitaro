import { useLayoutEffect, useState } from 'react'

export function useDropdownPlacement(open, rootRef) {
  const [placement, setPlacement] = useState({ side: 'bottom', listSpace: 240 })

  useLayoutEffect(() => {
    if (!open) return
    const viewport = window.visualViewport

    function position() {
      const bounds = rootRef.current?.getBoundingClientRect()
      if (!bounds) return
      const viewportTop = viewport?.offsetTop ?? 0
      const viewportHeight = viewport?.height ?? document.documentElement.clientHeight
      const above = bounds.top - viewportTop
      const below = viewportTop + viewportHeight - bounds.bottom
      const showAbove = below < 250 && above > below
      const side = showAbove ? 'top' : 'bottom'
      // Leave room for the panel gap, padding and the optional retry or refresh action.
      const listSpace = Math.max(40, Math.floor((showAbove ? above : below) - 60))
      setPlacement((previous) => previous.side === side && previous.listSpace === listSpace
        ? previous : { side, listSpace })
    }

    position()
    window.addEventListener('resize', position)
    window.addEventListener('scroll', position, { passive: true })
    viewport?.addEventListener('resize', position)
    viewport?.addEventListener('scroll', position, { passive: true })
    return () => {
      window.removeEventListener('resize', position)
      window.removeEventListener('scroll', position)
      viewport?.removeEventListener('resize', position)
      viewport?.removeEventListener('scroll', position)
    }
  }, [open, rootRef])

  return placement
}

export function scrollPopupOption(list, index) {
  const option = list?.children[index]
  if (!option) return
  const optionBounds = option.getBoundingClientRect()
  const listBounds = list.getBoundingClientRect()
  if (optionBounds.top < listBounds.top) list.scrollTop += optionBounds.top - listBounds.top
  else if (optionBounds.bottom > listBounds.bottom) list.scrollTop += optionBounds.bottom - listBounds.bottom
}
