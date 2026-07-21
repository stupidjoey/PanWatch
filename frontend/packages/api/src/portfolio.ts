import { fetchAPI } from './client'

export interface PortfolioDiagnostics {
  position_count: number
  total_market_value: number
  hhi: number
  max_weight: number
  by_market: Record<string, number>
  by_strategy: Record<string, number>
  total_unrealized_pnl: number
  alerts: string[]
}

export interface BenchmarkCurvePoint {
  date: string
  portfolio: number
  benchmark: number
}

export interface PortfolioBenchmark {
  empty?: boolean
  reason?: string
  portfolio_return?: number
  benchmark_return?: number
  excess_return?: number
  information_ratio?: number
  relative_drawdown?: number
  days?: number
  benchmark_code?: string
  benchmark_label?: string
  curve?: BenchmarkCurvePoint[]
}

export const portfolioApi = {
  /** 真实持仓组合诊断(集中度/分布/风险提示)。 */
  diagnostics: () => fetchAPI<PortfolioDiagnostics>('/portfolio/diagnostics'),

  /** 组合 vs 基准(超额/信息比率/相对回撤 + 归一化曲线)。 */
  benchmark: (params?: { days?: number; benchmark?: string }) =>
    fetchAPI<PortfolioBenchmark>(
      `/portfolio/benchmark?days=${params?.days ?? 60}&benchmark=${encodeURIComponent(params?.benchmark ?? '000300')}`,
      { timeoutMs: 40000 },
    ),

  /** 个股对组合收益的贡献(谁拖累/贡献)。 */
  attribution: (days = 60) =>
    fetchAPI<{ items: AttributionItem[] }>(`/portfolio/attribution?days=${days}`, { timeoutMs: 40000 }),

  /** 组合 AI 体检(叙述结论 + 调仓建议)。 */
  aiReview: () => fetchAPI<PortfolioAiReview>('/portfolio/ai-review', { method: 'POST', timeoutMs: 60000 }),
}

export interface AttributionItem {
  symbol: string
  name: string
  market: string
  return_pct: number
  weight_pct: number
  contribution_pct: number
}

export interface PortfolioAiReview {
  empty?: boolean
  reason?: string
  content?: string
  top?: AttributionItem[]
  worst?: AttributionItem[]
}

export type PortfolioEventType = 'SELL' | 'DIVIDEND' | 'DEPOSIT' | 'WITHDRAWAL'

export interface PortfolioTransaction {
  id: number
  account_id: number
  account_name: string
  stock_id: number | null
  event_type: PortfolioEventType
  stock_symbol: string
  stock_name: string
  stock_market: string
  quantity: number | null
  unit_price: number | null
  gross_amount: number
  net_amount: number
  cash_delta_base: number
  cost_basis_base: number
  realized_pnl_base: number
  currency: string
  fx_rate_to_base: number
  occurred_at: string
  recorded_at: string
  note: string
}

export interface PortfolioPerformancePoint {
  date: string
  total_assets: number
  return_index: number
}

export interface PortfolioYearPerformance {
  year: number
  empty: boolean
  reason?: string
  message?: string
  account_id?: number | null
  period_start?: string
  period_end?: string
  period_days?: number
  partial_period?: boolean
  start_value?: number
  end_value?: number
  profit?: number
  deposits?: number
  withdrawals?: number
  net_external_flow?: number
  dividend_income?: number
  realized_pnl?: number
  mwr_pct?: number | null
  mwr_annualized_pct?: number | null
  twr_pct?: number | null
  valuation_complete?: boolean
  missing_price_count?: number
  transaction_count?: number
  curve?: PortfolioPerformancePoint[]
}

export const portfolioLedgerApi = {
  transactions: (params?: {
    account_id?: number
    stock_id?: number
    event_type?: PortfolioEventType
    year?: number
    limit?: number
    offset?: number
  }) => {
    const query = new URLSearchParams()
    if (params?.account_id) query.set('account_id', String(params.account_id))
    if (params?.stock_id) query.set('stock_id', String(params.stock_id))
    if (params?.event_type) query.set('event_type', params.event_type)
    if (params?.year) query.set('year', String(params.year))
    query.set('limit', String(params?.limit ?? 100))
    query.set('offset', String(params?.offset ?? 0))
    return fetchAPI<{ total: number; items: PortfolioTransaction[] }>(`/portfolio/transactions?${query}`)
  },

  sell: (body: {
    position_id: number
    quantity: number
    unit_price: number
    net_amount?: number | null
    currency?: string
    occurred_at?: string
    note?: string
    idempotency_key?: string
  }) => fetchAPI<{ transaction: PortfolioTransaction; remaining_quantity: number; position_closed: boolean }>(
    '/portfolio/transactions/sell',
    { method: 'POST', body: JSON.stringify(body) },
  ),

  dividend: (body: {
    account_id: number
    stock_id: number
    amount: number
    currency?: string
    occurred_at?: string
    note?: string
    idempotency_key?: string
  }) => fetchAPI<{ transaction: PortfolioTransaction }>(
    '/portfolio/transactions/dividend',
    { method: 'POST', body: JSON.stringify(body) },
  ),

  cashFlow: (body: {
    account_id: number
    event_type: 'DEPOSIT' | 'WITHDRAWAL'
    amount: number
    currency?: string
    occurred_at?: string
    note?: string
    idempotency_key?: string
  }) => fetchAPI<{ transaction: PortfolioTransaction }>(
    '/portfolio/transactions/cash-flow',
    { method: 'POST', body: JSON.stringify(body) },
  ),

  years: () => fetchAPI<{ years: number[] }>('/portfolio/performance/years'),

  performance: (year: number, accountId?: number) => {
    const query = new URLSearchParams({ year: String(year) })
    if (accountId) query.set('account_id', String(accountId))
    return fetchAPI<PortfolioYearPerformance>(`/portfolio/performance?${query}`, { timeoutMs: 40000 })
  },
}
