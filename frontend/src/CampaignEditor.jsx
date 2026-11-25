import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react'
import { request } from './api.js'
import { capturePublication, readPendingPublications, reconcilePublications, savePendingPublications } from './publications.js'
import SelectionIcon from './SelectionIcon.jsx'
import StreamCard from './StreamCard.jsx'

export default function CampaignEditor({ campaign, created, onBusyChange }) {
  const [streams, setStreams] = useState(null)
  const [loading, setLoading] = useState(true)
  const [operation, setOperation] = useState(null)
  const [feedback, setFeedback] = useState(null)
  const [pendingPublications, setPendingPublications] = useState(() => readPendingPublications(campaign.id))
  const pendingRef = useRef(pendingPublications)
  const [publicationResults, setPublicationResults] = useState({})
  const [adminUrl, setAdminUrl] = useState(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const operationRef = useRef(null)
  const initialRead = useRef(false)
  const headingRef = useRef(null)

  function updatePending(update) {
    const next = update(pendingRef.current)
    pendingRef.current = next
    savePendingPublications(campaign.id, next)
    setPendingPublications(next)
  }

  function clearPendingPublication(streamId) {
    updatePending((current) => {
      const remaining = { ...current }
      delete remaining[streamId]
      return remaining
    })
  }

  useEffect(() => {
    headingRef.current?.focus()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    request('/settings', { signal: controller.signal }).then((settings) => {
      if (!controller.signal.aborted) setAdminUrl(settings.keitaro_admin_url)
    }).catch(() => {
      // The link is optional. The editor can still work if settings could not be read.
    })
    return () => controller.abort()
  }, [])

  const runOperation = useCallback(async (kind, streamId, work) => {
    if (operationRef.current) return false
    const active = { kind, streamId }
    operationRef.current = active
    setOperation(active)
    setFeedback(null)
    if (streamId !== null) {
      setPublicationResults((previous) => ({ ...previous, [streamId]: null }))
    }
    onBusyChange(true)
    try {
      return await work()
    } finally {
      operationRef.current = null
      setOperation(null)
      onBusyChange(false)
    }
  }, [onBusyChange])

  const sync = useCallback((previousStreams) => runOperation('sync', null, async () => {
    try {
      const current = await request('/campaigns/' + campaign.id + '/sync', { method: 'POST' })
      setStreams(current)
      const { results, missing, remaining } = reconcilePublications(previousStreams, current, pendingRef.current)
      setPublicationResults(results)
      updatePending(() => remaining)
      if (missing.length) {
        setFeedback({ kind: 'sync-warning',
          message: 'Не удалось подтвердить публикацию. После сверки в кампании отсутствуют потоки '
            + missing.join(', ') + '.' })
      }
      return true
    } catch (error) {
      setFeedback({ kind: 'sync-error', message: error.message })
      return false
    }
  }), [campaign.id, runOperation])

  const syncOnFirstRead = useEffectEvent((current) => {
    if (initialRead.current) return
    initialRead.current = true
    if (created || current.length === 0) void sync(current)
  })

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setFeedback(null)
    request('/campaigns/' + campaign.id + '/streams', { signal: controller.signal })
      .then((current) => {
        if (controller.signal.aborted) return
        setStreams(current)
        syncOnFirstRead(current)
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFeedback({ kind: 'load-error', message: error.message })
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [campaign.id, loadAttempt])

  function action(stream, kind, offerId, share) {
    if (pendingRef.current[stream.id] || stream.publication_pending) return Promise.resolve(false)
    return runOperation(kind, stream.id, async () => {
      const path = '/streams/' + stream.id
      try {
        let updated
        if (kind === 'cancel') {
          updated = await request(path + '/draft?version=' + stream.version, { method: 'DELETE' })
        } else if (kind === 'publish') {
          updatePending((current) => ({
            ...current,
            [stream.id]: capturePublication(stream, 'Запрос публикации отправлен. Результат требует сверки.'),
          }))
          updated = await request(path + '/publish', { method: 'POST', body: { version: stream.version } })
          clearPendingPublication(stream.id)
        } else {
          updated = await request(path + '/draft', {
            method: 'PATCH', body: { version: stream.version, action: kind, offer_id: offerId,
              ...(kind === 'set_share' ? { share } : {}) },
          })
        }
        setStreams((current) => current.map((item) => item.id === updated.id ? updated : item)
          .filter((item) => !(item.missing && !item.dirty)))
        if (kind === 'publish') setFeedback({ kind: 'published', streamId: stream.id })
        return true
      } catch (error) {
        const uncertain = kind === 'publish' && error.uncertain
        if (uncertain) {
          updatePending((current) => ({ ...current, [stream.id]: capturePublication(stream, error.message) }))
        } else if (kind === 'publish') {
          clearPendingPublication(stream.id)
        }
        const issue = { streamId: stream.id, kind: 'error', message: error.message }

        try {
          const saved = await request('/campaigns/' + campaign.id + '/streams')
          setStreams(saved)
          const streamExists = saved.some((item) => item.id === stream.id)
          const offerExists = saved.some((item) => item.id === stream.id
            && item.offers.some((offer) => offer.id === offerId && offer.enabled && !offer.removed))
          if ((!uncertain && kind !== 'set_share') || !streamExists || (kind === 'set_share' && !offerExists)) {
            setFeedback({ ...issue, streamId: streamExists ? stream.id : null })
          }
        } catch {
          const reloadMessage = 'Не удалось перечитать состояние. Обновите потоки перед повтором операции.'
          const message = uncertain ? reloadMessage : issue.message + '\n' + reloadMessage
          if (kind === 'set_share') error = new Error(message)
          else setFeedback({ ...issue, message })
        }
        if (kind === 'set_share') throw error
        return false
      }
    })
  }

  async function cancelAndSync(stream) {
    if (!window.confirm('Отменить правки потока #' + stream.id + ' и загрузить его состояние из Keitaro?')) return
    if (await action(stream, 'cancel')) await sync(streams)
  }

  const refreshing = operation?.kind === 'sync'
  const campaignFeedback = feedback && feedback.streamId == null ? feedback : null
  const empty = !loading && !refreshing && streams?.length === 0 && !campaignFeedback

  return (
    <section aria-labelledby="editor-heading">
      <header className="campaign-hero">
        <div className="hero-identity">
          <h1 className="hero-heading" id="editor-heading" ref={headingRef} tabIndex={-1}>Редактор кампании</h1>
          <div className="hero-campaign">
            <span>{campaign.name}</span><span className="hero-id numeric">#{campaign.id}</span>
          </div>
        </div>
        <div className="hero-actions">
          {adminUrl && (
            <a className="refresh-button keitaro-link" href={adminUrl + '#!/campaign/' + campaign.id + '/main'}
              target="_blank" rel="noopener noreferrer">
              Открыть в Keitaro <SelectionIcon kind="external" className="external-link-icon" />
            </a>
          )}
          <button type="button" className="refresh-button progress-button" onClick={() => sync(streams)}
            disabled={loading || Boolean(operation)}>
            <span className="progress-size" aria-hidden="true">Обновить потоки из Keitaro</span>
            <span className="progress-label">
              {refreshing ? <><span className="loading-spinner" aria-hidden="true" /> Обновляем потоки…</>
                : 'Обновить потоки из Keitaro'}
            </span>
          </button>
        </div>
      </header>
      <div className="campaign-feedback">
        {created && <p className="notice success" role="status">Кампания {campaign.name} создана.</p>}
        {loading && !refreshing && <p className="notice" role="status">Загружаем сохранённое состояние…</p>}
        {refreshing && <p className="sr-only" role="status">Обновляем потоки из Keitaro…</p>}
        {campaignFeedback && (
          <div className={'notice ' + (campaignFeedback.kind === 'sync-warning' ? 'warning' : 'error')} role="alert">
            {campaignFeedback.message}
            {campaignFeedback.kind === 'load-error' && (
              <div><button type="button" onClick={() => setLoadAttempt((value) => value + 1)}
                disabled={loading || Boolean(operation)}>Повторить загрузку</button></div>
            )}
          </div>
        )}
      </div>
      {empty && <div className="empty-state"><h2>В Keitaro у кампании нет потоков</h2></div>}
      {streams?.length > 0 && (
        <div className="streams">
          {streams.map((stream) => (
            <StreamCard key={stream.id} stream={stream} onAction={action} onCancelAndSync={cancelAndSync}
              operation={operation?.streamId === stream.id ? operation.kind : null}
              locked={Boolean(operation && operation.streamId !== stream.id)}
              pendingPublication={pendingPublications[stream.id]}
              feedback={feedback?.streamId === stream.id ? feedback : publicationResults[stream.id]} />
          ))}
        </div>
      )}
    </section>
  )
}
