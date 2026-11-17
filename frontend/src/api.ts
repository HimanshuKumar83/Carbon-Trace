const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1').replace(/\/$/, '')

export type User = {
  id: string
  email: string
  display_name: string
}

export type Organization = {
  id: string
  name: string
  country_code: string | null
  region: string | null
  timezone: string
  reporting_currency: string | null
}

export type Membership = {
  organization: Organization
  role: string
}

export type AuthResponse = {
  access_token: string
  token_type: string
  user: User
  organization: Organization | null
}

export type CurrentUser = {
  user: User
  organizations: Membership[]
}

export type Page<T> = {
  items: T[]
  page: number
  page_size: number
  total: number
}

export type AnalyticsOverview = {
  total_kg_co2e: number
  total_tonnes_co2e: number
  scope_1_kg_co2e: number
  scope_2_kg_co2e: number
  scope_3_kg_co2e: number
  calculation_count: number
  data_quality_score: number | null
  has_data: boolean
}

export type TrendPoint = {
  month: string
  kg_co2e: number
}

export type ScopeTotal = {
  scope: number
  kg_co2e: number
}

export type CategoryTotal = {
  category: string
  kg_co2e: number
}

export type IngestionRun = {
  id: string
  status: string
  source_name: string | null
  source_sha256: string | null
  records_seen: number
  records_accepted: number
  records_rejected: number
  error_summary: Record<string, unknown> | null
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export type ActivityRecord = {
  id: string
  ingestion_run_id: string
  source_record_key: string | null
  raw_payload: Record<string, unknown>
  activity_category: string | null
  activity_date: string | null
  quantity: number | null
  unit: string | null
  country_code: string | null
  scope: number | null
  validation_status: string
  validation_issues: { field?: string; code?: string; message?: string }[]
  reviewed_by_user_id: string | null
  reviewed_at: string | null
  created_at: string
}

export type EmissionFactor = {
  id: string
  organization_id: string | null
  factor_key: string
  activity_category: string
  scope: number
  geography_type: string
  country_code: string | null
  activity_unit: string
  factor_value: number
  factor_unit: string
  method: string
  valid_from: string
  valid_to: string | null
  source_name: string
  source_url: string
  publication_version: string
  methodology: string
  quality_metadata: Record<string, unknown> | null
  is_illustrative: boolean
  is_active: boolean
}

export type PipelineRun = {
  id: string
  organization_id: string
  trigger_type: string
  reporting_period_start: string
  reporting_period_end: string
  status: string
  current_stage: string | null
  error_summary: Record<string, unknown> | null
  attempt: number
  started_at: string | null
  completed_at: string | null
  created_at: string
}

export type Insight = {
  id: string
  insight_type: string
  title: string
  body: string
  evidence: Record<string, unknown>
  confidence: number | null
  generation_method: string
}

export type Anomaly = {
  id: string
  activity_category: string
  period_start: string
  period_end: string
  observed_value: number
  baseline_value: number | null
  deviation: number | null
  method: string
  severity: string
  explanation: string
  evidence: Record<string, unknown>
}

export type AssistantCitation = {
  id: string
  kind: string
  label: string
  source_url: string | null
  page: number | null
}

export type AssistantAnswer = {
  interaction_id: string
  answer: string
  citations: AssistantCitation[]
  limitations: string[]
  period_start: string
  period_end: string
  provider: string
  model: string
}

export type AIRecommendation = {
  title: string
  verified_finding: string
  suggested_action: string
  rationale: string
  evidence_ids: string[]
  guidance_ids: string[]
}

export type RecommendationResponse = {
  interaction_id: string
  recommendations: AIRecommendation[]
  citations: AssistantCitation[]
  limitations: string[]
  provider: string
  model: string
}

export type KnowledgeDocument = {
  id: string
  title: string
  filename: string
  source_url: string | null
  status: string
  chunk_count: number
  error_message: string | null
  created_at: string
}

export type Report = {
  id: string
  organization_id: string
  pipeline_run_id: string
  report_type: string
  period_start: string
  period_end: string
  status: string
  content_sha256: string | null
  methodology_snapshot: Record<string, unknown> | null
  created_at: string
}

export type BenchmarkCountry = {
  country: string
  iso_code: string
}

export type BenchmarkCountries = {
  source_repository: string
  dataset_sha256: string
  retrieved_at: string
  items: BenchmarkCountry[]
}

export type BenchmarkTrend = {
  year: number
  co2_million_tonnes: number | null
  co2_per_capita_tonnes_per_person: number | null
  population: number | null
  co2_per_gdp_kg_per_international_dollar: number | null
}

export type BenchmarkSeries = {
  country: string
  iso_code: string
  source: string
  source_repository: string
  dataset_sha256: string
  retrieved_at: string
  units: Record<string, string>
  series: BenchmarkTrend[]
  organizational_emission_factor: false
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

type RequestOptions = {
  token?: string | null
  method?: string
  body?: BodyInit | object
  headers?: HeadersInit
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers)
  let body: BodyInit | undefined

  if (options.body instanceof FormData || options.body instanceof Blob || typeof options.body === 'string') {
    body = options.body
  } else if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(options.body)
  }
  if (options.token) headers.set('Authorization', `Bearer ${options.token}`)

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body,
    })
  } catch {
    throw new ApiError(`Could not reach the CarbonTrace API at ${API_BASE_URL}. Check that the backend is running.`, 0)
  }

  if (!response.ok) {
    let message = `Request failed (${response.status}).`
    try {
      const payload: unknown = await response.json()
      if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
        const detail = payload.detail
        message = typeof detail === 'string' ? detail : JSON.stringify(detail)
      } else if (typeof payload === 'object' && payload !== null && 'message' in payload && typeof payload.message === 'string') {
        message = payload.message
      }
    } catch {
      message = `${message} The server returned an unreadable error response.`
    }
    throw new ApiError(message, response.status)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function downloadReport(reportId: string, organizationId: string, token: string): Promise<Blob> {
  const query = new URLSearchParams({ organization_id: organizationId })
  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}/reports/${reportId}/download?${query}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
  } catch {
    throw new ApiError(`Could not reach the CarbonTrace API at ${API_BASE_URL}. Check that the backend is running.`, 0)
  }
  if (!response.ok) {
    let message = `Report download failed (${response.status}).`
    try {
      const payload = await response.json() as { detail?: unknown }
      if (typeof payload.detail === 'string') message = payload.detail
    } catch {
      message = `${message} The server returned an unreadable error response.`
    }
    throw new ApiError(message, response.status)
  }
  return response.blob()
}

export function organizationQuery(organizationId: string, extra: Record<string, string> = {}): string {
  return new URLSearchParams({ organization_id: organizationId, ...extra }).toString()
}
