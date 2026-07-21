import { useEffect, useMemo, useState } from 'react'
import { portfolioLedgerApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export interface SellTarget {
  positionId: number
  accountId: number
  accountName: string
  stockId: number
  symbol: string
  name: string
  market: string
  quantity: number
  costPrice: number
  currentPrice: number | null
}

interface Props {
  open: boolean
  target: SellTarget | null
  onOpenChange: (open: boolean) => void
  onSaved: () => void | Promise<void>
}

const localDate = () => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

const currencyForMarket = (market: string) => market === 'HK' ? 'HKD' : market === 'US' ? 'USD' : 'CNY'

export default function SellDialog({ open, target, onOpenChange, onSaved }: Props) {
  const { toast } = useToast()
  const [quantity, setQuantity] = useState('')
  const [price, setPrice] = useState('')
  const [netAmount, setNetAmount] = useState('')
  const [date, setDate] = useState(localDate())
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open || !target) return
    setQuantity(String(target.quantity))
    setPrice(target.currentPrice != null ? String(target.currentPrice) : '')
    setNetAmount('')
    setDate(localDate())
    setNote('')
  }, [open, target])

  const parsedQuantity = Number.parseInt(quantity || '0', 10)
  const parsedPrice = Number.parseFloat(price || '0')
  const gross = Number.isFinite(parsedQuantity * parsedPrice) ? parsedQuantity * parsedPrice : 0
  const actualNet = netAmount ? Number.parseFloat(netAmount) : gross
  const estimatedPnl = useMemo(
    () => target ? actualNet - parsedQuantity * target.costPrice : 0,
    [actualNet, parsedQuantity, target],
  )
  const valid = !!target && parsedQuantity > 0 && parsedQuantity <= target.quantity && parsedPrice > 0 && actualNet > 0

  const setRatio = (ratio: number) => {
    if (!target) return
    setQuantity(String(ratio >= 1 ? target.quantity : Math.max(1, Math.floor(target.quantity * ratio))))
  }

  const submit = async () => {
    if (!target || !valid || saving) return
    setSaving(true)
    try {
      await portfolioLedgerApi.sell({
        position_id: target.positionId,
        quantity: parsedQuantity,
        unit_price: parsedPrice,
        net_amount: netAmount ? Number.parseFloat(netAmount) : null,
        currency: currencyForMarket(target.market),
        occurred_at: new Date(`${date}T12:00:00+08:00`).toISOString(),
        note,
        idempotency_key: crypto.randomUUID(),
      })
      toast(parsedQuantity === target.quantity ? '清仓记录已保存' : '卖出记录已保存', 'success')
      onOpenChange(false)
      await onSaved()
    } catch (error) {
      toast(error instanceof Error ? error.message : '保存卖出记录失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>记录卖出</DialogTitle>
          <DialogDescription>
            {target ? `${target.accountName} · ${target.name}（${target.symbol}）` : ''}
          </DialogDescription>
        </DialogHeader>

        {target && (
          <div className="space-y-4">
            <div className="rounded-lg bg-accent/30 px-3 py-2 text-[12px] text-muted-foreground">
              当前持仓 <span className="font-mono text-foreground">{target.quantity}</span> 股，成本价{' '}
              <span className="font-mono text-foreground">{target.costPrice.toFixed(3)}</span>
            </div>

            <div>
              <div className="mb-1 flex items-center justify-between">
                <Label>卖出数量</Label>
                <div className="flex gap-1">
                  {[0.25, 0.5, 1].map(ratio => (
                    <button
                      key={ratio}
                      type="button"
                      onClick={() => setRatio(ratio)}
                      className="rounded bg-accent px-2 py-0.5 text-[10px] text-muted-foreground hover:text-foreground"
                    >
                      {ratio === 1 ? '全部' : `${ratio * 100}%`}
                    </button>
                  ))}
                </div>
              </div>
              <Input value={quantity} onChange={e => setQuantity(e.target.value)} inputMode="numeric" />
              {parsedQuantity > target.quantity && <p className="mt-1 text-[11px] text-destructive">不能超过当前持仓数量</p>}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>成交价格</Label>
                <Input value={price} onChange={e => setPrice(e.target.value)} inputMode="decimal" />
              </div>
              <div>
                <Label>成交日期</Label>
                <Input type="date" value={date} onChange={e => setDate(e.target.value)} />
              </div>
            </div>

            <div>
              <Label>实际到账金额 <span className="font-normal text-muted-foreground">（选填）</span></Label>
              <Input
                value={netAmount}
                onChange={e => setNetAmount(e.target.value)}
                inputMode="decimal"
                placeholder={`默认 ${gross.toFixed(2)}`}
              />
              <p className="mt-1 text-[11px] text-muted-foreground">若券商到账金额已扣除费用，可填写实际到账；留空按数量 × 成交价计算。</p>
            </div>

            <div>
              <Label>备注 <span className="font-normal text-muted-foreground">（选填）</span></Label>
              <Input value={note} onChange={e => setNote(e.target.value)} placeholder="例如：止盈、调整仓位" />
            </div>

            <div className="grid grid-cols-3 gap-2 rounded-lg border border-border/50 p-3 text-[11px]">
              <div>
                <div className="text-muted-foreground">卖出后</div>
                <div className="mt-1 font-mono text-foreground">{Math.max(0, target.quantity - parsedQuantity)} 股</div>
              </div>
              <div>
                <div className="text-muted-foreground">预计回款</div>
                <div className="mt-1 font-mono text-foreground">{actualNet.toFixed(2)} {currencyForMarket(target.market)}</div>
              </div>
              <div>
                <div className="text-muted-foreground">预计已实现</div>
                <div className={`mt-1 font-mono ${estimatedPnl >= 0 ? 'text-rose-500' : 'text-emerald-500'}`}>
                  {estimatedPnl >= 0 ? '+' : ''}{estimatedPnl.toFixed(2)}
                </div>
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
              <Button onClick={submit} disabled={!valid || saving}>{saving ? '保存中…' : parsedQuantity === target.quantity ? '确认清仓' : '确认卖出'}</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
