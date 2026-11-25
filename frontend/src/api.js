export class ApiError extends Error {
  constructor(message, status, uncertain = false) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.uncertain = uncertain
  }
}

function errorMessage(detail) {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const field = item.loc?.filter((part) => part !== 'body').join('.')
      return field ? `${field} — ${item.msg}` : item.msg
    }).join('\n')
  }
  return 'Сервер не смог выполнить запрос.'
}

export async function request(path, { body, ...options } = {}) {
  const writing = !['GET', 'HEAD'].includes((options.method ?? 'GET').toUpperCase())
  let response
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    if (error.name === 'AbortError' && !writing) throw error
    throw new ApiError('Соединение с сервером прервано. Проверьте состояние перед повтором операции.', 0, writing)
  }

  const data = await response.json().catch((error) => {
    if (error.name === 'AbortError' && !writing) throw error
    return null
  })
  if (!response.ok) {
    throw new ApiError(errorMessage(data?.detail), response.status, writing && response.status >= 500)
  }
  if (data === null && response.status !== 204) {
    throw new ApiError('Сервер вернул неожиданный ответ. Проверьте адрес API и состояние операции.', response.status, writing)
  }
  return data
}
