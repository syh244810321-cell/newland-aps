import { useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { ArrowDownRight, ArrowLeft, ArrowRight, ArrowUpRight, CalendarDays, CheckCircle2,
  CircleAlert, Clock3, Factory, Layers3, ListFilter, Package, Search, Timer, Workflow } from 'lucide-react'
import type { Batch, Dataset, Order, Plan, PlanSummary, Status, Workspace } from './types'

export const statusName: Record<Status, string> = {
  draft: '草案', review: '待审批', approved: '已批准', published: '已发布', received: '已签收',
}
export const activityName: Record<string, string> = {
  import:'导入数据', solve:'发起排程', replan:'发起重排', submit:'提交审批',
  approve:'批准方案', reject:'退回草案', publish:'发布计划', receive:'车间签收',
  'manual-adjust':'人工调整', 'report-execution':'车间报工',
}
export const nf = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 })
export const num = (n: number) => nf.format(n)
export const rate = (n: number) => (n * 100).toFixed(1) + '%'
export const dateTime = (value: string | null | undefined) => value?.replace('T', ' ') ?? '—'
export const dshort = (value:string) => value.slice(5, 10).replace('-', '/')
export const createdAt = (value: number) => new Intl.DateTimeFormat('zh-CN', {
  month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false, timeZone:'Asia/Shanghai',
}).format(new Date(value * 1000))
export const keyLabel = (id:string) => id.slice(0, 7).toUpperCase()
export const ONE_DAY = 86_400_000
export const localDate = (stamp: number) => new Date(stamp + 28_800_000).toISOString().slice(0,16)
export const timeValue = (value: string) => Date.parse(value + '+08:00')

export function Badge({ children, tone = 'draft' }: { children: ReactNode; tone?: string }) {
  return <span className={'badge ' + tone}><i aria-hidden="true"/>{children}</span>
}
export function StatusBadge({ status }: { status: Status }) {
  return <Badge tone={status}>{statusName[status]}</Badge>
}
export function Panel({ title, detail, actions, children, extra='' }: {
  title: string; detail?: ReactNode; actions?: ReactNode; children: ReactNode; extra?: string
}) {
  return <section className={'panel ' + extra}>
    <header className="panel-head"><div><h2>{title}</h2>{detail && <p>{detail}</p>}</div>{actions && <div className="panel-actions">{actions}</div>}</header>
    {children}
  </section>
}
export function Empty({ title, detail, children }: { title: string; detail: string; children?: ReactNode }) {
  return <div className="empty"><div className="empty-icon"><Layers3 size={22}/></div><h3>{title}</h3><p>{detail}</p>{children && <div className="empty-actions">{children}</div>}</div>
}
export function KPIs({ dataset, plan, baseline }: { dataset:Dataset | null; plan:Plan | null; baseline?: PlanSummary }) {
  const m = plan?.metrics
  const orderCount = m?.orders ?? dataset?.summary.orders
  const productCodes = plan ? new Set(plan.orders.map(order=>order.code)).size : dataset?.summary.codes
  const setupDelta = m && baseline ? m.setup_hours - baseline.metrics.setup_hours : null
  const deliveryDelta = m && baseline ? (m.otd - baseline.metrics.otd) * 100 : null
  const rows = [
    { label:'计划订单', value:orderCount == null ? '—' : num(orderCount), unit:'笔', icon:Package, foot:productCodes != null ? num(productCodes) + ' 个订单产品编码' : '等待导入订单数据' },
    { label:'预计准交率', value:m ? rate(m.otd) : '—', unit:'', icon:CheckCircle2, foot:m ? num(m.on_time) + ' 笔预计按期' : '生成方案后计算', delta:deliveryDelta, dx:' pp', positive:(deliveryDelta ?? 0) >= 0 },
    { label:'换型耗时', value:m ? num(m.setup_hours) : '—', unit:'h', icon:Timer, foot:'产线换型合计', delta:setupDelta, dx:' h', positive:(setupDelta ?? 0) <= 0 },
    { label:'未排订单', value:m ? num(m.pending_orders) : '—', unit:'笔', icon:CircleAlert, foot:m ? num(m.missing_order_units) + ' 件待处置' : '生成方案后识别', warning:!!m?.pending_orders },
  ]
  return <section className="kpi-strip" aria-label="计划指标">
    {rows.map(({label,value,unit,icon:Icon,foot,delta,dx,positive,warning})=>
      <article className="stat" key={label}><div className="stat-top">{label}<span className={'stat-icon ' + (warning ? 'caution' : '')}><Icon size={17}/></span></div>
        <div className="stat-number">{value}{value !== '—' && unit && <span>{unit}</span>}</div>
        <div className="stat-foot"><span>{foot}</span>{delta != null && Math.abs(delta) > .04 &&
          <span className={'delta '+(positive?'good':'bad')} title="相对同批规则基准">{delta>0 ? <ArrowUpRight size={13}/> : <ArrowDownRight size={13}/>} {delta > 0 ? '+' : ''}{num(delta)}{dx}</span>}</div>
      </article>)}
  </section>
}

