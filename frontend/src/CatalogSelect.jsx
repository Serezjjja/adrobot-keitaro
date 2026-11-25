import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { scrollPopupOption, useDropdownPlacement } from './dropdown.js'
import SelectionIcon from './SelectionIcon.jsx'

export default function CatalogSelect({ id, labelId, options, value, onChange, disabled, errorId }) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const rootRef = useRef(null)
  const listRef = useRef(null)
  const typeahead = useRef({ text: '', time: 0 })
  const selectedIndex = options.findIndex((option) => String(option.id) === value)
  const selected = options[selectedIndex]
  const listId = `${id}-listbox`
  const menuOpen = open && !disabled
  const { side, listSpace } = useDropdownPlacement(menuOpen, rootRef)

  useEffect(() => {
    if (!open) return undefined
    function closeOutside(event) {
      if (!rootRef.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [open])

  useLayoutEffect(() => {
    if (menuOpen && active >= 0) scrollPopupOption(listRef.current, active)
  }, [menuOpen, active, listSpace])

  function show(preferred = selectedIndex >= 0 ? selectedIndex : 0) {
    setActive(options.length ? Math.max(0, Math.min(preferred, options.length - 1)) : -1)
    setOpen(true)
  }

  function choose(index) {
    if (options[index]) onChange(String(options[index].id))
    setOpen(false)
  }

  function handleKeyDown(event) {
    if (event.key === 'Tab') {
      setOpen(false)
      return
    }
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      setOpen(false)
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) show(selectedIndex >= 0 ? selectedIndex : event.key === 'ArrowDown' ? 0 : options.length - 1)
      else if (options.length) setActive((current) => Math.max(0, Math.min(options.length - 1, current + (event.key === 'ArrowDown' ? 1 : -1))))
      return
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      const index = event.key === 'Home' ? 0 : options.length - 1
      if (open) setActive(options.length ? index : -1)
      else show(index)
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (open) choose(active)
      else show()
      return
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = Date.now()
      const prefix = (now - typeahead.current.time < 700 ? typeahead.current.text : '') + event.key.toLocaleLowerCase()
      typeahead.current = { text: prefix, time: now }
      const index = options.findIndex((option) => option.name.toLocaleLowerCase().startsWith(prefix) || String(option.id).startsWith(prefix.replace(/^#/, '')))
      if (index >= 0) {
        event.preventDefault()
        if (open) setActive(index)
        else show(index)
      }
    }
  }

  return (
    <div className="combo catalog-select" ref={rootRef} onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
    }}>
      <button id={id} type="button" className="catalog-trigger" role="combobox"
        aria-labelledby={labelId} aria-expanded={menuOpen} aria-haspopup="listbox" aria-required="true"
        aria-controls={listId} aria-activedescendant={menuOpen && active >= 0 ? `${id}-option-${active}` : undefined}
        aria-invalid={errorId ? true : undefined} aria-describedby={errorId ? `${id}-value ${errorId}` : `${id}-value`}
        disabled={disabled} onKeyDown={handleKeyDown} onClick={() => open ? setOpen(false) : show()}>
        <span id={`${id}-value`} className={selected ? 'catalog-value' : 'catalog-placeholder'}>
          {selected ? <><span>{selected.name}</span>{' '}<span className="catalog-value-id">#{selected.id}</span></>
            : 'Выберите из Keitaro'}
        </span>
        <span className="catalog-chevron"><SelectionIcon kind="chevron" /></span>
      </button>
      <div className="combo-popup catalog-popup" hidden={!menuOpen} inert={!menuOpen} data-side={side}
        style={{ '--popup-list-space': listSpace + 'px' }}>
        <div className="combo-list" role="listbox" id={listId} aria-labelledby={labelId} ref={listRef}>
          {options.map((option, index) => (
            <div key={option.id} id={`${id}-option-${index}`} role="option"
              aria-selected={index === selectedIndex}
              className={`combo-option${index === active ? ' is-active' : ''}${index === selectedIndex ? ' is-selected' : ''}`}
              onMouseEnter={() => setActive(index)} onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(index)}>
              <span className="combo-option-name">{option.name}</span>
              <span className="combo-option-id">#{option.id}</span>
              <span className="combo-option-mark">{index === selectedIndex && <SelectionIcon kind="check" className="combo-option-check" />}</span>
            </div>
          ))}
        </div>
        {!options.length && <p className="field-status">В Keitaro нет доступных значений.</p>}
      </div>
    </div>
  )
}
