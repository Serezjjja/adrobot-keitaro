import { useEffect, useRef, useState } from 'react'
import { request } from './api.js'
import StreamCard from './StreamCard.jsx'

export default function CampaignEditor({ campaign, onBusyChange }) {
  const [streams, setStreams] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeOperation, setActiveOperation] = useState(null)
  const [errors, setErrors] = useState({})
  const [notices, setNotices] = useState({})
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const operationRef = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    request(`/campaigns/${campaign.id}/streams`, { signal: controller.signal })
      .then((data) => {
        setStreams(data)
        setLoaded(true)
      }).catch((error) => {
        if (error.name !== 'AbortError') setError(error.message)
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [campaign.id, loadAttempt])

  function beginOperation(operation) {
    // The ref closes the gap before React renders the disabled buttons.
    if (operationRef.current !== null) return false
    operationRef.current = operation
    setActiveOperation(operation)
    onBusyChange(true)
    return true
  }

  function finishOperation() {
    operationRef.current = null
    setActiveOperation(null)
    onBusyChange(false)
  }

  async function sync() {
    if (!beginOperation('sync')) return
    setError('')
    setNotices({})
    try {
      const current = await request(`/campaigns/${campaign.id}/sync`, { method: 'POST' })
      setStreams(current)
      setErrors({})
      setLoaded(true)
    } catch (error) {
      setError(error.message)
    } finally {
      finishOperation()
    }
  }

  async function action(stream, operation, offerId) {
    if (!beginOperation(stream.id)) return false
    setErrors((current) => ({ ...current, [stream.id]: '' }))
    setNotices((current) => ({ ...current, [stream.id]: '' }))
    const path = `/streams/${stream.id}`
    try {
      let updated
      if (operation === 'cancel') {
        updated = await request(`${path}/draft?version=${stream.version}`, { method: 'DELETE' })
      } else if (operation === 'publish') {
        updated = await request(`${path}/publish`, { method: 'POST', body: { version: stream.version } })
      } else {
        updated = await request(`${path}/draft`, { method: 'PATCH', body: { version: stream.version, action: operation, offer_id: offerId } })
      }
      setStreams((current) => current
        .map((item) => item.id === updated.id ? updated : item)
        .filter((item) => !(item.missing && !item.dirty)))
      if (operation === 'publish') {
        setNotices((current) => ({ ...current, [stream.id]: 'Изменения опубликованы в Keitaro.' }))
      }
      return true
    } catch (error) {
      setErrors((current) => ({ ...current, [stream.id]: error.message }))
      // Read the saved state after a stale version or uncertain write. GET keeps the draft intact.
      try {
        const saved = await request(`/campaigns/${campaign.id}/streams`)
        setStreams(saved)
      } catch {
        setErrors((current) => ({ ...current, [stream.id]: `${error.message}\nНе удалось обновить сохранённое состояние. Перезагрузите страницу перед повтором операции.` }))
      }
      return false
    } finally {
      finishOperation()
    }
  }

  return (
    <section aria-labelledby="editor-heading">
      <div className="section-heading">
        <div>
          <h1 id="editor-heading">{campaign.name}</h1>
          <p className="numeric">Кампания #{campaign.id}</p>
        </div>
        <button type="button" onClick={sync} disabled={loading || activeOperation !== null}>
          {activeOperation === 'sync' ? 'Загружаем из Keitaro…' : 'Загрузить потоки из Keitaro'}
        </button>
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}
      {loading && <p role="status">Загружаем сохранённое состояние…</p>}
      {!loading && !loaded && <button type="button" onClick={() => setLoadAttempt((value) => value + 1)}>Повторить загрузку</button>}
      {!loading && loaded && streams.length === 0 && (
        <div className="empty-state">
          <h2>Потоки ещё не загружены</h2>
          <p>Нажмите «Загрузить потоки из Keitaro», чтобы начать редактирование.</p>
        </div>
      )}
      {!loading && streams.length > 0 && (
        <div className="streams">
          {[...streams].sort((a, b) => a.position - b.position).map((stream) => (
            <StreamCard key={stream.id} stream={stream} onAction={action} busy={activeOperation === stream.id}
              locked={activeOperation !== null && activeOperation !== stream.id}
              error={errors[stream.id]} notice={notices[stream.id]} />
          ))}
        </div>
      )}
    </section>
  )
}
