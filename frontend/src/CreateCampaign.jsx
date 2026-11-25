import SelectionIcon from './SelectionIcon.jsx'
import { useEffect, useRef, useState } from 'react'
import { request } from './api.js'
import OfferSearch from './OfferSearch.jsx'
import CatalogSelect from './CatalogSelect.jsx'

const catalogFields = [
  ['domain_id', 'Домен', 'domains'],
  ['group_id', 'Группа кампаний', 'groups'],
  ['traffic_source_id', 'Источник трафика', 'traffic_sources'],
]

export default function CreateCampaign({ onCreated, busy, onBusyChange }) {
  const [settings, setSettings] = useState(null)
  const [form, setForm] = useState({ name: '', country: '', domain_id: '', group_id: '', traffic_source_id: '' })
  const [offer, setOffer] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reload, setReload] = useState(0)
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [catalogErrors, setCatalogErrors] = useState({})
  const [fieldErrors, setFieldErrors] = useState({})
  const initializedName = useRef(false)
  const offerField = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    setSettings(null)
    setLoading(true)
    setError('')
    Promise.all([
      request('/catalogs', { signal: controller.signal }),
      request('/settings', { signal: controller.signal }),
    ]).then(([catalogs, config]) => {
      if (controller.signal.aborted) return
      setSettings({ catalogs, prefix: config.campaign_prefix })
      setCatalogErrors({})
      const prefillName = !initializedName.current
      initializedName.current = true
      setForm((current) => {
        const next = { ...current }
        if (prefillName) next.name = config.campaign_prefix
        for (const [field, , key] of catalogFields) {
          const options = catalogs[key]
          if (!options.some((item) => String(item.id) === current[field])) {
            next[field] = options.length === 1 ? String(options[0].id) : ''
          }
        }
        return next
      })
      setCatalogOpen(catalogFields.some(([, , key]) => catalogs[key].length !== 1))
    }).catch((error) => {
      if (!controller.signal.aborted) setError(error.message)
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [reload])

  function changeField(event) {
    const { name, value } = event.target
    setForm((current) => ({ ...current, [name]: name === 'country' ? value.toUpperCase() : value }))
    setFieldErrors((current) => ({ ...current, [name]: undefined }))
  }

  async function create(event) {
    event.preventDefault()
    if (busy || !settings) return
    const name = form.name.trim()
    const country = form.country.trim()
    const nextFieldErrors = {}
    if (name === settings.prefix || !name.startsWith(settings.prefix) || !/^test_[a-z0-9]+_.+$/u.test(name)) {
      nextFieldErrors.name = settings.prefix === 'test_'
        ? 'Введите название в формате test_фамилия_название (латинские буквы и цифры в фамилии).'
        : `Начните название с ${settings.prefix} и добавьте имя кампании.`
    }
    if (!/^[A-Z]{2}$/.test(country)) nextFieldErrors.country = 'Укажите двухбуквенный код страны, например CA.'
    if (!offer) nextFieldErrors.offer = 'Найдите и выберите существующий оффер.'
    setFieldErrors(nextFieldErrors)
    if (Object.keys(nextFieldErrors).length) {
      if (nextFieldErrors.name) document.getElementById('campaign-name')?.focus()
      else if (nextFieldErrors.country) document.getElementById('campaign-country')?.focus()
      else offerField.current?.querySelector('input')?.focus()
      return
    }
    const missing = catalogFields.filter(([field, , key]) =>
      !settings.catalogs[key].some((option) => String(option.id) === form[field]))
    const nextErrors = Object.fromEntries(missing.map(([field, , key]) => [field,
      settings.catalogs[key].length ? 'Выберите значение из Keitaro.' : 'В Keitaro нет доступных значений.'
    ]))
    setCatalogErrors(nextErrors)
    if (missing.length) {
      setCatalogOpen(true)
      setError('Проверьте настройки Keitaro.')
      requestAnimationFrame(() => document.getElementById(missing[0][0])?.focus())
      return
    }
    onBusyChange(true)
    setError('')
    try {
      const campaign = await request('/campaigns', {
        method: 'POST',
        body: {
          name,
          country,
          offer_id: offer.id,
          domain_id: Number(form.domain_id),
          group_id: Number(form.group_id),
          traffic_source_id: Number(form.traffic_source_id),
        },
      })
      onCreated(campaign)
    } catch (error) {
      if (error.status === 422 && error.message.startsWith('country — ')) {
        setFieldErrors((current) => ({ ...current, country: error.message.replace(/^country — (Value error, )?/, '') }))
        requestAnimationFrame(() => document.getElementById('campaign-country')?.focus())
      } else if (error.status === 422 && error.message.startsWith('Начните название')) {
        setFieldErrors((current) => ({ ...current, name: error.message }))
        requestAnimationFrame(() => document.getElementById('campaign-name')?.focus())
      } else setError(error.message)
    } finally {
      onBusyChange(false)
    }
  }

  const missingCatalog = settings && catalogFields.some(([, , key]) => !settings.catalogs[key].length)
  const needsChoice = catalogFields.some(([name]) => !form[name])

  return (
    <section aria-labelledby="create-heading">
      <header className="create-heading">
        <h1 className="hero-heading" id="create-heading">Новая кампания</h1>
      </header>
      {error && <div className="notice error" role="alert">{error}</div>}
      {loading ? <p role="status">Загружаем настройки Keitaro…</p> : !settings ? (
        <button type="button" onClick={() => setReload((value) => value + 1)}>Повторить загрузку настроек</button>
      ) : (
        <form className="create-form" onSubmit={create} aria-busy={busy} noValidate>
          <fieldset disabled={busy}>
            <div className="form-grid">
              <div className="field">
                <label htmlFor="campaign-name">Название кампании</label>
                <input id="campaign-name" name="name" value={form.name} onChange={changeField}
                  placeholder={settings.prefix + 'campaign1'} required maxLength={100} autoComplete="off"
                  aria-invalid={fieldErrors.name ? true : undefined}
                  aria-describedby={fieldErrors.name ? "prefix-help campaign-name-error" : "prefix-help"} />
                <p className="field-status" id="prefix-help">Префикс — {settings.prefix}</p>
                {fieldErrors.name && <p className="campaign-field-error" id="campaign-name-error" role="alert">{fieldErrors.name}</p>}
              </div>
              <div className="field">
                <label htmlFor="campaign-country">Код страны</label>
                <input id="campaign-country" name="country" value={form.country} onChange={changeField}
                  placeholder="CA" pattern="[A-Z]{2}" title="Две латинские буквы, например CA" required
                  minLength={2} maxLength={2} autoComplete="off" className="country-input"
                  aria-invalid={fieldErrors.country ? true : undefined}
                  aria-describedby={fieldErrors.country ? "campaign-country-error" : undefined} />
                {fieldErrors.country && <p className="campaign-field-error" id="campaign-country-error" role="alert">{fieldErrors.country}</p>}
              </div>
              <div className="wide-field" ref={offerField}>
                <OfferSearch value={offer} disabled={busy} fieldError={fieldErrors.offer}
                  onFieldEdit={() => setFieldErrors((current) => ({ ...current, offer: undefined }))}
                  onChange={(value) => {
                    setOffer(value)
                    setFieldErrors((current) => ({ ...current, offer: undefined }))
                  }} />
              </div>
            </div>
          </fieldset>
          <details className="catalog-settings" open={catalogOpen}
            onToggle={(event) => setCatalogOpen(event.currentTarget.open)}>
            <summary>
              <span className="catalog-caption">
                <span>Настройки Keitaro</span>
                <span className="catalog-summary">{missingCatalog ? 'Нет доступных значений'
                  : needsChoice ? 'Требуется выбор'
                    : catalogFields.every(([, , key]) => settings.catalogs[key].length === 1)
                      ? 'Подставлены автоматически' : 'Настроено'}</span>
              </span>
              <span className="disclosure-chevron"><SelectionIcon kind="chevron" /></span>
            </summary>
            <div className="disclosure-content">
              <fieldset className="catalog-grid" disabled={busy}>
                {catalogFields.map(([name, label, key]) => (
                  <div className="field" key={name}>
                    <label id={`${name}-label`} htmlFor={name}>{label}</label>
                    <CatalogSelect id={name} labelId={`${name}-label`} options={settings.catalogs[key]}
                      value={form[name]} disabled={busy} errorId={catalogErrors[name] || !settings.catalogs[key].length ? `${name}-error` : undefined}
                      onChange={(value) => {
                        setForm((current) => ({ ...current, [name]: value }))
                        setCatalogErrors((current) => ({ ...current, [name]: undefined }))
                      }} />
                    {(catalogErrors[name] || settings.catalogs[key].length === 0) && (
                      <p className="catalog-error" id={`${name}-error`}>
                        {catalogErrors[name] || 'В Keitaro нет доступных значений.'}
                      </p>
                    )}
                  </div>
                ))}
              </fieldset>
              <div className="catalog-refresh">
                <button type="button" onClick={() => setReload((value) => value + 1)} disabled={busy}>Обновить справочники</button>
              </div>
            </div>
          </details>
          <div className="campaign-preview" aria-live="polite">
            <strong>Предпросмотр потоков</strong>
            <div><span>{/^[A-Z]{2}$/.test(form.country.trim()) ? form.country.trim() : 'Укажите страну'}</span><span aria-hidden="true">→</span><span>Google</span></div>
            <div><span>Остальной трафик</span><span aria-hidden="true">→</span><span>{offer ? `${offer.name} · #${offer.id} · 100%` : 'Выберите оффер'}</span></div>
          </div>
          <div className="form-actions">
            <button className="primary" type="submit" disabled={busy}>
              {busy ? <><span className="loading-spinner" aria-hidden="true" /> Создаём кампанию…</> : 'Создать кампанию'}
            </button>
          </div>
        </form>
      )}
    </section>
  )
}
