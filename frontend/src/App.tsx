import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  Building2,
  FileText,
  Gauge,
  Leaf,
  LogOut,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  UploadCloud,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  type AnalyticsOverview,
  type AIRecommendation,
  type ActivityRecord,
  type Anomaly,
  type AssistantAnswer,
  type AssistantCitation,
  type AuthResponse,
  type BenchmarkCountries,
  type BenchmarkSeries,
  type CategoryTotal,
  type CurrentUser,
  type EmissionFactor,
  type IngestionRun,
  type Insight,
  type KnowledgeDocument,
  type Organization,
  type Page,
  type PipelineRun,
  type Report,
  type RecommendationResponse,
  type ScopeTotal,
  type TrendPoint,
  apiRequest,
  downloadReport,
  organizationQuery,
} from './api'
import './App.css'

const TOKEN_KEY = 'carbontrace.access_token'
const SCOPE_COLORS = ['#3ddc97', '#7cc6ff', '#8b5cf6']
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1').replace(/\/api\/v1\/?$/, '')
const ACTIVITY_OPTIONS = [
  { value: 'electricity', scope: 2, method: 'location_based', units: ['kWh', 'MWh'] },
  { value: 'natural_gas', scope: 1, method: 'direct_combustion', units: ['kWh', 'MWh', 'therm', 'm3'] },
  { value: 'diesel', scope: 1, method: 'direct_combustion', units: ['L', 'gal'] },
  { value: 'petrol', scope: 1, method: 'direct_combustion', units: ['L', 'gal'] },
  { value: 'company_vehicle_fuel', scope: 1, method: 'direct_combustion', units: ['L', 'gal'] },
  { value: 'business_travel', scope: 3, method: 'activity_based', units: ['km', 'passenger_km'] },
  { value: 'employee_commuting', scope: 3, method: 'activity_based', units: ['km', 'passenger_km'] },
  { value: 'purchased_goods_services', scope: 3, method: 'activity_based', units: ['kg', 'tonne'] },
]

type DashboardData = {
  overview: AnalyticsOverview
  trend: TrendPoint[]
  scopes: ScopeTotal[]
  categories: CategoryTotal[]
  activities: ActivityRecord[]
  factors: EmissionFactor[]
  ingestions: IngestionRun[]
  pipelines: PipelineRun[]
  insights: Insight[]
  anomalies: Anomaly[]
  reports: Report[]
}

type AssistantResult =
  | { kind: 'answer'; answer: AssistantAnswer }
  | { kind: 'recommendations'; answer: RecommendationResponse }

function currentYearRange(): [string, string] {
  const today = new Date()
  return [`${today.getFullYear()}-01-01`, today.toISOString().slice(0, 10)]
}

function formatNumber(value: number, digits = 1): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value)
}

function formatTonnes(kg: number): string {
  return `${formatNumber(kg / 1000)} tCO₂e`
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value))
}

function messageFromError(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected error occurred.'
}

function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY))
  const [session, setSession] = useState<CurrentUser | null>(null)
  const [organizationId, setOrganizationId] = useState('')
  const [authLoading, setAuthLoading] = useState(Boolean(localStorage.getItem(TOKEN_KEY)))
  const [authError, setAuthError] = useState('')

  useEffect(() => {
    if (!token) return

    let active = true
    apiRequest<CurrentUser>('/auth/me', { token })
      .then((current) => {
        if (!active) return
        setSession(current)
        const savedOrganization = localStorage.getItem('carbontrace.organization_id')
        const selected = current.organizations.find((item) => item.organization.id === savedOrganization)
          ?? current.organizations[0]
        setOrganizationId(selected?.organization.id ?? '')
        if (!selected) {
          setAuthError('This account has no active organization membership.')
          localStorage.removeItem(TOKEN_KEY)
          setToken(null)
        } else {
          setAuthError('')
        }
      })
      .catch((error: unknown) => {
        if (!active) return
        localStorage.removeItem(TOKEN_KEY)
        setSession(null)
        setOrganizationId('')
        setToken(null)
        setAuthError(messageFromError(error))
      })
      .finally(() => {
        if (active) setAuthLoading(false)
      })

    return () => {
      active = false
    }
  }, [token])

  const handleAuthenticated = (auth: AuthResponse) => {
    localStorage.setItem(TOKEN_KEY, auth.access_token)
    if (auth.organization) localStorage.setItem('carbontrace.organization_id', auth.organization.id)
    setAuthLoading(true)
    setToken(auth.access_token)
  }

  const signOut = () => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem('carbontrace.organization_id')
    setToken(null)
    setSession(null)
    setOrganizationId('')
    setAuthLoading(false)
  }

  if (authLoading) {
    return <div className="loading-screen"><RefreshCw className="spin" size={20} /> Checking your CarbonTrace session…</div>
  }

  if (!token || !session || !organizationId) {
    return <AuthScreen initialError={authError} onAuthenticated={handleAuthenticated} />
  }

  const membership = session.organizations.find((item) => item.organization.id === organizationId)
  const organization = membership?.organization ?? session.organizations[0]?.organization
  if (!organization) {
    return <AuthScreen initialError="This account has no active organization membership." onAuthenticated={handleAuthenticated} />
  }

  return (
    <Dashboard
      token={token}
      user={session.user}
      organization={organization}
      role={membership?.role ?? 'viewer'}
      organizations={session.organizations.map((item) => item.organization)}
      onOrganizationChange={(id) => {
        localStorage.setItem('carbontrace.organization_id', id)
        setOrganizationId(id)
      }}
      onSignOut={signOut}
    />
  )
}