function labelAt(start:number, i:number) {
  return localDate(start + i * ONE_DAY).slice(5,10).replace('-','/')
}

export function Timeline({ plan, compact, onBatch, onMove }: {
  plan:Plan; compact:boolean; onBatch:(batch:Batch)=>void; onMove?:(batch:Batch, line:string, start:string)=>void
}) {
  const [dragged,setDragged]=useState<Batch|null>(null)
  const [line, setLine] = useState('')
  const [range, setRange] = useState(7)
  const [week, setWeek] = useState(0)
  const cursor = timeValue(plan.at) + week * range * ONE_DAY
  const duration = range * ONE_DAY
  const maxWeek = Math.max(0,Math.ceil((timeValue(plan.end)-timeValue(plan.at))/duration)-1)
  const visibleLines = line ? [line] : compact ? plan.lines.slice(0, 7) : plan.lines
  const barPosition = (a:string,b:string): CSSProperties | null => {
    const x = Math.max(cursor,timeValue(a)), y = Math.min(cursor+duration,timeValue(b))
    return y>x ? {left:100*(x-cursor)/duration+'%',width:100*(y-x)/duration+'%'} : null
  }
  const grouped = useMemo(() => {
    const groups = new Map<string, Batch[]>()
    for (const b of plan.batches) {
      if (!groups.has(b.line)) groups.set(b.line, [])
      groups.get(b.line)?.push(b)
    }
    return groups
  }, [plan.batches])
  const ticks = range >= 7 ? 7 : range === 1/3 || range === 1 ? 8 : 6
  const periodLabel = range >= 7 ? labelAt(cursor,0)+' – '+labelAt(cursor,range-1) :
    localDate(cursor).slice(5).replace('T',' ')+' – '+localDate(cursor+duration).slice(11,16)
  return <div className="timeline">
    <div className="timeline-controls">
      <div className="period"><CalendarDays size={15}/><span>{periodLabel}</span>
        <button className="icon-button" type="button" aria-label="上一时段" disabled={week===0} onClick={()=>setWeek(week-1)}><ArrowLeft size={14}/></button>
        <button className="icon-button" type="button" aria-label="下一时段" disabled={week>=maxWeek} onClick={()=>setWeek(week+1)}><ArrowRight size={14}/></button>
      </div>
      <div className="timeline-filters">
        <label className="sr-only" htmlFor="line-filter">产线</label><select id="line-filter" value={line} onChange={e=>setLine(e.target.value)}><option value="">全部产线</option>{plan.lines.map(x=><option key={x}>{x}</option>)}</select>
        <label className="sr-only" htmlFor="window-filter">显示周期</label><select id="window-filter" value={range} onChange={e=>{setRange(Number(e.target.value));setWeek(0)}}><option value={1/24}>1 小时</option><option value={1/3}>8 小时</option><option value={1}>1 天</option><option value={7}>7 天</option><option value={14}>14 天</option></select>
      </div>
    </div>
    <div className={'timeline-scroll '+(compact?'compact':'')}>
      <div className="gantt" aria-label="资源计划时间轴" style={{'--ticks':ticks,'--tick-step':100/ticks+'%'} as CSSProperties}>
        <div className="time-axis"><span>产线</span><div>{Array.from({length:ticks},(_,tick)=>
          <span key={tick}>{range>=7?labelAt(cursor,tick*range/ticks):localDate(cursor+tick*duration/ticks).slice(11,16)}</span>
        )}</div></div>
        {visibleLines.map(l=><div className="lane" key={l}><span className="line-number">{l}</span><div className={'track '+(dragged?'can-drop':'')}
          onDragOver={e=>{if(dragged&&onMove){e.preventDefault();e.dataTransfer.dropEffect='move'}}}
          onDrop={e=>{
            if(!dragged||!onMove)return
            e.preventDefault()
            const bounds=e.currentTarget.getBoundingClientRect()
            const fraction=Math.max(0,Math.min((e.clientX-bounds.left)/bounds.width,1))
            const increment=range<1/3?60_000:range<1?300_000:900_000
            const snapped=cursor+Math.round(fraction*duration/increment)*increment
            onMove(dragged,l,localDate(snapped))
            setDragged(null)
          }}>
          {(grouped.get(l)??[]).map((b, index)=>{
            const pos = barPosition(b.start,b.end), setup = barPosition(b.setup_start,b.start)
            return <div key={b.job+'-'+index} className="batch-fragment">
              {setup && <span className="bar setup" style={setup} title={num(b.setup_minutes)+' 分钟换型'}/>}
              {pos && <button type="button" className={'bar '+b.state} style={pos} title={b.code+' · '+b.qty+' 件 · '+dateTime(b.start)}
                aria-label={l+' '+b.job+' '+b.qty+' 件'} onClick={()=>onBatch(b)}
                draggable={!!onMove&&b.state==='planned'}
                onDragStart={e=>{e.dataTransfer.setData('text/plain',b.key);e.dataTransfer.effectAllowed='move';setDragged(b)}}
                onDragEnd={()=>setDragged(null)}/>}
            </div>
          })}
        </div></div>)}
      </div>
    </div>
    <div className="timeline-foot"><div className="legend"><span><i className="l-prod"/>加工</span><span><i className="l-setup"/>换型</span><span><i className="l-fixed"/>冻结或已执行</span>{plan.batches.some(b=>b.state==='locked')&&<span><i className="l-lock"/>手工锁定</span>}</div>
      <span>{visibleLines.length} / {plan.lines.length} 条产线</span></div>
  </div>
}

