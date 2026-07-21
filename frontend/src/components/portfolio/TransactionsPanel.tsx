import { useEffect, useState } from 'react'
import { portfolioLedgerApi, type PortfolioEventType, type PortfolioTransaction } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface Props {
  accounts: Array<{ id: number; name: string }>
  refreshKey: number
  onDividend: () => void
  onCashFlow: (type: 'DEPOSIT' | 'WITHDRAWAL') => void
}

const labels: Record<PortfolioEventType, string> = {
  SELL: '卖出',
  DIVIDEND: '分红',
  DEPOSIT: '入金',
  WITHDRAWAL: '出金',
}

const eventStyles: Record<PortfolioEventType, string> = {
  SELL: 'bg-amber-500/10 text-amber-600',
  DIVIDEND: 'bg-rose-500/10 text-rose-600',
  DEPOSIT: 'bg-blue-500/10 text-blue-600',
  WITHDRAWAL: 'bg-violet-500/10 text-violet-600',
}

const money = (value: number) => new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)
const dateTime = (value: string) => new Date(value).toLocaleString('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
})

export default function TransactionsPanel({ accounts, refreshKey, onDividend, onCashFlow }: Props) {
  const currentYear = new Date().getFullYear()
  const [items, setItems] = useState<PortfolioTransaction[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [accountId, setAccountId] = useState('')
  const [eventType, setEventType] = useState('')
  const [year, setYear] = useState(String(currentYear))

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    portfolioLedgerApi.transactions({
      account_id: accountId ? Number(accountId) : undefined,
      event_type: eventType ? eventType as PortfolioEventType : undefined,
      year: Number(year),
      limit: 200,
    }).then(result => {
      if (cancelled) return
      setItems(result.items)
      setTotal(result.total)
    }).catch(() => {
      if (!cancelled) {
        setItems([])
        setTotal(0)
      }
    }).finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [accountId, eventType, year, refreshKey])

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-border/40 p-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h2 className="text-[15px] font-semibold text-foreground">交易流水</h2>
          <p className="mt-1 text-[11px] text-muted-foreground">卖出、分红和资金变化都会保留在这里，清仓不会删除历史。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={() => onCashFlow('DEPOSIT')}>记录入金</Button>
          <Button variant="secondary" size="sm" onClick={() => onCashFlow('WITHDRAWAL')}>记录出金</Button>
          <Button size="sm" onClick={onDividend}>记录分红</Button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-border/30 bg-accent/10 px-4 py-3">
        <select value={year} onChange={e => setYear(e.target.value)} className="h-8 rounded-md border border-border bg-background px-2 text-[12px]">
          {[0, 1, 2, 3, 4].map(offset => <option key={offset} value={currentYear - offset}>{currentYear - offset} 年</option>)}
        </select>
        <select value={accountId} onChange={e => setAccountId(e.target.value)} className="h-8 rounded-md border border-border bg-background px-2 text-[12px]">
          <option value="">全部账户</option>
          {accounts.map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
        </select>
        <select value={eventType} onChange={e => setEventType(e.target.value)} className="h-8 rounded-md border border-border bg-background px-2 text-[12px]">
          <option value="">全部类型</option>
          {Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <span className="ml-auto text-[11px] text-muted-foreground">共 {total} 条</span>
      </div>

      {loading ? (
        <div className="py-16 text-center text-[12px] text-muted-foreground">加载中…</div>
      ) : items.length === 0 ? (
        <div className="py-16 text-center">
          <div className="text-[13px] text-muted-foreground">暂无交易流水</div>
          <div className="mt-1 text-[11px] text-muted-foreground/70">可以从持仓记录卖出，或在这里补录股票分红。</div>
        </div>
      ) : (
        <>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full">
              <thead>
                <tr className="border-b border-border/30 bg-accent/20">
                  {['日期', '类型', '账户', '股票', '数量 / 价格', '现金变化', '已实现收益', '备注'].map(title => (
                    <th key={title} className="px-4 py-2 text-left text-[11px] font-semibold text-muted-foreground">{title}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map(item => (
                  <tr key={item.id} className="border-t border-border/20 hover:bg-accent/20">
                    <td className="whitespace-nowrap px-4 py-3 text-[12px] text-muted-foreground">{dateTime(item.occurred_at)}</td>
                    <td className="px-4 py-3"><span className={`rounded px-2 py-0.5 text-[10px] ${eventStyles[item.event_type]}`}>{labels[item.event_type]}</span></td>
                    <td className="px-4 py-3 text-[12px] text-foreground">{item.account_name}</td>
                    <td className="px-4 py-3 text-[12px] text-foreground">{item.stock_name || '—'} {item.stock_symbol && <span className="font-mono text-muted-foreground">{item.stock_symbol}</span>}</td>
                    <td className="px-4 py-3 font-mono text-[12px] text-muted-foreground">
                      {item.quantity != null ? `${item.quantity} × ${item.unit_price?.toFixed(3)}` : '—'}
                    </td>
                    <td className={`px-4 py-3 text-right font-mono text-[12px] ${item.cash_delta_base >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                      {item.cash_delta_base >= 0 ? '+' : ''}{money(item.cash_delta_base)}
                    </td>
                    <td className={`px-4 py-3 text-right font-mono text-[12px] ${item.realized_pnl_base >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                      {item.event_type === 'SELL' ? `${item.realized_pnl_base >= 0 ? '+' : ''}${money(item.realized_pnl_base)}` : '—'}
                    </td>
                    <td className="max-w-[220px] truncate px-4 py-3 text-[11px] text-muted-foreground" title={item.note}>{item.note || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="divide-y divide-border/30 md:hidden">
            {items.map(item => (
              <div key={item.id} className="p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className={`rounded px-2 py-0.5 text-[10px] ${eventStyles[item.event_type]}`}>{labels[item.event_type]}</span>
                    <span className="text-[11px] text-muted-foreground">{dateTime(item.occurred_at)}</span>
                  </div>
                  <span className={`font-mono text-[13px] ${item.cash_delta_base >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                    {item.cash_delta_base >= 0 ? '+' : ''}{money(item.cash_delta_base)}
                  </span>
                </div>
                <div className="mt-2 text-[13px] text-foreground">
                  {item.stock_name || item.account_name}{item.stock_symbol ? `（${item.stock_symbol}）` : ''}
                </div>
                <div className="mt-1 text-[11px] text-muted-foreground">
                  {item.account_name}
                  {item.quantity != null ? ` · ${item.quantity} × ${item.unit_price?.toFixed(3)}` : ''}
                  {item.event_type === 'SELL' ? ` · 已实现 ${item.realized_pnl_base >= 0 ? '+' : ''}${money(item.realized_pnl_base)}` : ''}
                </div>
                {item.note && <div className="mt-1 text-[11px] text-muted-foreground/70">{item.note}</div>}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