function AuthScreen({
  initialError,
  onAuthenticated,
}: {
  initialError: string
  onAuthenticated: (auth: AuthResponse) => void
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(initialError)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError('')
    const form = new FormData(event.currentTarget)
    const payload: Record<string, string> = {}
    form.forEach((value, key) => {
      const normalized = String(value).trim()
      if (normalized) payload[key] = normalized
    })
    try {
      const auth = await apiRequest<AuthResponse>(`/auth/${mode === 'login' ? 'login' : 'register'}`, {
        method: 'POST',
        body: payload,
      })
      onAuthenticated(auth)
    } catch (requestError) {
      setError(messageFromError(requestError))
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="brand-block auth-brand">
          <div className="brand-mark">CT</div>
          <div>
            <div className="brand-name">CarbonTrace</div>
            <div className="brand-subtitle">Measure. Understand. Act.</div>
          </div>
        </div>
        <p className="eyebrow">{mode === 'login' ? 'Welcome back' : 'Get started'}</p>
        <h1>{mode === 'login' ? 'Sign in to your workspace' : 'Create your workspace'}</h1>
        <p className="auth-copy">Connect to your organization’s verified emissions data and reporting workflows.</p>
        {error && <div className="notice error-notice" role="alert">{error}</div>}
        <form className="form-stack" onSubmit={submit}>
          {mode === 'register' && (
            <>
              <label>
                Your name
                <input name="display_name" autoComplete="name" required maxLength={160} />
              </label>
              <label>
                Organization
                <input name="organization_name" required maxLength={200} />
              </label>
              <label>
                Country code <span className="field-note">optional, 2 letters</span>
                <input name="country_code" minLength={2} maxLength={2} placeholder="IN" />
              </label>
            </>
          )}
          <label>
            Work email
            <input name="email" type="email" autoComplete="email" required />
          </label>
          <label>
            Password
            <input
              name="password"
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              minLength={mode === 'register' ? 12 : undefined}
              required
            />
            {mode === 'register' && <span className="field-note">At least 12 characters with uppercase, lowercase, and a number.</span>}
          </label>
          {mode === 'register' && (
            <label>
              Timezone
              <input name="timezone" defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'} required />
            </label>
          )}
          <button className="primary-button auth-submit" disabled={busy} type="submit">
            {busy ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>
        <button className="text-button" type="button" onClick={() => {
          setError('')
          setMode((current) => current === 'login' ? 'register' : 'login')
        }}>
          {mode === 'login' ? 'New to CarbonTrace? Create an account' : 'Already registered? Sign in'}
        </button>
        <div className="auth-api-hint">API: {API_BASE_URL}/api/v1</div>
      </section>
    </main>
  )
}

function Dashboard({
  token,
  user,
  organization,
  role,
  organizations,
  onOrganizationChange,
  onSignOut,
}: {
  token: string
  user: CurrentUser['user']
  organization: Organization
  role: string
  organizations: Organization[]
  onOrganizationChange: (id: string) => void
  onSignOut: () => void
}) {
  const [dateRange, setDateRange] = useState(currentYearRange)
  const [data, setData] = useState<DashboardData | null>(null)
  const [benchmarking, setBenchmarking] = useState<BenchmarkCountries | null>(null)
  const [benchmarkSeries, setBenchmarkSeries] = useState<BenchmarkSeries | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)
  const [error, setError] = useState('')
  const [benchmarkError, setBenchmarkError] = useState('')
  const [actionMessage, setActionMessage] = useState('')
  const [actionError, setActionError] = useState('')
  const [busyAction, setBusyAction] = useState('')
  const [selectedBenchmark, setSelectedBenchmark] = useState('')
  const [periodStart, setPeriodStart] = useState(currentYearRange()[0])
  const [periodEnd, setPeriodEnd] = useState(currentYearRange()[1])
  const [factorCategory, setFactorCategory] = useState('electricity')
  const [assistantQuestion, setAssistantQuestion] = useState('')
  const [assistantResult, setAssistantResult] = useState<AssistantResult | null>(null)
  const [assistantError, setAssistantError] = useState('')
  const [assistantBusy, setAssistantBusy] = useState(false)
  const [knowledgeDocuments, setKnowledgeDocuments] = useState<KnowledgeDocument[]>([])
  const [knowledgeError, setKnowledgeError] = useState('')
  const [knowledgeBusy, setKnowledgeBusy] = useState(false)
  const canWrite = ['owner', 'admin', 'analyst'].includes(role)

  useEffect(() => {
    let active = true
    const query = organizationQuery(organization.id, { start: dateRange[0], end: dateRange[1] })

    Promise.all([
      apiRequest<AnalyticsOverview>(`/analytics/overview?${query}`, { token }),
      apiRequest<{ items: TrendPoint[] }>(`/analytics/emissions-trend?${query}`, { token }),
      apiRequest<{ items: ScopeTotal[] }>(`/analytics/scope-breakdown?${query}`, { token }),
      apiRequest<{ items: CategoryTotal[] }>(`/analytics/category-breakdown?${query}`, { token }),
      apiRequest<Page<ActivityRecord>>(`/activities?${organizationQuery(organization.id, { page_size: '50' })}`, { token }),
      apiRequest<EmissionFactor[]>(`/emission-factors?${organizationQuery(organization.id)}`, { token }),
      apiRequest<Page<IngestionRun>>(`/ingestion/history?${organizationQuery(organization.id, { page_size: '20' })}`, { token }),
      apiRequest<Page<PipelineRun>>(`/pipeline/runs?${organizationQuery(organization.id, { page_size: '20' })}`, { token }),
      apiRequest<Insight[]>(`/intelligence/insights?${organizationQuery(organization.id, { page_size: '10' })}`, { token }),
      apiRequest<Anomaly[]>(`/intelligence/anomalies?${organizationQuery(organization.id, { page_size: '10' })}`, { token }),
      apiRequest<Page<Report>>(`/reports?${organizationQuery(organization.id, { page_size: '10' })}`, { token }),
    ])
      .then(([overview, trend, scopes, categories, activities, factors, ingestions, pipelines, insights, anomalies, reports]) => {
        if (!active) return
        setError('')
        setData({
          overview,
          trend: trend.items,
          scopes: scopes.items,
          categories: categories.items,
          activities: activities.items,
          factors,
          ingestions: ingestions.items,
          pipelines: pipelines.items,
          insights,
          anomalies,
          reports: reports.items,
        })
      })
      .catch((requestError: unknown) => {
        if (active) setError(messageFromError(requestError))
      })
      .finally(() => {
        if (active) setLoading(false)
      })

    apiRequest<BenchmarkCountries>(`/benchmarking/countries?${organizationQuery(organization.id)}`, { token })
      .then((result) => {
        if (active) {
          setBenchmarkError('')
          setBenchmarking(result)
          setSelectedBenchmark((current) => result.items.some((item) => item.iso_code === current)
            ? current
            : result.items[0]?.iso_code ?? '')
        }
      })
      .catch((requestError: unknown) => {
        if (active) setBenchmarkError(messageFromError(requestError))
      })

    apiRequest<{ items: KnowledgeDocument[] }>(
      `/knowledge/documents?${organizationQuery(organization.id)}`,
      { token },
    )
      .then((result) => {
        if (active) {
          setKnowledgeDocuments(result.items)
          setKnowledgeError('')
        }
      })
      .catch((requestError: unknown) => {
        if (active) setKnowledgeError(messageFromError(requestError))
      })

    return () => {
      active = false
    }
  }, [dateRange, organization.id, refreshKey, token])

  useEffect(() => {
    if (!selectedBenchmark || !benchmarking) return
    let active = true
    apiRequest<BenchmarkSeries>(
      `/benchmarking/${encodeURIComponent(selectedBenchmark)}/trends?${organizationQuery(organization.id)}`,
      { token },
    )
      .then((series) => {
        if (active) setBenchmarkSeries(series)
      })
      .catch((requestError: unknown) => {
        if (active) setBenchmarkError(messageFromError(requestError))
      })
    return () => {
      active = false
    }
  }, [benchmarking, organization.id, selectedBenchmark, token])

  const scopeChartData = useMemo(() => (data?.scopes ?? []).map((item) => ({
    name: `Scope ${item.scope}`,
    value: Math.max(0, item.kg_co2e),
    color: SCOPE_COLORS[item.scope - 1] ?? '#8b5cf6',
  })), [data?.scopes])

  const categoryChartData = useMemo(() => (data?.categories ?? [])
    .map((item) => ({ name: item.category, value: item.kg_co2e / 1000 }))
    .slice(0, 6), [data?.categories])

  const latestPipeline = data?.pipelines[0]
  const completeIngestions = data?.ingestions.filter((run) => ['succeeded', 'partial'].includes(run.status)) ?? []
  const selectedBenchmarkCountry = benchmarking?.items.find((item) => item.iso_code === selectedBenchmark)

  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canWrite) return
    const formElement = event.currentTarget
    const formData = new FormData(formElement)
    formData.set('organization_id', organization.id)
    formData.set('idempotency_key', crypto.randomUUID())
    setBusyAction('upload')
    setActionMessage('')
    setActionError('')
    try {
      await apiRequest<IngestionRun>('/ingestion/upload', { method: 'POST', token, body: formData })
      setActionMessage('Upload accepted. The ingestion history has been refreshed.')
      formElement.reset()
      setLoading(true)
      setRefreshKey((current) => current + 1)
    } catch (requestError) {
      setActionError(messageFromError(requestError))
    } finally {
      setBusyAction('')
    }
  }

  async function reviewActivity(activityId: string, decision: 'accept' | 'reject', form?: HTMLFormElement) {
    if (!canWrite) return
    setBusyAction(`review:${activityId}`)
    setActionMessage('')
    setActionError('')
    try {
      const body: Record<string, unknown> = { decision }
      if (decision === 'accept' && form) {
        const values = new FormData(form)
        const quantity = Number(values.get('quantity'))
        if (!Number.isFinite(quantity)) throw new Error('Enter a finite activity quantity.')
        body.activity_category = String(values.get('activity_category') ?? '')
        body.activity_date = String(values.get('activity_date') ?? '')
        body.quantity = quantity
        body.unit = String(values.get('unit') ?? '')
        const countryCode = String(values.get('country_code') ?? '').trim()
        body.country_code = countryCode || null
      }
      const query = new URLSearchParams({ organization_id: organization.id })
      await apiRequest<ActivityRecord>(`/activities/${activityId}/review?${query}`, {
        method: 'PATCH',
        token,
        body,
      })
      setActionMessage(decision === 'accept' ? 'Activity reviewed and accepted.' : 'Activity explicitly rejected.')
      setLoading(true)
      setRefreshKey((current) => current + 1)
    } catch (requestError) {
      setActionError(messageFromError(requestError))
    } finally {
      setBusyAction('')
    }
  }

  async function createEmissionFactor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!['owner', 'admin'].includes(role)) return
    const form = event.currentTarget
    const values = new FormData(form)
    const category = String(values.get('activity_category'))
    const categoryInfo = ACTIVITY_OPTIONS.find((item) => item.value === category)
    if (!categoryInfo) {
      setActionError('Choose a supported activity category.')
      return
    }
    const geographyType = String(values.get('geography_type'))
    const countryCode = String(values.get('country_code') ?? '').trim().toUpperCase()
    if (geographyType === 'country' && !/^[A-Z]{2}$/.test(countryCode)) {
      setActionError('Country-specific factors require a two-letter ISO country code.')
      return
    }
    setBusyAction('factor')
    setActionMessage('')
    setActionError('')
    try {
      await apiRequest<EmissionFactor>(`/emission-factors?${new URLSearchParams({ organization_id: organization.id })}`, {
        method: 'POST',
        token,
        body: {
          factor_key: String(values.get('factor_key')),
          activity_category: category,
          scope: categoryInfo.scope,
          geography_type: geographyType,
          country_code: geographyType === 'country' ? countryCode : null,
          activity_unit: String(values.get('activity_unit')),
          factor_value: Number(values.get('factor_value')),
          factor_unit: `kg CO2e/${String(values.get('activity_unit'))}`,
          method: categoryInfo.method,
          valid_from: String(values.get('valid_from')),
          valid_to: String(values.get('valid_to') || '') || null,
          source_name: String(values.get('source_name')),
          source_url: String(values.get('source_url')),
          publication_version: String(values.get('publication_version')),
          methodology: String(values.get('methodology')),
          quality_metadata: null,
          is_illustrative: false,
        },
      })
      setActionMessage('Emission factor added and audit logged. Verify its source and applicability before running calculations.')
      form.reset()
      setFactorCategory('electricity')
      setLoading(true)
      setRefreshKey((current) => current + 1)
    } catch (requestError) {
      setActionError(messageFromError(requestError))
    } finally {
      setBusyAction('')
    }
  }

  async function deactivateEmissionFactor(factorId: string) {
    if (!['owner', 'admin'].includes(role)) return
    setBusyAction(`deactivate:${factorId}`)
    setActionError('')
    setActionMessage('')
    try {
      const query = new URLSearchParams({ organization_id: organization.id })
      await apiRequest<EmissionFactor>(`/emission-factors/${factorId}/deactivate?${query}`, { method: 'POST', token })
      setActionMessage('Emission factor deactivated.')
      setLoading(true)
      setRefreshKey((current) => current + 1)
    } catch (requestError) {
      setActionError(messageFromError(requestError))
    } finally {
      setBusyAction('')
    }
  }

  async function refreshBenchmarkData() {
    if (!['owner', 'admin'].includes(role)) return
    setBusyAction('benchmark')
    setActionError('')
    setActionMessage('')
    try {
      await apiRequest(`/benchmarking/refresh?${new URLSearchParams({ organization_id: organization.id })}`, {
        method: 'POST',
        token,
      })
      setActionMessage('OWID benchmark dataset refreshed with source and hash metadata.')
      setLoading(true)
      setRefreshKey((current) => current + 1)
    } catch (requestError) {
      setActionError(messageFromError(requestError))
    } finally {
      setBusyAction('')
    }
  }

  async function askAssistant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const question = assistantQuestion.trim()
    if (!question) return
    setAssistantBusy(true)
    setAssistantError('')
    setAssistantResult(null)
    try {
      const answer = await apiRequest<AssistantAnswer>('/intelligence/ask', {
        method: 'POST',
        token,
        body: {
          organization_id: organization.id,
          question,
          period_start: dateRange[0],
          period_end: dateRange[1],
        },
      })
      setAssistantResult({ kind: 'answer', answer })
    } catch (requestError) {
      setAssistantError(messageFromError(requestError))
    } finally {
      setAssistantBusy(false)
    }
  }

  async function requestRecommendations() {
    setAssistantBusy(true)
    setAssistantError('')
    setAssistantResult(null)
    try {
      const answer = await apiRequest<RecommendationResponse>('/intelligence/recommendations', {
        method: 'POST',
        token,
        body: {
          organization_id: organization.id,
          period_start: dateRange[0],
          period_end: dateRange[1],
        },
      })
      setAssistantResult({ kind: 'recommendations', answer })
    } catch (requestError) {
      setAssistantError(messageFromError(requestError))
    } finally {
      setAssistantBusy(false)
    }
  }

  async function uploadKnowledgeDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!['owner', 'admin'].includes(role)) return
    const form = event.currentTarget
    const formData = new FormData(form)
    formData.set('organization_id', organization.id)
    setKnowledgeBusy(true)
    setKnowledgeError('')
    try {
      const document = await apiRequest<KnowledgeDocument>('/knowledge/documents', {
        method: 'POST',
        token,
        body: formData,
      })
      setKnowledgeDocuments((current) => [document, ...current])
      form.reset()
    } catch (requestError) {
      setKnowledgeError(messageFromError(requestError))
    } finally {
      setKnowledgeBusy(false)
    }
  }

  async function deleteKnowledgeDocument(documentId: string) {
    if (!['owner', 'admin'].includes(role)) return
    setKnowledgeBusy(true)
    setKnowledgeError('')
    try {
      await apiRequest<void>(
        `/knowledge/documents/${documentId}?${organizationQuery(organization.id)}`,
        { method: 'DELETE', token },
      )
      setKnowledgeDocuments((current) => current.filter((document) => document.id !== documentId))
    } catch (requestError) {
      setKnowledgeError(messageFromError(requestError))
    } finally {
      setKnowledgeBusy(false)
    }
  }

  async function runPipeline(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canWrite) return
    const formData = new FormData(event.currentTarget)
    setBusyAction('pipeline')
    setActionMessage('')
    setActionError('')
    try {
      await apiRequest('/pipeline/run', {
        method: 'POST',
        token,
        body: {
          organization_id: organization.id,
          ingestion_run_id: String(formData.get('ingestion_run_id')),
          reporting_period_start: periodStart,
          reporting_period_end: periodEnd,
          generate_report: false,
          allow_illustrative: false,
          idempotency_key: crypto.randomUUID(),
        },
      })
      setActionMessage('Pipeline run started. Refresh the dashboard to see its latest status.')
      setLoading(true)
      setRefreshKey((current) => current + 1)
    } catch (requestError) {
      setActionError(messageFromError(requestError))
    } finally {
      setBusyAction('')
    }
  }

  async function generateReport(pipeline: PipelineRun) {
    if (!canWrite) return
    setBusyAction(`report:${pipeline.id}`)
    setActionMessage('')
    setActionError('')
    try {
      await apiRequest<Report>('/reports/generate', {
        method: 'POST',
        token,
        body: {
          organization_id: organization.id,
          pipeline_run_id: pipeline.id,
          report_type: 'custom',
          period_start: pipeline.reporting_period_start,
          period_end: pipeline.reporting_period_end,
        },
      })
      setActionMessage('Report generated from the selected pipeline run.')
      setLoading(true)
      setRefreshKey((current) => current + 1)
    } catch (requestError) {
      setActionError(messageFromError(requestError))
    } finally {
      setBusyAction('')
    }
  }

  async function download(report: Report) {
    setBusyAction(`download:${report.id}`)
    setActionError('')
    try {
      const blob = await downloadReport(report.id, organization.id, token)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `carbontrace-${report.id}.pdf`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (requestError) {
      setActionError(messageFromError(requestError))
    } finally {
      setBusyAction('')
    }
  }

  function applyDateRange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (periodEnd < periodStart) {
      setActionError('The period end date must be on or after the start date.')
      return
    }
    setActionError('')
    setLoading(true)
    setDateRange([periodStart, periodEnd])
  }

  if (loading && !data) {
    return <div className="loading-screen"><RefreshCw className="spin" size={20} /> Loading organization data…</div>
  }

  const overview = data?.overview
  const totalTonnes = overview?.total_tonnes_co2e ?? 0
  const metrics = [
    { label: 'Total emissions', value: overview?.has_data ? formatTonnes(overview.total_kg_co2e) : 'No data', hint: overview?.has_data ? `${overview.calculation_count} calculated activities` : 'No persisted calculations for this period', icon: Leaf },
    { label: 'Scope 1', value: overview?.has_data ? formatTonnes(overview.scope_1_kg_co2e) : '—', hint: 'Direct emissions', icon: Gauge },
    { label: 'Scope 2', value: overview?.has_data ? formatTonnes(overview.scope_2_kg_co2e) : '—', hint: 'Purchased energy', icon: Activity },
    { label: 'Data quality', value: overview?.data_quality_score == null ? '—' : `${formatNumber(overview.data_quality_score)}%`, hint: 'Latest ingestion assessment', icon: ShieldCheck },
  ]

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark">CT</div>
          <div>
            <div className="brand-name">CarbonTrace</div>
            <div className="brand-subtitle">Operations view</div>
          </div>
        </div>
        <div className="organization-picker">
          <label htmlFor="organization-select">Organization</label>
          <select
            id="organization-select"
            value={organization.id}
            onChange={(event) => {
              setLoading(true)
              setData(null)
              setBenchmarking(null)
              setBenchmarkSeries(null)
              setBenchmarkError('')
              setAssistantResult(null)
              setAssistantError('')
              setKnowledgeDocuments([])
              setKnowledgeError('')
              onOrganizationChange(event.target.value)
            }}
          >
            {organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </div>
        <nav className="nav">
          <a className="nav-item active" href="#dashboard"><BarChart3 size={18} />Dashboard</a>
          <a className="nav-item" href="#ingestion"><UploadCloud size={18} />Ingestion</a>
          <a className="nav-item" href="#review"><ShieldCheck size={18} />Data review</a>
          <a className="nav-item" href="#factors"><Leaf size={18} />Emission factors</a>
          <a className="nav-item" href="#analytics"><Activity size={18} />Analytics</a>
          <a className="nav-item" href="#reports"><FileText size={18} />Reports</a>
          <a className="nav-item" href="#benchmarking"><Building2 size={18} />Benchmarking</a>
          <a className="nav-item" href="#insights"><Sparkles size={18} />Insights</a>
          <a className="nav-item" href="#assistant"><Sparkles size={18} />AI assistant</a>
        </nav>
        <div className="sidebar-card">
          <div className="tiny-label">Latest pipeline</div>
          <div className="status-row">
            <span className={`status-dot ${latestPipeline?.status === 'failed' ? 'failure' : ''}`}></span>
            {latestPipeline ? latestPipeline.status : 'No runs yet'}
          </div>
          <div className="status-copy">
            {latestPipeline
              ? `${latestPipeline.current_stage ?? 'Run'} · ${formatDate(latestPipeline.created_at)}${latestPipeline.error_summary?.message ? ` · ${String(latestPipeline.error_summary.message)}` : ''}`
              : 'Upload data and run the deterministic calculation pipeline.'}
          </div>
          <div className="user-row">
            <span className="avatar">{user.display_name.slice(0, 1).toUpperCase()}</span>
            <span className="user-name">{user.display_name}</span>
            <button className="icon-button" type="button" aria-label="Sign out" onClick={onSignOut}><LogOut size={16} /></button>
          </div>
        </div>
      </aside>

      <main className="content" id="dashboard">
        <header className="topbar">
          <div>
            <div className="eyebrow">Sustainability control center · {organization.name}</div>
            <h1>Carbon performance overview</h1>
          </div>
          <div className="topbar-actions">
            <button className="ghost-button" type="button" onClick={() => {
              setLoading(true)
              setRefreshKey((current) => current + 1)
            }} disabled={loading}>
              <RefreshCw size={16} className={loading ? 'spin' : ''} /> Refresh
            </button>
            <a className="primary-button button-link" href="#ingestion">Upload activity data</a>
          </div>
        </header>

        {(error || actionError || actionMessage) && (
          <div className={`notice ${error || actionError ? 'error-notice' : 'success-notice'}`} role={error || actionError ? 'alert' : 'status'}>
            {error || actionError || actionMessage}
            {(error || actionError) && <button className="notice-dismiss" type="button" aria-label="Dismiss" onClick={() => {
              setError('')
              setActionError('')
            }}>×</button>}
          </div>
        )}

        <section className="hero-panel">
          <div>
            <div className="eyebrow accent">Measured reporting period</div>
            <h2>{overview?.has_data
              ? `${formatNumber(totalTonnes)} tonnes CO₂e across persisted calculations.`
              : 'Your verified emissions will appear here after the first pipeline run.'}</h2>
            <div className="hero-period">{formatDate(dateRange[0])} — {formatDate(dateRange[1])}</div>
          </div>
          <form className="date-filter" onSubmit={applyDateRange}>
            <label>From<input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} required /></label>
            <label>To<input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} required /></label>
            <button className="ghost-button" type="submit">Apply</button>
          </form>
        </section>

        <section className="metrics-grid">
          {metrics.map(({ label, value, hint, icon: Icon }) => (
            <article className="metric-card" key={label}>
              <div className="metric-header">
                <div><div className="metric-label">{label}</div><div className="metric-value">{value}</div></div>
                <div className="metric-icon"><Icon size={18} /></div>
              </div>
              <div className="metric-footer"><span className="metric-trend">{hint}</span></div>
            </article>
          ))}
        </section>

        <section className="panel-grid">
          <article className="panel large-panel" id="analytics">
            <div className="panel-header">
              <div><div className="tiny-label">Persisted calculation results</div><h3>Monthly carbon output</h3></div>
              <span className="unit-label">kg CO₂e</span>
            </div>
            {data?.trend.length ? (
              <div className="chart-wrap">
                <ResponsiveContainer width="100%" height={280}>
                  <AreaChart data={data.trend} margin={{ top: 10, right: 10, left: -18, bottom: 0 }}>
                    <defs><linearGradient id="fillCarbon" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#3ddc97" stopOpacity={0.45} /><stop offset="100%" stopColor="#3ddc97" stopOpacity={0.02} /></linearGradient></defs>
                    <CartesianGrid stroke="#1f2a2f" strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="month" stroke="#8ca2a8" tickLine={false} axisLine={false} />
                    <YAxis stroke="#8ca2a8" tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ background: '#0d1518', border: '1px solid #233338', borderRadius: 12 }} formatter={(value) => [`${formatNumber(Number(value))} kg CO₂e`, 'Emissions']} />
                    <Area type="monotone" dataKey="kg_co2e" name="Emissions" stroke="#3ddc97" strokeWidth={3} fill="url(#fillCarbon)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : <EmptyState text={loading ? 'Refreshing measured trend…' : 'No monthly calculation results in this period.'} />}
          </article>

          <article className="panel compact-panel">
            <div className="panel-header"><div><div className="tiny-label">Calculated totals</div><h3>Scope allocation</h3></div></div>
            {scopeChartData.some((item) => item.value > 0) ? (
              <>
                <div className="chart-wrap small">
                  <ResponsiveContainer width="100%" height={220}>
                    <PieChart>
                      <Pie data={scopeChartData} dataKey="value" nameKey="name" innerRadius={42} outerRadius={80} paddingAngle={3}>
                        {scopeChartData.map((entry) => <Cell key={entry.name} fill={entry.color} />)}
                      </Pie>
                      <Tooltip contentStyle={{ background: '#0d1518', border: '1px solid #233338', borderRadius: 12 }} formatter={(value) => [`${formatNumber(Number(value))} kg CO₂e`, 'Emissions']} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="legend-list">
                  {scopeChartData.map((entry) => (
                    <div className="legend-item" key={entry.name}>
                      <span className="legend-swatch" style={{ background: entry.color }} /><span>{entry.name}</span><strong>{formatTonnes(entry.value)}</strong>
                    </div>
                  ))}
                </div>
              </>
            ) : <EmptyState text="Scope totals will appear after successful calculations." />}
          </article>
        </section>

        <section className="panel-grid" id="assistant">
          <article className="panel ai-panel">
            <div className="panel-header">
              <div><div className="tiny-label">Gemini · organization-scoped analytics · RAG</div><h3>Sustainability assistant</h3></div>
              <Sparkles size={19} />
            </div>
            <p className="assistant-copy">
              Ask about the selected reporting period or request evidence-linked suggestions. Emissions remain
              calculated by CarbonTrace; Gemini explains persisted results and cites retrieved guidance.
            </p>
            <form className="assistant-form" onSubmit={(event) => void askAssistant(event)}>
              <label htmlFor="assistant-question">Your question</label>
              <textarea
                id="assistant-question"
                value={assistantQuestion}
                onChange={(event) => setAssistantQuestion(event.target.value)}
                placeholder="Which emission category contributes the most, and what evidence supports it?"
                maxLength={4000}
                rows={3}
                required
              />
              <div className="assistant-actions">
                <button className="primary-button" type="submit" disabled={assistantBusy}>
                  {assistantBusy ? 'Working…' : 'Ask CarbonTrace'}
                </button>
                <button className="ghost-button" type="button" onClick={() => void requestRecommendations()} disabled={assistantBusy}>
                  Generate recommendations
                </button>
              </div>
            </form>
            {assistantError && <div className="notice error-notice" role="alert">{assistantError}</div>}
            {assistantBusy && <div className="assistant-loading"><RefreshCw className="spin" size={16} /> Grounding response in persisted evidence…</div>}
            {assistantResult && (
              <div className="assistant-result" aria-live="polite">
                <div className="assistant-period">
                  Period: {dateRange[0]} — {dateRange[1]} · {assistantResult.answer.provider} / {assistantResult.answer.model}
                </div>
                {assistantResult.kind === 'answer' ? (
                  <div className="assistant-answer">{assistantResult.answer.answer}</div>
                ) : assistantResult.answer.recommendations.length ? (
                  <div className="recommendation-list">
                    {assistantResult.answer.recommendations.map((item: AIRecommendation) => (
                      <article className="recommendation-card" key={item.title}>
                        <h4>{item.title}</h4>
                        <p><strong>Verified finding:</strong> {item.verified_finding}</p>
                        <p><strong>General suggestion:</strong> {item.suggested_action}</p>
                        <p>{item.rationale}</p>
                      </article>
                    ))}
                  </div>
                ) : <EmptyState text="No evidence-supported recommendations are available for this period." />}
                <SourceReferences citations={assistantResult.answer.citations} />
                {assistantResult.answer.limitations.length > 0 && (
                  <div className="assistant-limitations">
                    <strong>Limitations</strong>
                    <ul>{assistantResult.answer.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
                  </div>
                )}
              </div>
            )}

            <div className="knowledge-manager">
              <div className="panel-header">
                <div><div className="tiny-label">Organization-only source corpus</div><h4>Sustainability documents</h4></div>
                <span className="unit-label">{knowledgeDocuments.filter((item) => item.status === 'active').length} active</span>
              </div>
              <p className="benchmark-note">Upload authorized GHG Protocol or other sustainability guidance (PDF, TXT, or Markdown). Sources are isolated to this organization.</p>
              {['owner', 'admin'].includes(role) && (
                <form className="knowledge-form" onSubmit={(event) => void uploadKnowledgeDocument(event)}>
                  <label>
                    Document
                    <input type="file" name="file" accept=".pdf,.txt,.md" required />
                  </label>
                  <label>
                    Source title
                    <input name="source_title" maxLength={240} placeholder="Optional; defaults to filename" />
                  </label>
                  <label>
                    Public source URL
                    <input name="source_url" type="url" maxLength={2000} placeholder="https://…" />
                  </label>
                  <button className="small-action" type="submit" disabled={knowledgeBusy}>
                    {knowledgeBusy ? 'Indexing…' : 'Upload and index'}
                  </button>
                </form>
              )}
              {knowledgeError && <div className="inline-error" role="alert">{knowledgeError}</div>}
              {knowledgeDocuments.length ? (
                <div className="knowledge-list">
                  {knowledgeDocuments.map((document) => (
                    <div className="knowledge-row" key={document.id}>
                      <div>
                        <div className="stack-title">{document.title}</div>
                        <div className="stack-meta">{document.filename} · {document.status} · {document.chunk_count} chunks</div>
                        {document.error_message && <div className="inline-error">{document.error_message}</div>}
                      </div>
                      {['owner', 'admin'].includes(role) && document.status === 'active' && (
                        <button className="small-action" type="button" disabled={knowledgeBusy} onClick={() => void deleteKnowledgeDocument(document.id)}>Remove</button>
                      )}
                    </div>
                  ))}
                </div>
              ) : <EmptyState text="No sustainability documents have been added to this organization yet." />}
            </div>
          </article>
        </section>

        <section className="panel-grid">
          <article className="panel" id="review">
            <div className="panel-header">
              <div><div className="tiny-label">Explicit validation decisions</div><h3>Activity review queue</h3></div>
              <span className="unit-label">{data?.activities.length ?? 0} pending</span>
            </div>
            {data?.activities.length ? (
              <div className="review-list">
                {data.activities.map((activity) => (
                  <form
                    className="review-card"
                    key={activity.id}
                    onSubmit={(event) => {
                      event.preventDefault()
                      void reviewActivity(activity.id, 'accept', event.currentTarget)
                    }}
                  >
                    <div className="review-heading">
                      <div>
                        <div className="stack-title">Source row {activity.source_record_key ?? '—'}</div>
                        <div className="stack-meta">{activity.activity_category ?? 'Unknown category'} · {activity.validation_status}</div>
                      </div>
                      <span className="status-pill review">Review required</span>
                    </div>
                    <div className="issue-list">
                      {activity.validation_issues.map((issue, index) => (
                        <div className="validation-issue" key={`${issue.code ?? 'issue'}-${index}`}>
                          <strong>{issue.field ?? 'record'}:</strong> {issue.message ?? issue.code ?? 'Needs review'}
                        </div>
                      ))}
                    </div>
                    <div className="review-fields">
                      <label>Activity category
                        <select name="activity_category" defaultValue={activity.activity_category ?? ''} required>
                          <option value="" disabled>Choose category</option>
                          {ACTIVITY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.value}</option>)}
                        </select>
                      </label>
                      <label>Activity date
                        <input name="activity_date" type="date" defaultValue={activity.activity_date ?? ''} required />
                      </label>
                      <label>Quantity
                        <input name="quantity" type="number" step="any" min="0" defaultValue={activity.quantity ?? ''} required />
                      </label>
                      <label>Unit
                        <select name="unit" defaultValue={activity.unit ?? ''} required>
                          <option value="" disabled>Choose unit</option>
                          {[...new Set(ACTIVITY_OPTIONS.flatMap((item) => item.units))].map((unit) => <option key={unit} value={unit}>{unit}</option>)}
                        </select>
                      </label>
                      <label>Country code
                        <input name="country_code" minLength={2} maxLength={2} defaultValue={activity.country_code ?? ''} placeholder="Optional" />
                      </label>
                    </div>
                    <details className="raw-row">
                      <summary>View original source row (preserved)</summary>
                      <pre>{JSON.stringify(activity.raw_payload, null, 2)}</pre>
                    </details>
                    <div className="review-actions">
                      <button className="primary-button" type="submit" disabled={!canWrite || busyAction === `review:${activity.id}`}>
                        {busyAction === `review:${activity.id}` ? 'Saving…' : 'Accept corrected row'}
                      </button>
                      <button className="danger-button" type="button" disabled={!canWrite || busyAction === `review:${activity.id}`} onClick={() => void reviewActivity(activity.id, 'reject')}>
                        Reject row
                      </button>
                    </div>
                  </form>
                ))}
              </div>
            ) : <EmptyState text="No rows currently need review." />}
          </article>

          <article className="panel" id="factors">
            <div className="panel-header">
              <div><div className="tiny-label">Auditable factor registry</div><h3>Emission factors</h3></div>
              <span className="unit-label">{data?.factors.length ?? 0} available</span>
            </div>
            <p className="benchmark-note">Enter a factor only from a verified published source. CarbonTrace does not provide authoritative factor values by default.</p>
            {['owner', 'admin'].includes(role) && (
              <form className="factor-form" onSubmit={(event) => void createEmissionFactor(event)}>
                <label>Factor key<input name="factor_key" maxLength={120} required placeholder="source-year-category-region" /></label>
                <label>Activity category
                  <select name="activity_category" required value={factorCategory} onChange={(event) => setFactorCategory(event.target.value)}>
                    {ACTIVITY_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.value} · Scope {item.scope}</option>)}
                  </select>
                </label>
                <label>Geography
                  <select name="geography_type" defaultValue="country" required>
                    <option value="country">Country-specific</option>
                    <option value="global">Global</option>
                  </select>
                </label>
                <label>Country code<input name="country_code" minLength={2} maxLength={2} placeholder="IN" /></label>
                <label>Factor value<input name="factor_value" type="number" min="0" step="any" required /></label>
                <label>Factor activity unit
                  <select name="activity_unit" required key={factorCategory} defaultValue={ACTIVITY_OPTIONS.find((item) => item.value === factorCategory)?.units[0]}>
                    {ACTIVITY_OPTIONS.find((item) => item.value === factorCategory)?.units.map((unit) => <option key={unit} value={unit}>{unit}</option>)}
                  </select>
                </label>
                <label>Valid from<input name="valid_from" type="date" defaultValue={currentYearRange()[0]} required /></label>
                <label>Valid to <span className="field-note">optional</span><input name="valid_to" type="date" /></label>
                <label>Source name<input name="source_name" required /></label>
                <label>Source URL<input name="source_url" type="url" required placeholder="https://…" /></label>
                <label>Publication/version<input name="publication_version" maxLength={120} required /></label>
                <label className="wide-field">Methodology<textarea name="methodology" required rows={3} /></label>
                <button className="primary-button wide-field" type="submit" disabled={busyAction === 'factor'}>
                  {busyAction === 'factor' ? 'Saving…' : 'Add verified factor'}
                </button>
              </form>
            )}
            {!['owner', 'admin'].includes(role) && <div className="empty-state">Only organization owners and admins can manage factors.</div>}
            <div className="factor-list">
              {data?.factors.map((factor) => (
                <div className="factor-row" key={factor.id}>
                  <div>
                    <div className="stack-title">{factor.factor_key} {factor.is_illustrative && <span className="status-pill review">Illustrative</span>}</div>
                    <div className="stack-meta">Scope {factor.scope} · {factor.activity_category} · {factor.geography_type === 'country' ? factor.country_code : 'Global'} · {factor.factor_value} {factor.factor_unit}</div>
                    <div className="stack-meta">{factor.source_name} · {factor.publication_version} · valid from {factor.valid_from}{factor.valid_to ? ` to ${factor.valid_to}` : ''}</div>
                  </div>
                  {factor.organization_id === organization.id && factor.is_active && ['owner', 'admin'].includes(role) && (
                    <button className="danger-button" type="button" disabled={busyAction === `deactivate:${factor.id}`} onClick={() => void deactivateEmissionFactor(factor.id)}>
                      {busyAction === `deactivate:${factor.id}` ? 'Deactivating…' : 'Deactivate'}
                    </button>
                  )}
                </div>
              ))}
              {!data?.factors.length && <EmptyState text="No active shared or organization factors are registered." />}
            </div>
          </article>
        </section>

        <section className="panel-grid">
          <article className="panel" id="ingestion">
            <div className="panel-header"><div><div className="tiny-label">Real upload endpoint</div><h3>Ingest activity data</h3></div></div>
            <form className="upload-form" onSubmit={submitUpload}>
              <label className="file-picker">
                <UploadCloud size={20} />
                <span>Choose a CSV or XLSX file</span>
                <input name="file" type="file" accept=".csv,.xlsx" required />
              </label>
              <button className="primary-button" type="submit" disabled={busyAction === 'upload' || !canWrite}>
                {!canWrite ? 'Analyst access required' : busyAction === 'upload' ? 'Uploading…' : 'Upload for validation'}
              </button>
            </form>
            <a className="source-link template-link" href={`${API_BASE_URL}/api/v1/ingestion/templates/csv`}>
              Download CSV template (contains synthetic example rows) <ArrowUpRight size={14} />
            </a>
            <div className="panel-header list-heading"><div><div className="tiny-label">Recent ingestion history</div></div></div>
            {data?.ingestions.length ? (
              <div className="stack-list">
                {data.ingestions.slice(0, 5).map((run) => (
                  <div className="stack-row" key={run.id}>
                    <div><div className="stack-title">{run.source_name ?? 'Activity upload'}</div><div className="stack-meta">{run.records_accepted} accepted · {run.records_rejected} rejected · {formatDate(run.created_at)}</div></div>
                    <span className={`status-pill ${run.status.toLowerCase()}`}>{run.status}</span>
                  </div>
                ))}
              </div>
            ) : <EmptyState text="No ingestion history yet. Upload a source file to begin." />}
          </article>

          <article className="panel">
            <div className="panel-header"><div><div className="tiny-label">Deterministic calculation</div><h3>Run carbon pipeline</h3></div></div>
            <form className="form-stack compact-form" onSubmit={runPipeline}>
              <label>
                Completed ingestion
                <select name="ingestion_run_id" required disabled={!completeIngestions.length}>
                  {completeIngestions.length
                    ? completeIngestions.map((run) => <option key={run.id} value={run.id}>{run.source_name ?? run.id.slice(0, 8)} · {run.status}</option>)
                    : <option value="">No completed ingestion available</option>}
                </select>
              </label>
              <button className="primary-button" type="submit" disabled={busyAction === 'pipeline' || !completeIngestions.length || !canWrite}>
                {!canWrite ? 'Analyst access required' : busyAction === 'pipeline' ? 'Starting…' : 'Start pipeline'}
              </button>
            </form>
            <div className="panel-header list-heading"><div><div className="tiny-label">Pipeline history</div></div></div>
            {data?.pipelines.length ? (
              <div className="stack-list">
                {data.pipelines.slice(0, 5).map((run) => (
                  <div className="stack-row" key={run.id}>
                    <div><div className="stack-title">{run.reporting_period_start} — {run.reporting_period_end}</div><div className="stack-meta">{run.current_stage ?? 'Pipeline'} · {formatDate(run.created_at)}</div></div>
                    <div className="stack-status-group">
                      <span className={`status-pill ${run.status.toLowerCase()}`}>{run.status}</span>
                      {canWrite && ['succeeded', 'partial'].includes(run.status) && (
                        <button className="small-action" type="button" disabled={busyAction === `report:${run.id}`} onClick={() => void generateReport(run)}>
                          {busyAction === `report:${run.id}` ? 'Generating…' : 'Report'}
                        </button>
                      )}
                    </div>
                    {typeof run.error_summary?.message === 'string' && (
                      <div className="pipeline-error">{run.error_summary.message}</div>
                    )}
                  </div>
                ))}
              </div>
            ) : <EmptyState text="No pipeline runs have been recorded yet." />}
          </article>
        </section>

        <section className="panel-grid">
          <article className="panel">
            <div className="panel-header"><div><div className="tiny-label">Measured category totals</div><h3>Top contributors</h3></div></div>
            {categoryChartData.length ? (
              <div className="chart-wrap small-bars">
                <ResponsiveContainer width="100%" height={230}>
                  <BarChart data={categoryChartData} layout="vertical" margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                    <CartesianGrid stroke="#1f2a2f" strokeDasharray="3 3" horizontal={false} />
                    <XAxis type="number" stroke="#8ca2a8" tickLine={false} axisLine={false} />
                    <YAxis type="category" dataKey="name" stroke="#8ca2a8" tickLine={false} axisLine={false} width={110} />
                    <Tooltip contentStyle={{ background: '#0d1518', border: '1px solid #233338', borderRadius: 12 }} formatter={(value) => [`${formatNumber(Number(value))} tCO₂e`, 'Emissions']} />
                    <Bar dataKey="value" name="Emissions" radius={[0, 8, 8, 0]} fill="#7cc6ff" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : <EmptyState text="Category totals will appear after successful calculations." />}
          </article>

          <article className="panel" id="insights">
            <div className="panel-header"><div><div className="tiny-label">Persisted system insights</div><h3>Insights and anomalies</h3></div></div>
            <div className="insight-list">
              {data?.insights.map((insight) => (
                <div className="insight-item" key={insight.id}>
                  <div className="insight-topline"><Sparkles size={15} /><span>{insight.insight_type} · {insight.generation_method}</span></div>
                  <div className="insight-title">{insight.title}</div>
                  <div className="insight-detail">{insight.body}</div>
                  {insight.confidence != null && <div className="confidence-label">Confidence {formatNumber(insight.confidence * 100)}%</div>}
                </div>
              ))}
              {data?.anomalies.map((anomaly) => (
                <div className="insight-item anomaly-item" key={anomaly.id}>
                  <div className="insight-topline"><AlertTriangle size={15} /><span>{anomaly.severity} anomaly · {anomaly.method}</span></div>
                  <div className="insight-title">{anomaly.activity_category} · {formatDate(anomaly.period_start)}</div>
                  <div className="insight-detail">{anomaly.explanation}</div>
                </div>
              ))}
              {!data?.insights.length && !data?.anomalies.length && <EmptyState text="No saved insights or anomalies are available for this organization yet." />}
            </div>
          </article>
        </section>

        <section className="panel-grid bottom-grid">
          <article className="panel" id="reports">
            <div className="panel-header"><div><div className="tiny-label">Generated from persisted pipeline results</div><h3>Reports</h3></div></div>
            {data?.reports.length ? (
              <div className="report-list">
                {data.reports.map((report) => (
                  <div className="report-row" key={report.id}>
                    <div className="report-name-block"><div className="report-name">{report.report_type} report</div><div className="report-subtitle">{report.period_start} — {report.period_end}</div></div>
                    <div className="report-meta"><span>{formatDate(report.created_at)}</span><button className="small-action" type="button" disabled={busyAction === `download:${report.id}`} onClick={() => void download(report)}>{busyAction === `download:${report.id}` ? 'Downloading…' : 'Download PDF'}</button></div>
                  </div>
                ))}
              </div>
            ) : <EmptyState text="No reports generated yet. Generate one from a completed pipeline run." />}
          </article>

          <article className="panel" id="benchmarking">
            <div className="panel-header">
              <div><div className="tiny-label">OWID reference data</div><h3>Benchmark context</h3></div>
              {['owner', 'admin'].includes(role) && (
                <button className="small-action" type="button" disabled={busyAction === 'benchmark'} onClick={() => void refreshBenchmarkData()}>
                  {busyAction === 'benchmark' ? 'Refreshing…' : 'Refresh dataset'}
                </button>
              )}
            </div>
            <p className="benchmark-note">Country-level context only; OWID data is not used as an organizational emission factor.</p>
            {benchmarkError ? <div className="inline-error">{benchmarkError}</div> : null}
            {benchmarking?.items.length ? (
              <>
                <label className="benchmark-select-label">
                  Country
                  <select value={selectedBenchmark} onChange={(event) => setSelectedBenchmark(event.target.value)}>
                    {benchmarking.items.map((item) => <option key={item.iso_code} value={item.iso_code}>{item.country}</option>)}
                  </select>
                </label>
                {benchmarkSeries?.series.length ? (
                  <div className="benchmark-result">
                    <div className="benchmark-country">{selectedBenchmarkCountry?.country ?? benchmarkSeries.country} <span>{benchmarkSeries.iso_code}</span></div>
                    <div className="benchmark-value">
                      {benchmarkSeries.series.at(-1)?.co2_per_capita_tonnes_per_person == null
                        ? 'Latest per-capita value unavailable'
                        : `${formatNumber(benchmarkSeries.series.at(-1)!.co2_per_capita_tonnes_per_person!)} tCO₂ per person`}
                    </div>
                    <div className="benchmark-emissions">Year: {benchmarkSeries.series.at(-1)?.year ?? '—'}</div>
                    <div className="benchmark-emissions">Dataset updated {formatDate(benchmarkSeries.retrieved_at)}</div>
                    <a className="source-link" href={benchmarkSeries.source_repository} target="_blank" rel="noreferrer">View source dataset <ArrowUpRight size={14} /></a>
                  </div>
                ) : <EmptyState text="Loading country benchmark context…" />}
              </>
            ) : !benchmarkError ? <EmptyState text="No cached OWID benchmark dataset is available." /> : null}
          </article>
        </section>

        <footer className="dashboard-footer">
          <span>Reporting values reflect persisted calculation results for the selected period.</span>
          <span>{data?.overview.calculation_count ?? 0} calculation records · OWID last retrieved {formatDate(benchmarking?.retrieved_at)}</span>
        </footer>
      </main>
    </div>
  )
}

function SourceReferences({ citations }: { citations: AssistantCitation[] }) {
  if (!citations.length) return null
  return (
    <div className="source-list">
      <strong>Evidence and sources</strong>
      <ul>
        {citations.map((citation) => (
          <li key={citation.id}>
            {citation.source_url
              ? <a href={citation.source_url} target="_blank" rel="noreferrer">{citation.label}</a>
              : citation.label}
            {citation.page ? ` · page ${citation.page}` : ''}
          </li>
        ))}
      </ul>
    </div>
  )
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>
}

export default App