export function Versions({ plans, selected, onPick }: { plans:PlanSummary[]; selected?:string; onPick:(plan:PlanSummary)=>void }) {
  return plans.length ? <div className="table-scroll"><table><thead><tr><th>方案</th><th>状态</th><th>预计准交率</th><th>换型工时</th><th>未排订单</th><th>调整任务</th><th>创建时间</th><th><span className="sr-only">打开</span></th></tr></thead>
    <tbody>{plans.map(p=><tr key={p.id} className={selected===p.id?'selected':''}>
      <td><button type="button" className="table-link" onClick={()=>onPick(p)}>{p.strategy}</button><small className="id-line">{keyLabel(p.id)}</small></td>
      <td><StatusBadge status={p.status}/></td><td className="mono">{rate(p.metrics.otd)}</td><td className="mono">{num(p.metrics.setup_hours)} h</td><td className="mono">{p.metrics.pending_orders}</td><td className="mono">{p.metrics.comparable ? p.metrics.moved + ' / ' + p.metrics.comparable : '—'}</td><td>{createdAt(p.created)}</td>
      <td><button type="button" className="icon-button table-open" aria-label={'打开 '+p.strategy+' '+keyLabel(p.id)} onClick={()=>onPick(p)}><ArrowUpRight size={16}/></button></td></tr>)}</tbody></table></div> :
    <Empty title="暂无方案" detail="生成排程后，候选方案会显示在这里。"/>
}

export function DecisionRail({ scope, plan, openFlow, onPick }: {
  scope:Workspace; plan:Plan|null; openFlow:()=>void; onPick:(item:PlanSummary)=>void
}) {
  const review = scope.plans.filter(p=>p.status==='review'||p.status==='approved'||p.status==='published')
  const active = scope.plans.find(p=>p.id===scope.plant.active_plan)
  const alerts = plan?.unassigned ?? []
  const summary = new Map<string,{model:string; reason:string; qty:number}>()
  for (const gap of alerts) {
    const key = gap.model + '|' + gap.reason
    const previous = summary.get(key)
    summary.set(key, { model: gap.model, reason: gap.reason, qty: (previous?.qty ?? 0) + gap.qty })
  }
  const topAlerts = [...summary.entries()].sort((a,b)=>b[1].qty-a[1].qty).slice(0,3)
  return <aside className="decision-rail">
    <Panel title="计划流转" actions={<button type="button" className="icon-button" aria-label="查看计划流转" onClick={openFlow}><ArrowUpRight size={17}/></button>}>
      <div className="rail-body">{active && <div className="current-plan"><div><span className="tiny-label">当前已发布</span><b>{active.strategy}</b></div><StatusBadge status={active.status}/></div>}
        {review.length ? review.slice(0,3).map(p=><button type="button" className="rail-item" key={p.id} onClick={()=>onPick(p)}>
          <span className="rail-icon"><Workflow size={15}/></span><span><b>{p.strategy}</b><small>{keyLabel(p.id)} · {createdAt(p.created)}</small></span>
          <StatusBadge status={p.status}/></button>) :
          <div className="rail-neutral"><Clock3 size={17}/><div><b>暂无待办</b><small>{active?'当前工厂没有待处理的版本。':'提交一个草案即可开始审批。'}</small></div></div>}
      </div>
    </Panel>
    <Panel title="排程异常" detail={alerts.length ? alerts.length+' 条任务需关注' : undefined}>
      <div className="rail-body">{alerts.length ? topAlerts.map(([key,g])=><div className="rail-item alert-row" key={key}>
        <span className="rail-icon warning"><CircleAlert size={15}/></span><div><b>{g.model}</b><small>{g.reason}</small></div><strong>{num(g.qty)} 件</strong>
      </div>) : <div className="rail-neutral"><CircleAlert size={16}/><div><b>{plan?'没有未排任务':'暂无排程结果'}</b><small>{plan?'当前计划已覆盖全部生产任务。':'运行排程后查看原因和数量。'}</small></div></div>}</div>
    </Panel>
  </aside>
}

