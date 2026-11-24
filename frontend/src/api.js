export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
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
  let response
  try {
    response = await fetch(`/api${path}`, {
      ...options,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new ApiError('Соединение с сервером прервано. Проверьте состояние перед повтором операции.', 0)
  }

  const data = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(errorMessage(data?.detail), response.status)
  }
  if (data === null && response.status !== 204) {
    throw new ApiError('Сервер вернул неожиданный ответ. Проверьте адрес API и состояние операции.', response.status)
  }
  return data
}
