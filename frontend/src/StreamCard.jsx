import SelectionIcon from './SelectionIcon.jsx'
import { useEffect, useId, useRef, useState } from 'react'
import OfferSearch from './OfferSearch.jsx'

function activeShareTotal(offers) {
  return offers.reduce((total, offer) => offer.enabled && !offer.removed ? total + offer.share : total, 0)
}

function PinIcon() {
  return (
    <svg className="pin-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="m13.3 2.9 3.8 3.8-2.2 1-2 3.3.2 2-1 1-2.9-2.9-4.8 4.8m4.8-4.8-2.9-2.9 1-1 2 .2 3.3-2 1-2.5Z"
        stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function ShareEditor({ offer, open, onOpen, onClose, blocked, saving, onSave }) {
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

function DraftChanges({ published, offers, total }) {
  const previous = new Map(published.map((offer) => [offer.id, offer]))
  const current = new Map(offers.map((offer) => [offer.id, offer]))
  const changes = []
  for (const id of new Set([...previous.keys(), ...current.keys()])) {
    const before = previous.get(id)
    const after = current.get(id)
    const name = after?.name ?? before.name
    const notes = []
    if ((!after || after.removed) && before && !before.removed) {
      notes.push('Будет убран', before.share + '% → 0%')
    } else if (after && !after.removed) {
      if (!before || before.removed) {
        notes.push(before ? 'Будет возвращён' : 'Будет добавлен', 'Доля ' + after.share + '%')
      } else if (before.share !== after.share) {
        notes.push(before.share + '% → ' + after.share + '%')
      }
      if (before && !before.removed && before.enabled !== after.enabled) {
        notes.push(after.enabled ? 'Будет включён' : 'Будет выключен')
      }
      if (Boolean(before?.pinned) !== after.pinned) {
        notes.push(after.pinned ? 'Доля закреплена' : 'Закрепление снято')
      }
    }
    if (notes.length) changes.push({ id, name, text: notes.join(' · ') })
  }
  if (!changes.length) return null

  const previousTotal = activeShareTotal(published)

  return (
    <details className="draft-changes">
      <summary>
        <span>Посмотреть изменения</span>
        <span className="disclosure-chevron"><SelectionIcon kind="chevron" /></span>
      </summary>
      <div className="disclosure-content">
        <ul>
          {changes.map(({ id, name, text }) => (
            <li key={id}><strong>#{id} {name}</strong><span>{text}</span></li>
          ))}
        </ul>
        {previousTotal !== total && <p className="field-status">Сумма активных долей · {previousTotal}% → {total}%</p>}
      </div>
    </details>
  )
}

function StreamNotices({ stream, total, feedback, pendingPublication }) {
  return (
    <div className="stream-notices">
      {stream.missing ? (
        <p className="notice error" role="alert">
          Поток отсутствует в Keitaro. {stream.dirty ? 'Черновик сохранён.' : 'Обновите потоки.'}
        </p>
      ) : stream.conflict && (
        <p className="notice error" role="alert">
          Поток изменился в Keitaro. {stream.dirty
            ? 'Черновик сохранён. Чтобы принять внешние изменения, нажмите «Отменить черновик и обновить».'
            : 'Обновите потоки, чтобы загрузить актуальное состояние.'}
        </p>
      )}
      {stream.issue && !stream.dirty && !stream.missing && !stream.conflict && (
        <p className="notice warning" role="status">
          В последнем снимке из Keitaro сумма активных долей — {total}%. {stream.issue}
        </p>
      )}
      {feedback?.kind === 'published' && (
        <p className="notice success" role="status">✓ Опубликовано в Keitaro · поток #{stream.id}</p>
      )}
      {feedback?.kind === 'reconciled' && <p className="notice warning" role="status">{feedback.message}</p>}
      {pendingPublication && (
        <div className="notice warning" role="alert">
          <strong>Публикация не подтверждена.</strong>
          {pendingPublication.message && !pendingPublication.message.includes('Публикация не подтверждена') && <p>{pendingPublication.message}</p>}
          <p>Нажмите «Обновить потоки из Keitaro», чтобы сверить состояние.</p>
        </div>
      )}
      {feedback?.kind === 'error' && <p className="notice error" role="alert">{feedback.message}</p>}
    </div>
  )
}

export default function StreamCard({ stream, operation, locked, onAction, onCancelAndSync, feedback, pendingPublication }) {
  pendingPublication = pendingPublication || (stream.publication_pending ? { message: '' } : null)
  const [picked, setPicked] = useState(null)
  const [showEmptyEditor, setShowEmptyEditor] = useState(false)
  const [editingShareId, setEditingShareId] = useState(null)
  const reasonId = useId()
  const blocked = Boolean(operation || locked || stream.missing || stream.conflict || pendingPublication)
  const active = stream.offers.filter((offer) => !offer.removed)
  const archived = stream.offers.filter((offer) => offer.removed)
  const previouslyActive = new Set(stream.published_offers?.filter((offer) => !offer.removed).map((offer) => offer.id))
  const candidate = active.some((offer) => offer.id === picked?.id) ? null : picked
  const fromArchive = archived.some((offer) => offer.id === candidate?.id)
  const total = activeShareTotal(active)
  const publishing = operation === 'publish'
  const readOnly = (!stream.editable || (!stream.offers.length && !showEmptyEditor))
    && !stream.dirty && !stream.missing && !stream.conflict && !pendingPublication && !feedback
  const needsRefresh = stream.conflict || stream.missing
  const canPublish = stream.editable && stream.dirty && !blocked && !stream.issue && active.some((offer) => offer.enabled) && total === 100

  let streamStatus = 'Снимок Keitaro · без локальных правок'
  if (publishing) streamStatus = 'Публикуем в Keitaro…'
  else if (operation) streamStatus = 'Сохраняем в Adrobot…'
  else if (pendingPublication) streamStatus = 'Ожидает сверки с Keitaro'
  else if (stream.dirty) streamStatus = needsRefresh ? 'Локальные правки сохранены' : 'Изменения ещё не в Keitaro'

  let publishReason = ''
  if (stream.missing) publishReason = 'Поток отсутствует в Keitaro.'
  else if (stream.conflict) publishReason = 'Сначала разрешите конфликт с Keitaro.'
  else if (pendingPublication) publishReason = 'Обновите потоки перед повторной публикацией.'
  else if (!stream.dirty) publishReason = 'Нет правок для публикации.'
  else if (!active.some((offer) => offer.enabled)) publishReason = 'Добавьте хотя бы один активный оффер.'
  else if (total !== 100) publishReason = 'Сейчас ' + total + '%. '
    + (stream.issue ?? 'Для публикации нужна сумма долей 100%.')
  else if (stream.issue) publishReason = stream.issue

  async function addOffer() {
    if (candidate && await onAction(stream, fromArchive ? 'restore' : 'add', candidate.id)) setPicked(null)
  }

  if (readOnly) {
    return (
      <article className="geo-rule" aria-labelledby={'stream-' + stream.id}>
        <h2 className="geo-label" id={'stream-' + stream.id}>{stream.name} <span className="numeric">#{stream.id}</span></h2>
        <span className="geo-readonly">{stream.editable ? 'Нет офферов' : 'Только просмотр'}</span>
        {stream.editable && (
          <button className="text-button empty-offer-action" type="button" disabled={blocked}
            onClick={() => {
              setShowEmptyEditor(true)
              requestAnimationFrame(() =>
                document.getElementById('stream-' + stream.id)?.closest('article')?.querySelector('input')?.focus())
            }}>Добавить оффер</button>
        )}
      </article>
    )
  }

  return (
    <article className={'stream' + (stream.dirty ? ' stream-dirty' : '')} aria-labelledby={'stream-' + stream.id}
      aria-busy={Boolean(operation)}>
      <header className="stream-header">
        <div className="stream-title-line">
          <h2 id={'stream-' + stream.id}>{stream.name} <span className="numeric">#{stream.id}</span></h2>
          {stream.dirty && <span className="draft-chip">Черновик</span>}
          <span className="stream-state" key={streamStatus}>{streamStatus}</span>
        </div>
      </header>
      <StreamNotices stream={stream} total={total} feedback={feedback} pendingPublication={publishing ? null : pendingPublication} />
      {stream.editable && (
        <>
          <div className="add-offer">
            <OfferSearch value={candidate} onChange={setPicked} disabled={blocked}
              streamId={stream.id} excludeIds={active.map((offer) => offer.id)} />
            <button type="button" onClick={addOffer} disabled={blocked || !candidate}>
              <span aria-hidden="true">＋</span> {fromArchive ? 'Вернуть из архива' : 'Добавить оффер'}
            </button>
          </div>
          <p className="share-help">Доли рассчитываются автоматически. Нажмите на процент, чтобы задать и закрепить долю вручную.</p>
          <div className="table-wrap">
            <table className="offer-table" aria-label="Офферы потока">
              <thead>
                <tr><th scope="col">Оффер</th><th scope="col">Доля</th><th scope="col"><span className="sr-only">Действия</span></th></tr>
              </thead>
              <tbody>
                {active.map((offer) => (
                  <tr key={offer.id}>
                    <td>
                      <span className="offer-name"><span className="numeric">#{offer.id}</span> {offer.name}</span>
                      {!offer.enabled && <span className="offer-meta">Выключен в Keitaro</span>}
                    </td>
                    <td className="share-cell">
                      {offer.enabled ? (
                        <ShareEditor offer={offer} open={editingShareId === offer.id}
                          onOpen={() => setEditingShareId(offer.id)} onClose={() => setEditingShareId(null)}
                          blocked={blocked} saving={operation === 'set_share'}
                          onSave={(share) => onAction(stream, 'set_share', offer.id, share)} />
                      ) : <span className="numeric">{offer.share}%</span>}
                      {offer.enabled && (
                        <button type="button" className={'pin-button' + (offer.pinned ? ' is-pinned' : '')}
                          aria-label={(offer.pinned ? 'Закреплено. Снять закрепление доли оффера ' : 'Закрепить долю оффера ') + offer.name}
                          title={offer.pinned ? 'Снять закрепление' : 'Закрепить ' + offer.share + '%'}
                          onClick={() => onAction(stream, offer.pinned ? 'unpin' : 'pin', offer.id)} disabled={blocked}>
                          <PinIcon />
                          {offer.pinned ? 'Закреплено' : 'Закрепить'}
                        </button>
                      )}
                    </td>
                    <td className="row-actions">
                      <button type="button" className="text-button remove-offer" aria-label={'Убрать оффер ' + offer.name}
                        disabled={blocked} onClick={() => onAction(stream, 'remove', offer.id)}>Убрать</button>
                    </td>
                  </tr>
                ))}
                {active.length === 0 && <tr><td colSpan={3} className="empty-cell">В потоке пока нет активных офферов.</td></tr>}
              </tbody>
            </table>
          </div>
          {archived.length > 0 && (
            <details className="offer-archive" open>
              <summary>
                <span>Архив · {archived.length}</span>
                <span className="disclosure-chevron"><SelectionIcon kind="chevron" /></span>
              </summary>
              <div className="disclosure-content">
                <div className="table-wrap">
                  <table className="offer-table archive-table" aria-label="Архив офферов">
                    <tbody>
                      {archived.map((offer) => (
                        <tr className="archived" key={offer.id}>
                          <td>
                            <span className="offer-name"><span className="numeric">#{offer.id}</span> {offer.name}</span>
                            <span className="offer-meta">{previouslyActive.has(offer.id) ? 'Будет убран при публикации' : 'В архиве'}</span>
                          </td>
                          <td className="share-cell"><span className="numeric">0%</span></td>
                          <td className="row-actions">
                            <button type="button" className="text-button" aria-label={'Вернуть оффер ' + offer.name}
                              onClick={() => onAction(stream, 'restore', offer.id)} disabled={blocked}>Вернуть</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </details>
          )}
        </>
      )}
      {stream.editable && stream.dirty && stream.published_offers && (
        <DraftChanges published={stream.published_offers} offers={stream.offers} total={total} />
      )}
      {(stream.editable || stream.dirty) && (
        <footer className="stream-footer">
          <div className="stream-summary">
            {stream.editable && <><span>Итого активные</span><strong className="stream-total numeric">{total}%</strong></>}
          </div>
          <div className="stream-actions">
            {stream.dirty && (
              <button type="button" onClick={() => needsRefresh ? onCancelAndSync(stream) : onAction(stream, 'cancel')}
                disabled={Boolean(operation || locked || pendingPublication)}>
                {needsRefresh ? 'Отменить черновик и обновить' : 'Отменить изменения'}
              </button>
            )}
            {stream.editable && (
              <button type="button" className="primary" disabled={!canPublish}
                aria-describedby={publishReason ? reasonId : undefined} onClick={() => onAction(stream, 'publish')}>
                {publishing ? <><span className="loading-spinner" aria-hidden="true" /> Публикуем…</> : 'Опубликовать в Keitaro'}
              </button>
            )}
            {stream.editable && publishReason && (
              <p className={'publish-reason' + (stream.dirty || stream.conflict || stream.missing ? ' is-blocked' : '')}
                id={reasonId}>{publishReason}</p>
            )}
          </div>
        </footer>
      )}
    </article>
  )
}
