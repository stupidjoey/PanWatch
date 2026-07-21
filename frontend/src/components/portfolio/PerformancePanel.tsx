import { useEffect, useMemo, useState } from 'react'
import { portfolioLedgerApi, type PortfolioYearPerformance } from '@panwatch/api'
import { AlertTriangle } from 'lucide-react'

interface Props {
  accounts: Array<{ id: number; name: string }>
  refreshKey: number
}

const money = (value?: number) => new Intl.NumberFormat('zh-CN', {
  style: 'currency', currency: 'CNY', maximumFractionDigits: 2,
}).format(value || 0)

const pct = (value?: number | null) => value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`

export default function PerformancePanel({ accounts, refreshKey }: Props) {
  const currentYear = new Date().getFullYear()
  const [years, setYears] = useState<number[]>([currentYear])
  const [year, setYear] = useState(currentYear)
  const [accountId, setAccountId] = useState('')
  const [data, setData] = useState<PortfolioYearPerformance | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    portfolioLedgerApi.years().then(result => setYears(result.years)).catch(() => {})
  }, [refreshKey])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    portfolioLedgerApi.performance(year, accountId ? Number(accountId) : undefined)
      .then(result => !cancelled && setData(result))
      .catch(() => !cancelled && setData(null))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [year, accountId, refreshKey])

  const chartPoints = useMemo(() => {
    const curve = data?.curve || []
    if (curve.length < 2) return ''
    const values = curve.map(p => p.total_assets)
    const min = Math.min(...values)
    const max = Math.max(...values)
    const span = Math.max(1, max - min)
    return curve.map((point, index) => {
      const x = 4 + index / (curve.length - 1) * 92
      const y = 92 - (point.total_assets - min) / span * 84
      return `${x},${y}`
    }).join(' ')
  }, [data])

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-[15px] font-semibold text-foreground">年度收益</h2>
          <p className="mt-1 text-[11px] text-muted-foreground">实际收益率考虑入金和出金时间；策略收益率用于观察投资表现。</p>
        </div>
        <div className="flex gap-2">
          <select value={year} onChange={e => setYear(Number(e.target.value))} className="h-9 rounded-md border border-border bg-background px-3 text-[12px]">
            {years.map(item => <option key={item} value={item}>{item} 年</option>)}
          </select>
          <select value={accountId} onChange={e => setAccountId(e.target.value)} className="h-9 rounded-md border border-border bg-background px-3 text-[12px]">
            <option value="">全部账户</option>
            {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="card py-20 text-center text-[12px] text-muted-foreground">正在计算收益…</div>
      ) : !data || data.empty ? (
        <div className="card py-20 text-center">
          <div className="text-[13px] text-muted-foreground">{data?.message || '暂无收益数据'}</div>
          <div className="mt-1 text-[11px] text-muted-foreground/70">首次使用时会从当前资产估值建立统计基线。</div>
        </div>
      ) : (
        <>
          {(data.partial_period || !data.valuation_complete) && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{data.message || '部分估值使用了成本价占位，收益结果仅供参考。'}</span>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <MetricCard label="实际收益率" value={pct(data.mwr_pct)} tone={(data.mwr_pct || 0) >= 0 ? 'up' : 'down'} hint="MWR" />
            <MetricCard label="策略收益率" value={pct(data.twr_pct)} tone={(data.twr_pct || 0) >= 0 ? 'up' : 'down'} hint="TWR" />
            <MetricCard label="投资收益" value={money(data.profit)} tone={(data.profit || 0) >= 0 ? 'up' : 'down'} />
            <MetricCard label="分红收入" value={money(data.dividend_income)} />
            <MetricCard label="净入金" value={money(data.net_external_flow)} />
          </div>

          <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
            <div className="card p-4">
              <div className="flex items-center justify-between">
                <h3 className="text-[13px] font-semibold text-foreground">资产变化</h3>
                <span className="text-[10px] text-muted-foreground">{data.period_days || 0} 天 · {data.transaction_count || 0} 笔流水</span>
              </div>
              {chartPoints ? (
                <div className="mt-4 h-52 w-full">
                  <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-full w-full overflow-visible">
                    <defs>
                      <linearGradient id="portfolioArea" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity="0.22" />
                        <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity="0" />
                      </linearGradient>
                    </defs>
                    <polyline points={chartPoints} fill="none" stroke="hsl(var(--primary))" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
                  </svg>
                </div>
              ) : (
                <div className="flex h-52 items-center justify-center text-[11px] text-muted-foreground">积累更多每日估值后将显示资产曲线</div>
              )}
              <div className="flex justify-between border-t border-border/30 pt-3 text-[11px] text-muted-foreground">
                <span>期初 {money(data.start_value)}</span>
                <span>期末 {money(data.end_value)}</span>
              </div>
            </div>

            <div className="card p-4">
              <h3 className="text-[13px] font-semibold text-foreground">收益与资金构成</h3>
              <div className="mt-4 space-y-3">
                <Breakdown label="卖出已实现收益" value={data.realized_pnl || 0} colored />
                <Breakdown label="现金分红" value={data.dividend_income || 0} colored />
                <Breakdown label="期间入金" value={data.deposits || 0} />
                <Breakdown label="期间出金" value={-(data.withdrawals || 0)} />
                <div className="border-t border-border/40 pt-3"><Breakdown label="投资收益合计" value={data.profit || 0} colored strong /></div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function MetricCard({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: 'up' | 'down' }) {
  const color = tone === 'up' ? 'text-rose-500' : tone === 'down' ? 'text-emerald-500' : 'text-foreground'
  return (
    <div className="card p-4">
      <div className="flex items-center gap-1 text-[11px] text-muted-foreground">{label}{hint && <span className="text-[9px] opacity-60">{hint}</span>}</div>
      <div className={`mt-1 font-mono text-[18px] font-bold ${color}`}>{value}</div>
    </div>
  )
}

function Breakdown({ label, value, colored, strong }: { label: string; value: number; colored?: boolean; strong?: boolean }) {
  const color = colored ? value >= 0 ? 'text-rose-500' : 'text-emerald-500' : 'text-foreground'
  return (
    <div className={`flex items-center justify-between text-[12px] ${strong ? 'font-semibold' : ''}`}>
      <span className="text-muted-foreground">{label}</span>
      <span className={`font-mono ${color}`}>{value >= 0 ? '+' : ''}{money(value)}</span>
    </div>
  )
}
