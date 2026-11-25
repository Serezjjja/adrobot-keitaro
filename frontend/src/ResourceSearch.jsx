import { useId, useLayoutEffect, useRef, useState } from 'react'
import { scrollPopupOption, useDropdownPlacement } from './dropdown.js'
import SelectionIcon from './SelectionIcon.jsx'

export default function ResourceSearch({
  label, placeholder, value, onChange, disabled = false, query, onQueryChange,
  fieldError, onFieldEdit,
  options, status, error, onRetry, onRefresh, onClose, retryLabel = 'Повторить поиск',
  className = '', statusInPopup = false, visuallyHiddenLabel = false, refreshing = false,
}) {
  const id = useId()
  const rootRef = useRef(null)
  const listRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [activeId, setActiveId] = useState(null)
  const selectedIndex = options.findIndex((option) => String(option.id) === String(value?.id))
  const activeIndex = options.findIndex((option) => String(option.id) === String(activeId))
  const exactId = /^#?\d+$/.test(query?.trim()) ? query.trim().replace(/^#/, '') : null
  const exactIndex = exactId === null ? -1 : options.findIndex((option) => String(option.id) === exactId)
  const highlightedIndex = activeIndex >= 0 ? activeIndex : exactIndex >= 0 ? exactIndex : Math.max(selectedIndex, 0)
  const highlighted = options[highlightedIndex]
  const listId = `${id}-list`
  const optionId = (index) => `${id}-option-${index}`
  const showPopup = Boolean(open && !disabled && (options.length || error || onRefresh || (statusInPopup && status)))
  const { side, listSpace } = useDropdownPlacement(showPopup, rootRef)

  useLayoutEffect(() => {
    if (showPopup) scrollPopupOption(listRef.current, highlightedIndex)
  }, [showPopup, highlightedIndex, highlighted?.id, listSpace])

  function select(option) {
    onChange(option)
    setActiveId(null)
    setOpen(false)
  }

  function close() {
    setOpen(false)
    setActiveId(null)
    onClose?.()
  }

  function onKeyDown(event) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setOpen(true)
      if (options.length) {
        const step = event.key === 'ArrowDown' ? 1 : -1
        const index = open ? Math.max(0, Math.min(options.length - 1, highlightedIndex + step)) : highlightedIndex
        setActiveId(options[index].id)
      }
    } else if (event.key === 'Enter' && (open || (query && !value))) {
      event.preventDefault()
      if (open && highlighted) select(highlighted)
    } else if (event.key === 'Escape' && open) {
      event.preventDefault()
      close()
    }
  }

  return (
    <div
      className={`combo ${className}`.trim()}
      ref={rootRef}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) close()
      }}
    >
      <label htmlFor={id} className={visuallyHiddenLabel ? 'sr-only' : undefined}>{label}</label>
      <div className="combo-control">
        <svg className="search-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
          <circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" strokeWidth="1.6" />
          <path d="m13 13 4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <input
          id={id}
          type="text"
          role="combobox"
          autoComplete="off"
          aria-autocomplete="list"
          aria-expanded={showPopup}
          aria-controls={listId}
          aria-activedescendant={showPopup && highlighted ? optionId(highlightedIndex) : undefined}
          aria-invalid={fieldError ? true : undefined}
          aria-describedby={[fieldError && `${id}-field-error`, (!statusInPopup || (showPopup && status)) && `${id}-status`,
            error && showPopup && `${id}-error`].filter(Boolean).join(' ') || undefined}
          disabled={disabled}
          value={query}
          placeholder={placeholder}
          onFocus={() => setOpen(true)}
          onClick={() => setOpen(true)}
          onChange={(event) => {
            onFieldEdit?.()
            onQueryChange(event.target.value)
            setActiveId(null)
            setOpen(true)
          }}
          onKeyDown={onKeyDown}
        />
        {query && (
          <button
            type="button"
            className="combo-clear"
            aria-label="Очистить поиск"
            disabled={disabled}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              onFieldEdit?.()
              onQueryChange('')
              setActiveId(null)
              setOpen(true)
              rootRef.current?.querySelector('input')?.focus()
            }}
          ><SelectionIcon kind="clear" /></button>
        )}
        {onRefresh && (
          <button type="button" className="combo-toggle" tabIndex={-1} disabled={disabled}
            aria-label={showPopup ? 'Закрыть список кампаний' : 'Открыть список кампаний'}
            aria-expanded={showPopup} aria-controls={listId}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              rootRef.current?.querySelector('input')?.focus()
              if (open) close()
              else setOpen(true)
            }}>
            <SelectionIcon kind="chevron" className="combo-toggle-icon" />
          </button>
        )}
      </div>
      {fieldError && <p id={`${id}-field-error`} className="campaign-field-error" role="alert">{fieldError}</p>}
      {!statusInPopup && <div id={`${id}-status`} className="combo-status" role="status" aria-live="polite">
        {status && <span key={status} className="combo-status-message">{status}</span>}
      </div>}
      <div className="combo-popup" hidden={!showPopup} inert={!showPopup} data-side={side}
        style={{ '--popup-list-space': listSpace + 'px' }}>
        {statusInPopup && <div id={`${id}-status`} className="combo-status" role="status" aria-live="polite">
          {status && <span key={status} className="combo-status-message">{status}</span>}
        </div>}
        <ul id={listId} className="combo-list" role="listbox" aria-label={label} ref={listRef}>
          {options.map((option, index) => {
            const selected = String(option.id) === String(value?.id)
            return (
              <li
                key={option.id}
                id={optionId(index)}
                role="option"
                aria-selected={selected}
                className={`combo-option${index === highlightedIndex ? ' is-active' : ''}${selected ? ' is-selected' : ''}`}
                onMouseEnter={() => setActiveId(option.id)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(option)}
              >
                <span className="combo-option-name">{option.name}</span>
                <span className="combo-option-id">#{option.id}</span>
                <span className="combo-option-mark">{selected && <SelectionIcon kind="check" className="combo-option-check" />}</span>
              </li>
            )
          })}
        </ul>
        {error && <div id={`${id}-error`} className="combo-status" role="alert">{error}</div>}
        {!onRefresh && error && onRetry && <button type="button" className="combo-retry" onClick={() => {
          rootRef.current?.querySelector('input')?.focus()
          onRetry()
        }}>{retryLabel}</button>}
        {onRefresh && (
          <button type="button" className="combo-retry combo-refresh progress-button" disabled={refreshing} onClick={() => {
            rootRef.current?.querySelector('input')?.focus()
            onRefresh()
          }}>
            <span className="progress-size" aria-hidden="true">Загружаем кампании…</span>
            <span className="progress-label" aria-live="polite">
              {refreshing && <span className="loading-spinner" aria-hidden="true" />}
              {refreshing ? 'Загружаем кампании…' : error ? retryLabel : 'Обновить список'}
            </span>
          </button>
        )}
      </div>
    </div>
  )
}
