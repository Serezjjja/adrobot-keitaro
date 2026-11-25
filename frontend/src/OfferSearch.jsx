import { useEffect, useState } from 'react'
import { request } from './api.js'
import ResourceSearch from './ResourceSearch.jsx'

export default function OfferSearch({ value, onChange, disabled = false, excludeIds = [], streamId, fieldError, onFieldEdit }) {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const term = !value && !disabled ? query.trim().replace(/^#(?=\d)/, '') : ''

  useEffect(() => {
    if (!term) return
    const controller = new AbortController()
    const timer = setTimeout(async () => {
      try {
        const offers = await request(`/offers?q=${encodeURIComponent(term)}${streamId == null ? '' : '&stream_id=' + streamId}`, {
          signal: controller.signal,
        })
        if (!controller.signal.aborted) setResult({ term, attempt, offers })
      } catch (error) {
        if (!controller.signal.aborted) setResult({ term, attempt, error: error.message })
      }
    }, 250)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [term, attempt, streamId, excludeIds.join(',')])

  const current = term && result?.term === term && result.attempt === attempt ? result : null
  const excluded = new Set(excludeIds.map(String))
  const available = current?.offers?.filter((offer) => !excluded.has(String(offer.id))) ?? []
  const status = term && !current ? 'Ищем офферы…'
    : value ? `Выбран оффер #${value.id}`
      : current?.offers && available.length === 0
        ? current.offers.length ? 'Все найденные офферы уже добавлены в поток.' : 'Офферы не найдены.'
        : ''

  return (
    <ResourceSearch
      className="offer-search"
      fieldError={fieldError}
      onFieldEdit={onFieldEdit}
      label="Оффер по ID или названию"
      placeholder="Например, #123"
      value={value}
      query={value ? `#${value.id} · ${value.name}` : query}
      onQueryChange={(nextQuery) => {
        setQuery(nextQuery)
        if (value) onChange(null)
      }}
      onChange={(offer) => {
        setQuery('')
        setResult(null)
        onChange(offer)
      }}
      options={available}
      disabled={disabled}
      status={status}
      error={current?.error}
      onRetry={() => setAttempt((previous) => previous + 1)}
    />
  )
}
