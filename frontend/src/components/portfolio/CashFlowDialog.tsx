import { useEffect, useState } from 'react'
import { portfolioLedgerApi } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Label } from '@panwatch/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@panwatch/base-ui/components/ui/select'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

interface Props {
  open: boolean
  accounts: Array<{ id: number; name: string }>
  initialType?: 'DEPOSIT' | 'WITHDRAWAL'
  onOpenChange: (open: boolean) => void
  onSaved: () => void | Promise<void>
}

const localDate = () => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

export default function CashFlowDialog({ open, accounts, initialType = 'DEPOSIT', onOpenChange, onSaved }: Props) {
  const { toast } = useToast()
  const [accountId, setAccountId] = useState('')
  const [eventType, setEventType] = useState<'DEPOSIT' | 'WITHDRAWAL'>(initialType)
  const [amount, setAmount] = useState('')
  const [date, setDate] = useState(localDate())
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    setAccountId(String(accounts[0]?.id || ''))
    setEventType(initialType)
    setAmount('')
    setDate(localDate())
    setNote('')
  }, [open, accounts, initialType])

  const parsedAmount = Number.parseFloat(amount || '0')
  const submit = async () => {
    if (!accountId || parsedAmount <= 0 || saving) return
    setSaving(true)
    try {
      await portfolioLedgerApi.cashFlow({
        account_id: Number(accountId),
        event_type: eventType,
        amount: parsedAmount,
        currency: 'CNY',
        occurred_at: new Date(`${date}T12:00:00+08:00`).toISOString(),
        note,
        idempotency_key: crypto.randomUUID(),
      })
      toast(eventType === 'DEPOSIT' ? '入金记录已保存' : '出金记录已保存', 'success')
      onOpenChange(false)
      await onSaved()
    } catch (error) {
      toast(error instanceof Error ? error.message : '保存资金流水失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>记录资金变化</DialogTitle>
          <DialogDescription>入金和出金是年度收益计算需要剔除的外部现金流。</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>类型</Label>
              <Select value={eventType} onValueChange={value => setEventType(value as 'DEPOSIT' | 'WITHDRAWAL')}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="DEPOSIT">入金</SelectItem>
                  <SelectItem value="WITHDRAWAL">出金</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>账户</Label>
              <Select value={accountId} onValueChange={setAccountId}>
                <SelectTrigger><SelectValue placeholder="选择账户" /></SelectTrigger>
                <SelectContent>
                  {accounts.map(account => <SelectItem key={account.id} value={String(account.id)}>{account.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>金额（CNY）</Label>
              <Input value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" placeholder="0.00" />
            </div>
            <div>
              <Label>日期</Label>
              <Input type="date" value={date} onChange={e => setDate(e.target.value)} />
            </div>
          </div>
          <div>
            <Label>备注 <span className="font-normal text-muted-foreground">（选填）</span></Label>
            <Input value={note} onChange={e => setNote(e.target.value)} />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>取消</Button>
            <Button onClick={submit} disabled={!accountId || parsedAmount <= 0 || saving}>{saving ? '保存中…' : '保存'}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