export function OrdersTable({ orders, onExplain }: { orders:Order[]; onExplain:(id:string)=>void }) {
  const [query,setQuery] = useState('')
  const [status,setStatus] = useState('')
  const [page,setPage] = useState(0)
  const filtered=orders.filter(o=>(!status || o.status===status) && [o.id,o.code,o.customer].some(v=>v.toLowerCase().includes(query.trim().toLowerCase())))
  const maxPage=Math.max(0,Math.ceil(filtered.length/18)-1)
  const active=Math.min(page,maxPage)
  return <>
    <div className="table-tools">
      <label className="search-box"><Search size={16}/><input aria-label="搜索订单" placeholder="按订单、产品或客户搜索" value={query} onChange={e=>{setQuery(e.target.value);setPage(0)}}/></label>
      <label className="select-box"><ListFilter size={15}/><select aria-label="筛选订单状态" value={status} onChange={e=>{setStatus(e.target.value);setPage(0)}}><option value="">全部状态</option>{['按期','延期','未排完','库存覆盖'].map(s=><option key={s}>{s}</option>)}</select></label>
    </div>
    {!filtered.length ? <Empty title="没有匹配的订单" detail="可以调整搜索词或订单状态筛选。"/> : <div className="table-scroll"><table><thead><tr><th>订单</th><th>产品编码</th><th>需求量</th><th>库存覆盖</th><th>排产量</th><th>交期</th><th>预计完工</th><th>状态</th></tr></thead>
      <tbody>{filtered.slice(active*18,(active+1)*18).map(o=><tr key={o.id}>
        <td><button type="button" className="table-link" onClick={()=>onExplain(o.id)}>{o.id}</button><small className="id-line">{o.customer}</small></td>
        <td>{o.code}</td><td className="mono">{num(o.qty)}</td><td className="mono">{num(o.stock)}</td><td className="mono">{num(o.planned)}</td>
        <td>{dateTime(o.due)}</td><td>{dateTime(o.completion)}</td><td><Badge tone={o.status==='未排完'||o.status==='延期'?'review':'approved'}>{o.status}</Badge></td>
      </tr>)}</tbody></table></div>}
    <div className="pagination"><span>共 {filtered.length} 笔 · {active+1} / {maxPage+1}</span><div><button type="button" className="icon-button" disabled={active===0} aria-label="上一页" onClick={()=>setPage(active-1)}><ArrowLeft size={15}/></button><button type="button" className="icon-button" disabled={active>=maxPage} aria-label="下一页" onClick={()=>setPage(active+1)}><ArrowRight size={15}/></button></div></div>
  </>
}

export function Resources({ dataset }: { dataset:Dataset }) {
  return <Panel title="生产资源" detail={<><Factory size={14}/> {dataset.resources.length} 条产线 · {dataset.name}</>}>
    <div className="table-scroll"><table><thead><tr><th>产线</th><th>适用型号</th><th>小时产能</th><th>大换型</th><th>小换型</th><th>日历来源</th></tr></thead>
      <tbody>{dataset.resources.map(r=><tr key={r.id}><td className="mono">{r.id}</td><td>{r.families.join(' / ')}</td>
        <td>{num(r.capacity)} 件/h</td><td>{r.major===null?'—':r.major+' min'}</td><td>{r.minor} min</td>
        <td><Badge tone={r.calendar?'approved':'draft'}>{r.calendar?'导入日历':'连续可用假设'}</Badge></td></tr>)}</tbody></table></div>
  </Panel>
}
