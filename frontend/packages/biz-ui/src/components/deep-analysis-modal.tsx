/**
 * 深度分析弹窗(TradingAgents)。
 *
 * 三种状态:
 * 1. 触发中 — 显示「分析需 3-5 分钟,确认开始?」+ 成本预估
 * 2. 运行中 — polling /agents/runs/{trace_id}/progress,显示阶段进度
 * 3. 完成 — 顶层摘要 + Markdown 推理 + 可展开 4 分析师报告 + 辩论
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'
import {
  tradingAgentsApi,
  type BudgetInfo,
  type DeepAnalysisResult,
  type ProgressResponse,
  type ProgressStage,
} from '@panwatch/api'

const STAGE_LABEL: Record<string, string> = {
  market_analyst: '技术分析师',
  social_analyst: '情绪分析师',
  news_analyst: '新闻分析师',
  fundamentals_analyst: '基本面分析师',
  bull_bear_debate: '看多看空辩论',
  research_manager: '研究主管',
  trader: '交易员决策',
  risk_judge: '风控判定',
  final_decision: 'PM 整合',
}

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-emerald-600 dark:text-emerald-400',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-rose-600 dark:text-rose-400',
}

const POLL_INTERVAL_MS = 2000

/** localStorage 里记录某只股票最近一次触发的 trace_id;关闭重开弹窗时恢复 polling */
const STORAGE_KEY_PREFIX = 'panwatch:tradingagents:running:'
/** trace_id 持续多久后认为可能已不再运行(避免显示过期 trace 的 idle) */
const TRACE_MAX_AGE_MS = 20 * 60 * 1000  // 20 分钟

function loadRunningTrace(stockSymbol: string): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + stockSymbol)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { traceId: string; startedAt: number }
    if (!parsed.traceId || !parsed.startedAt) return null
    if (Date.now() - parsed.startedAt > TRACE_MAX_AGE_MS) {
      localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
      return null
    }
    return parsed.traceId
  } catch {
    return null
  }
}

function saveRunningTrace(stockSymbol: string, traceId: string): void {
  try {
    localStorage.setItem(
      STORAGE_KEY_PREFIX + stockSymbol,
      JSON.stringify({ traceId, startedAt: Date.now() }),
    )
  } catch {
    /* 忽略 quota 等错误 */
  }
}

function clearRunningTrace(stockSymbol: string): void {
  try {
    localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
  } catch {
    /* ignore */
  }
}

export interface DeepAnalysisModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stockId: number
  stockName: string
  stockSymbol: string
  /** 历史分析(若有,直接展示) */
  initialResult?: DeepAnalysisResult | null
}

