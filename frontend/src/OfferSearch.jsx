import { useEffect, useId, useState } from 'react'
import { request } from './api.js'

export default function OfferSearch({ value, onChange, disabled = false, excludeIds = [] }) {
  const id = useId()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)

  useEffect(() => {
    if (!query.trim() || value || disabled) {
      setResults([])
      setLoading(false)
      setSearched(false)
      return
    }

    const controller = new AbortController()
    setLoading(true)
    setError('')
    setSearched(false)
    const timer = setTimeout(async () => {
      try {
        const offers = await request(`/offers?q=${encodeURIComponent(query.trim())}`, {
          signal: controller.signal,
        })
        if (!controller.signal.aborted) {
          setResults(offers)
          setSearched(true)
        }
      } catch (error) {
        if (error.name !== 'AbortError') setError(error.message)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }, 250)

    // A new query invalidates the previous result, including during the debounce.
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [query, value, disabled])

  const available = results.filter((offer) => !excludeIds.includes(offer.id))

  function editQuery(event) {
    setQuery(event.target.value)
    setResults([])
    onChange(null)
  }

  function selectOffer(offer) {
    setQuery('')
    setResults([])
    onChange(offer)
  }

  return (
    <div className="offer-search">
      <label htmlFor={id}>Оффер по ID или названию</label>
      <input
        id={id}
        type="search"
        autoComplete="off"
        value={value ? `#${value.id} · ${value.name}` : query}
        onChange={editQuery}
        onKeyDown={(event) => {
          if (event.key === 'Enter') event.preventDefault()
        }}
        disabled={disabled}
        aria-describedby={`${id}-status`}
        placeholder="Начните вводить ID или название"
      />
      <div id={`${id}-status`} className="field-status" role="status">
        {loading && 'Поиск…'}
        {value && `Выбран оффер #${value.id}`}
        {!loading && !value && searched && available.length === 0 && 'Подходящих офферов нет.'}
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {available.length > 0 && !value && (
        <ul className="search-results" aria-label="Найденные офферы">
          {available.slice(0, 20).map((offer) => (
            <li key={offer.id}>
              <button type="button" onClick={() => selectOffer(offer)} disabled={disabled}>
                <span className="numeric">#{offer.id}</span>
                <span>{offer.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
