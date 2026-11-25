import { useEffect, useId, useRef, useState } from 'react'

export default function ShareEditor({ offer, open, onOpen, onClose, blocked, saving, onSave }) {
  const [value, setValue] = useState(String(offer.share))
  const [error, setError] = useState('')
  const editorRef = useRef(null)
  const triggerRef = useRef(null)
  const inputRef = useRef(null)
  const id = useId()

  useEffect(() => {
    if (open) inputRef.current?.select()
  }, [open])

  useEffect(() => {
    if (!open || saving) return
    function closeOutside(event) {
      if (!editorRef.current?.contains(event.target)) onClose()
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [open, onClose, saving])

  function close() {
    onClose()
    requestAnimationFrame(() => triggerRef.current?.focus())
  }

  async function save(event) {
    event.preventDefault()
    if (blocked) return
    if (!/^\d+$/.test(value) || Number(value) > 100) {
      setError('Введите целое число от 0 до 100.')
      inputRef.current?.focus()
      return
    }
    setError('')
    try {
      if (await onSave(Number(value))) close()
      else setError('Операция уже выполняется. Повторите попытку.')
    } catch (error) {
      setError(error.message)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }

  return (
    <div className="share-edit-control" ref={editorRef} onBlur={(event) => {
      if (open && !saving && event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) onClose()
    }}>
      <button ref={triggerRef} type="button" className="share-value-button numeric"
        aria-label={offer.share + '% · Изменить долю оффера ' + offer.name} aria-expanded={open}
        aria-controls={open ? id + '-editor' : undefined}
        title="Изменить долю" disabled={blocked} onClick={() => {
          if (open) close()
          else {
            setValue(String(offer.share))
            setError('')
            onOpen()
          }
        }}>{offer.share}%</button>
      {open && <form id={id + '-editor'} className="share-editor" onSubmit={save} noValidate
        onKeyDown={(event) => {
          if (event.key === 'Escape' && !saving) {
            event.preventDefault()
            event.stopPropagation()
            close()
          }
        }}>
        <label htmlFor={id}>Доля, %</label>
        <input ref={inputRef} id={id} className="share-editor-input" type="number" inputMode="numeric" min="0" max="100" step="1"
          value={value} disabled={blocked} autoFocus aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined} onChange={(event) => {
            setValue(event.target.value)
            setError('')
          }} />
        {error && <p className="share-editor-error" id={`${id}-error`} role="alert">{error}</p>}
        <div className="share-editor-actions">
          <button type="submit" className="primary" disabled={blocked}>Сохранить</button>
          <button type="button" disabled={saving} onClick={close}>Отмена</button>
        </div>
      </form>}
    </div>
  )
}
