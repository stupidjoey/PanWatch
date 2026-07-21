import { useEffect, useMemo, useState } from 'react'
import { portfolioLedgerApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface AccountOption { id: number; name: string }
interface StockOption { id: number; symbol: string; name: string; market: string }

interface Props {
  open: boolean
  accounts: AccountOption[]
  stocks: StockOption[]
  initialAccountId?: number | null
  initialStockId?: number | null
  onOpenChange: (open: boolean) => void
  onSaved: () => void | Promise<void>
}

const localDate = () => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

const currencyForMarket = (market: string) => market === 'HK' ? 'HKD' : market === 'US' ? 'USD' : 'CNY'

export default function DividendDialog({
  open,
  accounts,
  stocks,
  initialAccountId,
  initialStockId,
  onOpenChange,
  onSaved,
}: Props) {
  const { toast } = useToast()
  const [accountId, setAccountId] = useState('')
  const [stockId, setStockId] = useState('')
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(localDate())
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    setAccountId(String(initialAccountId || accounts[0]?.id || ''))
    setStockId(String(initialStockId || stocks[0]?.id || ''))
    setAmount('')
    setDate(localDate())
    setNote('')
  }, [open, initialAccountId, initialStockId, accounts, stocks])

  const selectedStock = useMemo(() => stocks.find(s => s.id === Number(stockId)), [stocks, stockId])
  const currency = currencyForMarket(selectedStock?.market || 'CN')
  const parsedAmount = Number.parseFloat(amount || '0')
  const valid = Number(accountId) > 0 && Number(stockId) > 0 && parsedAmount > 0

  const submit = async () => {
    if (!valid || saving) return
    setSaving(true)
    try {
      await portfolioLedgerApi.dividend({
        account_id: Number(accountId),
        stock_id: Number(stockId),
        amount: parsedAmount,
        currency,
        occurred_at: new Date(`${date}T12:00:00+08:00`).toISOString(),
        note,
        idempotency_key: crypto.randomUUID(),
      })
      toast('分红记录已保存，账户可用资金已增加', 'success')
      onOpenChange(false)
      await onSaved()
    } catch (error) {
      toast(error instanceof Error ? error.message : '保存分红记录失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>记录股票分红</DialogTitle>
          <DialogDescription>填写券商显示的实际到账金额即可，无需拆分红利税。</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>账户</Label>
              <Select value={accountId} onValueChange={setAccountId}>
                <SelectTrigger><SelectValue placeholder="选择账户" /></SelectTrigger>
                <SelectContent>
                  {accounts.map(account => <SelectItem key={account.id} value={String(account.id)}>{account.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>到账日期</Label>
              <Input type="date" value={date} onChange={e => setDate(e.target.value)} />
            </div>
          </div>

          <div>
            <Label>股票</Label>
            <Select value={stockId} onValueChange={setStockId}>
              <SelectTrigger><SelectValue placeholder="选择股票" /></SelectTrigger>
              <SelectContent>
                {stocks.map(stock => (
                  <SelectItem key={stock.id} value={String(stock.id)}>
                    {stock.name}（{stock.symbol}）
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="mt-1 text-[11px] text-muted-foreground">已清仓股票仍可在这里补录历史分红。</p>
          </div>

          <div>
            <Label>实际到账金额（{currency}）</Label>
            <Input value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" placeholder="0.00" />
          </div>

          <div>
            <Label>备注 <span className="font-normal text-muted-foreground">（选填）</span></Label>
            <Input value={note} onChange={e => setNote(e.target.value)} placeholder="例如：2026 年中期分红" />
          </div>

          <div className="rounded-lg bg-accent/30 px-3 py-2 text-[11px] text-muted-foreground">
            分红会增加账户现金并计入投资收益，不改变股票持仓数量和成本。
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
            <Button onClick={submit} disabled={!valid || saving}>{saving ? '保存中…' : '保存分红'}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
