import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, organizationQuery } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('apiRequest', () => {
  it('sends authenticated JSON requests and parses the response', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(JSON.stringify({ result: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await apiRequest<{ result: string }>('/example', {
      token: 'test-token',
      method: 'POST',
      body: { value: 3 },
    })

    expect(result).toEqual({ result: 'ok' })
    const [, init] = fetchMock.mock.calls[0]
    const headers = new Headers(init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer test-token')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(init?.body).toBe(JSON.stringify({ value: 3 }))
  })

  it('surfaces API validation details as errors', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'A completed ingestion is required.' }),
      { status: 409, headers: { 'Content-Type': 'application/json' } },
    )))

    await expect(apiRequest('/pipeline/run')).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      message: 'A completed ingestion is required.',
    } satisfies Partial<ApiError>)
  })
})

it('encodes organization-scoped query parameters', () => {
  const query = organizationQuery('org/id', { page_size: '20' })
  expect(new URLSearchParams(query).get('organization_id')).toBe('org/id')
  expect(new URLSearchParams(query).get('page_size')).toBe('20')
})