export function DeepAnalysisModal({
  open,
  onOpenChange,
  stockId,
  stockName,
  stockSymbol,
  initialResult = null,
}: DeepAnalysisModalProps) {
  const { toast } = useToast()
  const [stage, setStage] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [progress, setProgress] = useState<ProgressResponse | null>(null)
  const [result, setResult] = useState<DeepAnalysisResult | null>(initialResult)
  const [error, setError] = useState<string>('')
  const [showAnalystDetails, setShowAnalystDetails] = useState(false)
  const [showDebate, setShowDebate] = useState(false)
  const [budget, setBudget] = useState<BudgetInfo | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 弹窗关闭时清理 polling
  useEffect(() => {
    if (!open) {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [open])

  // 重置初始状态 + 后端查询是否有正在跑/已完成的任务
  useEffect(() => {
    if (!open) return

    if (initialResult) {
      setResult(initialResult)
      setStage('done')
      return
    }

    // 先重置为 idle (避免上次 state 残留),然后异步查后端
    setStage('idle')
    setResult(null)
    setError('')
    setProgress(null)
    setTraceId(null)

    // 并发查 3 个数据:
    //   - findRunning:这只股票最近 30 分钟有没有运行中的任务
    //   - getLatestForStock:有没有当日已完成的结果(过 30 分钟也算)
    //   - getBudget:本月预算(idle 状态展示)
    // 优先级:running > done(已有结果)> idle
    Promise.all([
      tradingAgentsApi.findRunning(stockSymbol).catch(() => ({ trace_id: null, status: 'none' as const })),
      tradingAgentsApi.getLatestForStock(stockSymbol).catch(() => null),
      tradingAgentsApi.getBudget().catch(() => null),
    ]).then(([runningInfo, latestResult, budgetInfo]) => {
      setBudget(budgetInfo)

      // 1) 优先:有正在跑的任务 → 进入 running
      if (runningInfo.status === 'running' && runningInfo.trace_id) {
        const tid = runningInfo.trace_id
        setTraceId(tid)
        setStage('running')
        tradingAgentsApi.getProgress(tid).then(resp => setProgress(resp))
        if (timerRef.current) clearInterval(timerRef.current)
        timerRef.current = setInterval(() => pollProgress(tid), POLL_INTERVAL_MS)
        return
      }

      // 2) localStorage 兜底:任务刚触发可能还没写 log(后端 findRunning 暂时查不到)
      if (runningInfo.status === 'none') {
        const localTrace = loadRunningTrace(stockSymbol)
        if (localTrace) {
          setTraceId(localTrace)
          setStage('running')
          tradingAgentsApi.getProgress(localTrace).then(resp => setProgress(resp))
          if (timerRef.current) clearInterval(timerRef.current)
          timerRef.current = setInterval(() => pollProgress(localTrace), POLL_INTERVAL_MS)
          return
        }
      }

      // 3) 有当日已完成结果 → 直接展示(DoneView 会显示「当日缓存」标签 + 重新分析按钮)
      if (latestResult) {
        // 标记成缓存(避免用户误以为是刚跑出来的实时结果)
        latestResult.raw_data.from_cache = true
        setResult(latestResult)
        setStage('done')
        clearRunningTrace(stockSymbol)
        return
      }

      // 4) 都没有 → idle 状态(默认)
      clearRunningTrace(stockSymbol)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialResult, stockSymbol])

  const pollProgress = useCallback(
    async (tid: string) => {
      try {
        const resp = await tradingAgentsApi.getProgress(tid)
        setProgress(resp)
        if (resp.status === 'success' && resp.run) {
          // 完成,拉历史结果
          if (timerRef.current) {
            clearInterval(timerRef.current)
            timerRef.current = null
          }
          clearRunningTrace(stockSymbol)
          const latest = await tradingAgentsApi.getLatestForStock(stockSymbol)
          if (latest) {
            setResult(latest)
            setStage('done')
          } else {
            setError('结果未落库,请稍后到「AI 历史」查看')
            setStage('error')
          }
        } else if (resp.status === 'failed') {
          if (timerRef.current) {
            clearInterval(timerRef.current)
            timerRef.current = null
          }
          clearRunningTrace(stockSymbol)
          setError(resp.run?.error || '分析失败')
          setStage('error')
        }
      } catch (e) {
        // polling 失败不立即终止,记一次错误
        console.warn('progress poll error:', e)
      }
    },
    [stockSymbol],
  )

  const handleStart = useCallback(async (force = false) => {
    setStage('running')
    setError('')
    setProgress(null)
    try {
      const triggerResp = await tradingAgentsApi.trigger(stockId, { force })
      const tid = triggerResp.trace_id || ''
      setTraceId(tid)
      if (!tid) {
        // 后端未返回 trace_id,只显示 message
        setStage('done')
        toast(triggerResp.message || '已触发', 'success')
        return
      }
      // 持久化 trace_id 让关闭重开能恢复进度
      saveRunningTrace(stockSymbol, tid)
      // 启动 polling
      timerRef.current = setInterval(() => {
        pollProgress(tid)
      }, POLL_INTERVAL_MS)
      // 立即拉一次
      pollProgress(tid)
    } catch (e) {
      setStage('error')
      setError(e instanceof Error ? e.message : '触发失败')
    }
  }, [stockId, stockSymbol, pollProgress, toast])

  const handleClose = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    onOpenChange(false)
  }, [onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl w-[92vw] max-h-[85vh] overflow-y-auto scrollbar">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            🧠 深度分析 · {stockName} ({stockSymbol})
          </DialogTitle>
          <DialogDescription>
            TradingAgents 多 Agent 决策框架 · 仅供学习研究参考,不构成投资建议
          </DialogDescription>
        </DialogHeader>

        {stage === 'idle' && (
          <IdleView
            stockSymbol={stockSymbol}
            budget={budget}
            onStart={() => handleStart(false)}
            onCancel={handleClose}
          />
        )}

        {stage === 'running' && (
          <RunningView progress={progress} traceId={traceId || ''} onClose={handleClose} />
        )}

        {stage === 'done' && result && <DoneView
          result={result}
          showAnalystDetails={showAnalystDetails}
          setShowAnalystDetails={setShowAnalystDetails}
          showDebate={showDebate}
          setShowDebate={setShowDebate}
          onRerun={() => handleStart(true)}
        />}

        {stage === 'error' && (
          <div className="space-y-3 text-[13px]">
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-rose-600">
              <div className="font-semibold mb-1">分析失败</div>
              <div className="text-[12px]">{error}</div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>关闭</Button>
              <Button onClick={() => handleStart(false)}>重试</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function IdleView({
  stockSymbol,
  budget,
  onStart,
  onCancel,
}: {
  stockSymbol: string
  budget: BudgetInfo | null
  onStart: () => void
  onCancel: () => void
}) {
  const overBudget = budget?.exceeded && budget.over_budget_action === 'reject'
  const est = budget?.estimate_next_run
  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-1.5">
        <div className="font-medium">即将分析:{stockSymbol}</div>
        <div className="text-muted-foreground">
          调用 4 类分析师(技术 / 情绪 / 新闻 / 基本面) + 看多看空辩论 + 风控 + PM 整合
        </div>
        <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
          <div>⏱ 预计耗时:3-8 分钟</div>
          {est ? (
            <div>💰 预估成本:${est.cost_low_usd.toFixed(2)} - ${est.cost_high_usd.toFixed(2)} ({est.model})</div>
          ) : (
            <div>💰 预估成本:加载中...</div>
          )}
          <div>ℹ️ 异步执行,可关闭弹窗,完成时通过通知渠道推送</div>
        </div>
      </div>

      {/* 本月预算 */}
      {budget && (
        <div className={`rounded-lg p-3 text-[12px] ${overBudget ? 'bg-rose-500/10 border border-rose-500/30' : 'bg-accent/20'}`}>
          <div className="flex items-center justify-between">
            <span className="font-medium">本月预算</span>
            <span className={overBudget ? 'text-rose-600' : 'text-muted-foreground'}>
              ${budget.used.toFixed(2)} / ${budget.limit.toFixed(2)}
              {budget.runs_this_month > 0 && ` · ${budget.runs_this_month} 次`}
            </span>
          </div>
          {overBudget && (
            <div className="text-[11px] text-rose-600 mt-1">
              ⚠️ 本月预算已用尽。如需继续,请到「设置 → Agent → TradingAgents」调高 `monthly_budget_usd`。
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>取消</Button>
        <Button onClick={onStart} disabled={overBudget}>开始分析</Button>
      </div>
    </div>
  )
}

function RunningView({
  progress,
  traceId,
  onClose,
}: {
  progress: ProgressResponse | null
  traceId: string
  onClose: () => void
}) {
  const elapsed = progress?.elapsed_sec ?? 0
  const cost = progress?.total_cost_usd ?? 0
  const stages = progress?.stages ?? []

  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-primary animate-pulse" />
          <span className="font-medium">分析进行中...</span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            已用 {formatElapsed(elapsed)} · ${cost.toFixed(4)}
          </span>
        </div>
        <div className="space-y-1 mt-3">
          {stages.length > 0 ? stages.map((s) => (
            <StageRow key={s.name} stage={s} />
          )) : (
            <div className="text-[12px] text-muted-foreground">准备中...</div>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground/70 mt-3 font-mono">
          trace_id: {traceId.slice(0, 16)}...
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>
          后台运行 (完成时推送通知)
        </Button>
      </div>
    </div>
  )
}

function StageRow({ stage }: { stage: ProgressStage }) {
  const label = STAGE_LABEL[stage.name] || stage.name
  const icon =
    stage.status === 'done' ? '✓' : stage.status === 'running' ? '🔄' : '⏸'
  const cls =
    stage.status === 'done'
      ? 'text-emerald-600 dark:text-emerald-400'
      : stage.status === 'running'
      ? 'text-primary'
      : 'text-muted-foreground/60'
  return (
    <div className={`flex items-center gap-2 text-[12px] ${cls}`}>
      <span className="w-4">{icon}</span>
      <span>{label}</span>
      {stage.cost_usd ? (
        <span className="ml-auto text-[10px] opacity-70 font-mono">
          ${stage.cost_usd.toFixed(4)}
        </span>
      ) : null}
    </div>
  )
}

function DoneView({
  result,
  showAnalystDetails,
  setShowAnalystDetails,
  showDebate,
  setShowDebate,
  onRerun,
}: {
  result: DeepAnalysisResult
  showAnalystDetails: boolean
  setShowAnalystDetails: (v: boolean) => void
  showDebate: boolean
  setShowDebate: (v: boolean) => void
  onRerun: () => void
}) {
  // 防御性默认值:后端拉历史时可能 raw_data 缺失,这里给完整 fallback 避免白屏
  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion || {
    action: 'hold' as const,
    action_label: '持有',
    signal: '',
    reason: '',
    should_alert: false,
    agent_name: 'tradingagents',
    agent_label: 'TradingAgents 深度',
    confidence: 5.0,
  }
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const fromCache = rawData.from_cache
  const costUsd = rawData.cost_usd

  return (
    <div className="space-y-4 text-[13px]">
      {fromCache && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-2 text-[12px] text-amber-700 dark:text-amber-400 flex items-center justify-between">
          <span>ℹ️ 当日缓存:今天已经分析过这只股票,展示缓存结果(无新成本)</span>
          <Button variant="outline" size="sm" onClick={onRerun} className="ml-3 h-7 text-[11px]">
            忽略缓存重新分析
          </Button>
        </div>
      )}

      {/* 顶层摘要 */}
      <div className="rounded-lg bg-accent/30 p-4 space-y-2">
        <div className="flex items-center gap-3">
          <span className={`text-[22px] font-bold ${DECISION_COLOR[sug.action] || ''}`}>
            {sug.action_label}
          </span>
          <span className="text-[12px] text-muted-foreground">
            置信度 {sug.confidence?.toFixed(1) ?? '-'} / 10
          </span>
        </div>
        <div className="text-[12px] text-foreground/80">{sug.reason?.slice(0, 200)}</div>
        <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-2">
          <span>成本:${costUsd?.toFixed(4) ?? '-'}</span>
        </div>
      </div>

      {/* Markdown 推理 */}
      <div className="rounded-lg border border-border/50 p-4">
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <ReactMarkdown>{result.content}</ReactMarkdown>
        </div>
      </div>

      {/* 分析师报告(可展开) */}
      <div>
        <button
          className="text-[12px] text-muted-foreground hover:text-foreground flex items-center gap-1"
          onClick={() => setShowAnalystDetails(!showAnalystDetails)}
        >
          {showAnalystDetails ? '▼' : '▶'} 4 位分析师报告
        </button>
        {showAnalystDetails && (
          <div className="space-y-3 mt-2 pl-3 border-l-2 border-border/40">
            {(['market', 'social', 'news', 'fundamentals'] as const).map((k) => {
              const text = (reports as unknown as Record<string, string>)[k] || ''
              if (!text) return null
              return (
                <details key={k} open className="text-[12px]">
                  <summary className="font-medium cursor-pointer">
                    {STAGE_LABEL[`${k}_analyst`] || k}
                  </summary>
                  <div className="mt-2 text-[11px] text-foreground/80 whitespace-pre-wrap">
                    {text.slice(0, 1500)}
                    {text.length > 1500 && '... (截断)'}
                  </div>
                </details>
              )
            })}
          </div>
        )}
      </div>

      {/* 辩论历史(可展开) */}
      {debate && debate.history && (
        <div>
          <button
            className="text-[12px] text-muted-foreground hover:text-foreground flex items-center gap-1"
            onClick={() => setShowDebate(!showDebate)}
          >
            {showDebate ? '▼' : '▶'} 看多看空辩论
          </button>
          {showDebate && (
            <div className="mt-2 pl-3 border-l-2 border-border/40 text-[11px] text-foreground/80 whitespace-pre-wrap max-h-96 overflow-y-auto">
              {debate.history}
              {debate.judge_decision && (
                <>
                  <div className="font-medium mt-3 mb-1">研究主管裁决:</div>
                  <div>{debate.judge_decision}</div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* 免责声明 */}
      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        本分析由 AI 多 Agent 框架生成,仅供学习研究参考,不构成任何投资建议。
        投资有风险,决策需自主判断。
      </div>
    </div>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}s`
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}m${s.toString().padStart(2, '0')}s`
}
