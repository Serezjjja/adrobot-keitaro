import { useEffect, useRef, useState } from 'react'
import { request } from './api.js'
import OfferSearch from './OfferSearch.jsx'

const emptyCatalogs = { domains: [], groups: [], traffic_sources: [] }

export default function CreateCampaign({ onCreated, onBusyChange }) {
  const [catalogs, setCatalogs] = useState(emptyCatalogs)
  const [prefix, setPrefix] = useState('test_')
  const [form, setForm] = useState({ name: '', country: '', domain_id: '', group_id: '', traffic_source_id: '' })
  const [offer, setOffer] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const submitting = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    Promise.all([
      request('/catalogs', { signal: controller.signal }),
      request('/settings', { signal: controller.signal }),
    ]).then(([data, settings]) => {
      setCatalogs(data)
      setPrefix(settings.campaign_prefix)
    }).catch((error) => {
      if (error.name !== 'AbortError') setError(error.message)
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [reload])

  function changeField(event) {
    const { name, value } = event.target
    setForm((current) => ({ ...current, [name]: name === 'country' ? value.toUpperCase() : value }))
  }

  async function create(event) {
    event.preventDefault()
    if (submitting.current) return
    if (!offer) {
      setError('Найдите и выберите существующий оффер.')
      return
    }
    if (!form.name.trim().startsWith(prefix)) {
      setError(`Название должно начинаться с ${prefix}`)
      return
    }
    submitting.current = true
    setBusy(true)
    onBusyChange(true)
    setError('')
    try {
      const campaign = await request('/campaigns', {
        method: 'POST',
        body: {
          name: form.name.trim(),
          country: form.country.trim(),
          offer_id: offer.id,
          domain_id: Number(form.domain_id),
          group_id: Number(form.group_id),
          traffic_source_id: Number(form.traffic_source_id),
        },
      })
      onCreated(campaign)
    } catch (error) {
      setError(error.message)
    } finally {
      submitting.current = false
      setBusy(false)
      onBusyChange(false)
    }
  }

  const catalogFields = [
    ['domain_id', 'Домен', catalogs.domains],
    ['group_id', 'Группа кампаний', catalogs.groups],
    ['traffic_source_id', 'Источник трафика', catalogs.traffic_sources],
  ]

  return (
    <section aria-labelledby="create-heading">
      <div className="section-heading">
        <div>
          <h1 id="create-heading">Новая кампания</h1>
          <p>Геопоток на Google и основной поток на выбранный оффер.</p>
        </div>
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}
      {loading ? <p role="status">Загружаем настройки Keitaro…</p> : (
        <form className="create-form" onSubmit={create}>
          <fieldset disabled={busy}>
            <div className="form-grid">
              <div className="field wide-field">
                <label htmlFor="campaign-name">Название кампании</label>
                <input id="campaign-name" name="name" value={form.name} onChange={changeField}
                  placeholder={`${prefix}campaign1`} required maxLength={100} autoComplete="off" />
                <p className="field-status">Префикс для тестовых кампаний — {prefix}</p>
              </div>
              <div className="field">
                <label htmlFor="campaign-country">Код страны</label>
                <input id="campaign-country" name="country" value={form.country} onChange={changeField}
                  placeholder="AU" pattern="[A-Z]{2}" title="Две латинские буквы, например AU" required
                  minLength={2} maxLength={2} autoComplete="off" className="country-input" />
              </div>
              <div className="wide-field">
                <OfferSearch value={offer} onChange={setOffer} disabled={busy} />
              </div>
              {catalogFields.map(([name, label, options]) => (
                <div className="field" key={name}>
                  <label htmlFor={name}>{label}</label>
                  <select id={name} name={name} value={form[name]} onChange={changeField} required>
                    <option value="">Выберите из Keitaro</option>
                    {options.map((option) => <option key={option.id} value={option.id}>{option.name} · #{option.id}</option>)}
                  </select>
                  {options.length === 0 && <p className="field-status">В Keitaro нет доступных значений.</p>}
                </div>
              ))}
            </div>
          </fieldset>
          <div className="form-actions">
            <button className="primary" type="submit" disabled={busy || catalogFields.some(([, , options]) => !options.length)}>
              {busy ? 'Создаём кампанию…' : 'Создать кампанию'}
            </button>
            <button type="button" onClick={() => setReload((value) => value + 1)} disabled={busy}>Обновить справочники</button>
          </div>
        </form>
      )}
    </section>
  )
}
