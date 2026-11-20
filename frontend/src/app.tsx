import { lazy, Suspense, useEffect, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { Aperture, ArrowRight, Archive, BellRing, CalendarDays, Check, ChevronDown, ChevronRight,
  ClipboardCheck, Download, Factory, FileSpreadsheet, GitBranch, LayoutGrid, ListOrdered, LogOut,
  Menu, Plus, ShieldCheck, Upload, Workflow } from 'lucide-react'
import type { Action, ActualEntry, Batch, Config, Evidence, Job, Plan, PlanSummary, Role, User, Workspace } from './types'
import { BatchList, ExecutionTable } from './execution'
import { Badge, DecisionRail, Empty, KPIs, ONE_DAY, OrdersTable, Panel, Resources, StatusBadge, Timeline, Versions,
  activityName, createdAt, dateTime, keyLabel, localDate, num, timeValue } from './ui'

const endpoint = '/api/workbench'
const Modal = lazy(() => import('antd/es/modal'))
const roleName:Record<Role,string> = {planner:'计划员',reviewer:'审批主管',operator:'车间班组',admin:'管理员'}
const sections = [
  {id:'overview',name:'排程工作台',icon:LayoutGrid},
  {id:'planning',name:'计划视图',icon:CalendarDays},
  {id:'orders',name:'订单交付',icon:ListOrdered},
  {id:'data',name:'数据接入',icon:FileSpreadsheet},
  {id:'resources',name:'生产资源',icon:Factory},
  {id:'release',name:'审批与发布',icon:ClipboardCheck},
  {id:'execution',name:'车间报工',icon:Check},
  {id:'event',name:'异常重排',icon:BellRing},
  {id:'audit',name:'操作记录',icon:Archive},
] as const
type Page = typeof sections[number]['id']
type Dialog =
  | {kind:'import'|'run'|'replan'}
  | {kind:'action'; action:Action; plan:Plan}
  | {kind:'batch'; batch:Batch}
  | {kind:'manual'; batch:Batch; line:string; start:string}
  | {kind:'actual'; batch:Batch; planId:string; entry?:ActualEntry}
  | {kind:'evidence'; evidence:Evidence}
const verb:Record<Action,string> = {submit:'提交审批',approve:'批准方案',reject:'退回草案',publish:'发布计划',receive:'确认签收'}

async function request<T>(url:string, options?:RequestInit):Promise<T> {
  const response = await fetch(endpoint+url, {credentials:'same-origin', ...options})
  if (response.status===204) return undefined as T
  const value = await response.json()
  if (!response.ok) throw new Error(typeof value.detail==='string' ? value.detail :
    Array.isArray(value.detail) ? '请检查请求字段、日期和数量。' : '请求未完成，请重试。')
  return value as T
}
function post<T>(path:string, value?:unknown):Promise<T> {
  return request<T>(path,{method:'POST',headers:{'Content-Type':'application/json'},body:value===undefined?undefined:JSON.stringify(value)})
}
function pause(ms:number) { return new Promise<void>(done=>window.setTimeout(done,ms)) }
function field(form:FormData, key:string) {return String(form.get(key)??'')}
function continueActions(value:Plan, user:User, active:string|null):Action[] {
  const cap = (role:Role)=>user.role===role||user.role==='admin'
  if(value.status==='draft' && cap('planner')) return ['submit']
  if(value.status==='review' && cap('reviewer') && value.submitted_by!==user.id) return ['approve','reject']
  if(value.status==='approved' && cap('reviewer')) return ['publish']
  if(value.status==='published' && cap('operator') && active===value.id) return ['receive']
  return []
}

function LoginScreen({ config, busy, error, onLogin, onDemo }: {
  config:Config|null; busy:boolean; error:string; onLogin:(e:FormEvent<HTMLFormElement>)=>void; onDemo:(r:Role)=>void
}) {
  return <main className="login-page">
    <section className="login-card">
      <div className="login-brand"><span className="brand-mark"><Aperture size={24}/></span><span>新大地 APS</span></div>
      <h1>生产计划工作台</h1>
      <p>登录工厂账户，管理订单排程和计划发布。</p>
      <form onSubmit={onLogin} className="form-stack">
        <label>账户<input name="username" autoComplete="username" required maxLength={100}/></label>
        <label>密码<input name="password" autoComplete="current-password" type="password" required maxLength={200}/></label>
        <button type="submit" className="button primary" disabled={busy}>登录 <ArrowRight size={17}/></button>
      </form>
      {error && <div className="warning-box" role="alert">{error}</div>}
      {config?.demo && <div className="login-demo"><span>本机试点演示</span><div>{config.roles.map(r=>
        <button key={r.id} title={'进入 '+r.name} type="button" className="button quiet" disabled={busy} onClick={()=>onDemo(r.id)}>{r.name}</button>)}</div></div>}
    </section>
  </main>
}

export function App() {
  const [config,setConfig]=useState<Config|null>(null)
  const [user,setUser]=useState<User|null>(null)
  const [scope,setScope]=useState<Workspace|null>(null)
  const [plan,setPlan]=useState<Plan|null>(null)
  const [page,setPage]=useState<Page>('overview')
  const [dialog,setDialog]=useState<Dialog|null>(null)
  const [job,setJob]=useState<Job|null>(null)
  const [busy,setBusy]=useState(false)
  const [loading,setLoading]=useState(true)
  const [toast,setToast]=useState('')
  const [error,setError]=useState('')
  const [drawer,setDrawer]=useState(false)
  const [eventType,setEventType]=useState('insert')
  const [useActual,setUseActual]=useState(false)
  const [runCutoff,setRunCutoff]=useState('')
  const [executionCutoff,setExecutionCutoff]=useState('')
  const [actualType,setActualType]=useState<'closed'|'not_started'>('closed')
  const [executionView,setExecutionView]=useState<{plan:Plan; entries:ActualEntry[]}|null>(null)
  const [executionLoading,setExecutionLoading]=useState(false)
  const activePlanId=scope?.plant.active_plan
  const [dismissUser,setDismissUser]=useState(false)

  async function sync(selected?:string) {
    const data=await request<Workspace>('')
    const candidates=data.plans
    const chosen=candidates.find(p=>p.id===selected)||candidates.find(p=>p.id===data.plant.active_plan)
      ||candidates.find(p=>p.job_id===candidates[0]?.job_id && p.strategy==='交付优先')||candidates[0]
    const detail=chosen ? await request<Plan>('/plans/'+chosen.id) : null
    setScope(data)
    setPlan(detail)
  }
  useEffect(()=>{
    let alive=true
    request<Config>('/config').then(value=>{if(alive)setConfig(value)}).catch(e=>{if(alive)setError(e.message)})
    request<User>('/session').then(async value=>{if(alive){await sync();setUser(value)}}).catch(()=>{})
      .finally(()=>{if(alive)setLoading(false)})
    return ()=>{alive=false}
  },[])

  useEffect(()=>{
    if(page!=='execution'||!user||!activePlanId) return
    let live=true
    setExecutionLoading(true)
    setExecutionView(null)
    const base='/plans/'+activePlanId
    Promise.all([request<Plan>(base),request<ActualEntry[]>(base+'/execution')])
      .then(([plan,entries])=>{if(live){
        setExecutionView({plan,entries})
        setExecutionCutoff(localDate(Math.min(timeValue(plan.at)+8*3_600_000,timeValue(plan.end)-60_000)))
      }})
      .catch(e=>{if(live)setError(e.message)})
      .finally(()=>{if(live)setExecutionLoading(false)})
    return ()=>{live=false}
  },[page,user?.id,activePlanId])

  function go(to:Page) {setPage(to);setDrawer(false);setError('');window.scrollTo(0,0)}
  function open(value:Dialog) {setError('');setDialog(value)}
  async function run(fn:()=>Promise<void>) {
    setBusy(true);setError('');setToast('')
    try {await fn()} catch(e) {setError(e instanceof Error ? e.message : '请求失败，请重试。')}
    finally {setBusy(false)}
  }
  function demo(role:Role) {
    void run(async()=>{
      const person=await post<User>('/demo/'+role)
      await sync(plan?.id)
      setUser(person)
      setDismissUser(false)
      if (person.role==='operator' && page!=='overview') go('release')
    })
  }
  function login(e:FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f=new FormData(e.currentTarget)
    void run(async()=>{
      const person=await post<User>('/session',{username:field(f,'username'),password:field(f,'password')})
      await sync()
      setUser(person); go('overview')
    })
  }
  function logout() {
    void run(async()=>{
      await request('/session',{method:'DELETE'})
      setUser(null);setPlan(null);setScope(null);setDismissUser(false);setDialog(null)
    })
  }
  function choose(version:PlanSummary, move?:Page) {
    void run(async()=>{
      setPlan(await request<Plan>('/plans/'+version.id))
      if (move) go(move)
    })
  }
  function explain(id:string) {
    if (!plan) return
    void run(async()=>open({kind:'evidence',evidence:await request<Evidence>('/plans/'+plan.id+'/orders/'+encodeURIComponent(id)+'/evidence')}))
  }
  function upload(e?:FormEvent<HTMLFormElement>) {
    e?.preventDefault()
    const data=e ? new FormData(e.currentTarget) : null
    void run(async()=>{
      if(data) await request('/datasets',{method:'POST',body:data})
      else await post('/sample')
      await sync(plan?.id)
      setDialog(null);go('data');setToast('数据已校验并保存为工厂快照。')
    })
  }
  async function follow(next:Job) {
    let task=next
    setJob(task)
    for(let attempt=0; task.status==='queued'||task.status==='running'; attempt++){
      if(attempt===80) throw new Error('排程仍在后台执行。稍后刷新工作台查看方案。')
      await pause(500)
      task=await request<Job>('/jobs/'+task.id);setJob(task)
    }
    if(task.status==='failed') throw new Error(task.error??'任务失败。')
    await sync(task.output?.plan_ids[1]??task.output?.plan_ids[0])
    setDialog(null);go('planning');setToast('方案已生成并通过独立校验。')
  }
  function openReplan(source:Plan, withActual:boolean, cutoff='') {
    setPlan(source)
    setRunCutoff(cutoff)
    setUseActual(withActual)
    setEventType(withActual?'none':'insert')
    setJob(null)
    open({kind:'replan'})
  }
  function manual(e:FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if(dialog?.kind!=='manual'||!plan)return
    const form=new FormData(e.currentTarget)
    const change={revision:plan.revision,line:field(form,'line')||dialog.line,start:field(form,'start')||dialog.start,locked:form.has('locked')}
    const path='/plans/'+plan.id+'/batches/'+dialog.batch.key+'/edit'
    void run(async()=>{
      const draft=await post<Plan>(path,change)
      await sync(draft.id);setDialog(null);go('planning');setToast('新草案已通过冲突校验。')
    })
  }
  function saveActual(e:FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if(dialog?.kind!=='actual')return
    const form=new FormData(e.currentTarget)
    const closed=actualType==='closed'
    const fields={kind:actualType,revision:dialog.entry?.revision??0,qty:closed?Number(field(form,'qty')):0,
      start:closed?field(form,'start'):null,end:closed?field(form,'end'):null,
      checked_at:closed?null:field(form,'checked_at')}
    const path='/plans/'+dialog.planId+'/execution/'+dialog.batch.key
    void run(async()=>{
      const entry=await request<ActualEntry>(path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)})
      setExecutionView(view=>view?{...view,entries:[...view.entries.filter(item=>item.batch_key!==entry.batch_key),entry]}:view)
      setDialog(null);setToast('报工已保存。')
    })
  }
  function create(e:FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f=new FormData(e.currentTarget)
    void run(async()=>{
      setJob(null)
      if(dialog?.kind==='replan' && plan) {
        const code=field(f,'code')
        const complete=field(f,'completed_qty')
        const event=eventType==='insert' ? {type:'insert',code,model:field(f,'model'),qty:Number(field(f,'qty')),due:field(f,'due')} :
          eventType==='fault' ? {type:'fault',line:field(f,'line'),duration_minutes:Number(field(f,'duration')),
            completed_qty:complete===''?null:Number(complete)} :
            {type:'delay',code,available:field(f,'available')}
        await follow(await post<Job>('/plans/'+plan.id+'/replan',{at:field(f,'at'),freeze_hours:Number(field(f,'freeze')),events:eventType==='none'?[]:[event],use_actual:useActual,release_conflicting_locks:f.has('release_locks'),request_key:crypto.randomUUID()}))
      } else if(scope?.dataset) {
        await follow(await post<Job>('/jobs',{dataset_id:scope.dataset.id,at:field(f,'at'),end:field(f,'end'),request_key:crypto.randomUUID()}))
      }
    })
  }
  function apply(e:FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if(dialog?.kind!=='action') return
    const note=field(new FormData(e.currentTarget),'note')
    const {action, plan:current}=dialog
    void run(async()=>{
      const fresh=await post<Plan>('/plans/'+current.id+'/transition',{action,revision:current.revision,note})
      await sync(fresh.id);setDialog(null);setToast(verb[action]+'成功。');go('release')
    })
  }

  if(loading) return <div role="status" className="splash"><Aperture size={28}/> 正在连接计划工作台…</div>
  if(!user||!scope) return <LoginScreen config={config} busy={busy} error={error} onLogin={login} onDemo={demo}/>

  const planner=user.role==='planner'||user.role==='admin'
  const dataset=scope.dataset
  const candidates=scope.plans.filter(p=>p.job_id===plan?.job_id)
  const baseline=candidates.find(p=>p.strategy==='规则基准')
  const previous=scope.plans.find(p=>p.id===plan?.parent_id)
  const inFlow=scope.plans.filter(p=>p.status!=='draft')
  const allowedSections=sections.filter(s=>user.role!=='operator'||['overview','planning','orders','release','execution','audit'].includes(s.id))
  const name=sections.find(s=>s.id===page)?.name
  const analysisTime=dataset?.summary.last_order??''
  const stopTime=dataset ? localDate(timeValue(dataset.summary.last_due)+7*ONE_DAY) : ''
  const eventTime=runCutoff || (plan ? localDate(Math.min(timeValue(plan.at)+4*3_600_000,timeValue(plan.end)-60_000)) : '')
  const eventDue=eventTime ? localDate(timeValue(eventTime)+2*ONE_DAY) : ''
  const canOpenPlan = planner && !!dataset
  const canEdit = planner && plan?.status==='draft'
  const canUseActual = plan?.status==='received' && scope.plant.active_plan===plan.id
  const period=plan ? dateTime(plan.at)+' 至 '+dateTime(plan.end) :
    dataset ? dateTime(dataset.summary.first_order)+' 至 '+dateTime(dataset.summary.last_due) : '等待导入工厂快照'

  const primary = <button type="button" className="button primary" disabled={!canOpenPlan||busy} onClick={()=>open({kind:'run'})}><Plus size={17}/> 新建排程</button>
  const importButton = <button type="button" className="button quiet" onClick={()=>open({kind:'import'})}><Upload size={16}/> 导入数据</button>
  const exportButton = plan && <a className="button quiet" href={endpoint+'/plans/'+plan.id+'/export.xlsx'}><Download size={15}/> 导出计划</a>

  function pipelineActions(item:Plan) {
    const options=continueActions(item,user!,scope!.plant.active_plan)
    return options.map(a=><button type="button" key={a} className={'button '+(a==='reject'?'quiet':'primary')} onClick={()=>open({kind:'action',action:a,plan:item})}>{verb[a]}</button>)
  }
  function overview() {
    return <>
      <KPIs dataset={dataset} plan={plan} baseline={baseline}/>
      <div className="dashboard-grid">
        <Panel title="产线排程" detail={plan ? <><GitBranch size={14}/>{plan.strategy}<StatusBadge status={plan.status}/></> : undefined}
          actions={plan && <button className="button quiet compact-button" type="button" onClick={()=>go('planning')}>查看全量 <ChevronRight size={15}/></button>}>
          {plan ? <Timeline key={plan.id+':overview'} plan={plan} compact onBatch={b=>open({kind:'batch',batch:b})}/> :
            <Empty title="生成第一版生产计划" detail={dataset?'载入当前数据快照，比较交付、换型和均衡方案。':'导入一份包含订单、库存和产线的 Excel 工作簿。'}>
              {planner && (dataset?primary:importButton)}
              {planner&&!dataset&&config?.sample&&<button className="button quiet" type="button" onClick={()=>upload()}>读取原始样本</button>}
            </Empty>}
        </Panel>
        <DecisionRail scope={scope!} plan={plan} openFlow={()=>go('release')} onPick={p=>choose(p,'release')}/>
      </div>
      <Panel title="最近方案" detail="方案状态与交付指标" actions={<button className="button quiet compact-button" type="button" onClick={()=>go('planning')}>查看版本 <ArrowRight size={15}/></button>}>
        <Versions plans={scope!.plans.slice(0,6)} selected={plan?.id} onPick={p=>choose(p,'planning')}/>
      </Panel>
    </>
  }
  function planning() {
    return <>
      {plan && <Panel title={plan.strategy} detail={<><StatusBadge status={plan.status}/><span>{keyLabel(plan.id)}</span></>}
        actions={<>{exportButton}{pipelineActions(plan)}</>}>
        {plan.execution_mode&&<p className="review-note">{plan.execution_mode==='actual'?'已依据车间报工核算剩余量。':'此方案用原计划推定进度，用于模拟比较。'}</p>}
        {canEdit&&<p className="review-note edit-hint">拖动草案作业，或点击作业填写目标产线和开工时间。调整通过校验后会生成新草案。</p>}
        <Timeline key={plan.id+':full'} plan={plan} compact={false} onBatch={b=>open({kind:'batch',batch:b})}
          onMove={canEdit?(batch,line,start)=>open({kind:'manual',batch,line,start}):undefined}/>
      </Panel>}
      {plan&&<Panel title="计划作业" detail="可搜索作业号，查看时间或打开调整。">
        <BatchList key={plan.id} plan={plan} onBatch={batch=>open({kind:'batch',batch})}/>
      </Panel>}
      {plan && previous && <Panel title="版本前后对比" detail="查看交付、换型与调整任务的变化。">
        <Versions plans={[previous,plan]} selected={plan.id} onPick={p=>choose(p)}/>
      </Panel>}
      <Panel title={candidates.length>1?'同批方案比较':'计划版本'} detail={candidates.length>1?'选择任一候选，查看甘特图、订单和审批状态。':undefined}>
        <Versions plans={candidates.length>1?candidates:scope!.plans} selected={plan?.id} onPick={p=>choose(p)}/>
      </Panel>
      {candidates.length>1 && <Panel title="其他计划版本">
        <Versions plans={scope!.plans.filter(p=>p.job_id!==plan?.job_id)} selected={plan?.id} onPick={p=>choose(p)}/>
      </Panel>}
    </>
  }
  function release() {
    return <>
      {plan && <Panel title={plan.strategy} detail={<><StatusBadge status={plan.status}/><span>{keyLabel(plan.id)}</span></>}
        actions={<>{exportButton}{pipelineActions(plan)}</>}>
        <div className="release-steps" aria-label="计划审批进度">
          {(['draft','review','approved','published','received'] as const).map((state,index,all)=>
            <span className={index<=all.indexOf(plan.status)?'step reached':'step'} key={state}>
              <i>{index<all.indexOf(plan.status)?<Check size={13}/>:index+1}</i>{['草案','待审批','已批准','已发布','已签收'][index]}</span>)}
        </div>
        {plan.unassigned.length>0 && <div className="review-note">该方案仍有 {num(plan.metrics.missing_order_units)} 件需求未排，发布时请记录处置说明。</div>}
      </Panel>}
      <Panel title="流转队列" detail="查看已提交、发布及签收的计划">
        <Versions plans={inFlow} selected={plan?.id} onPick={p=>choose(p)}/>
      </Panel>
      {planner && <Panel title="待提交草案"><Versions plans={scope!.plans.filter(p=>p.status==='draft')} selected={plan?.id} onPick={p=>choose(p)}/></Panel>}
    </>
  }
  function dataPage() {
    return <Panel title="工厂数据快照" detail={dataset ? dataset.name : undefined} actions={planner&&importButton}>
      {!dataset ? <Empty title="还没有导入数据" detail="通过 Excel 导入订单、库存和生产资源。">
        {planner&&importButton}{planner&&config?.sample&&<button className="button quiet" type="button" onClick={()=>upload()}>读取原始样本</button>}
      </Empty> : <>
        <div className="snapshot"><div><strong>{num(dataset.summary.orders)}</strong><span>订单</span></div><div><strong>{num(dataset.summary.lines)}</strong><span>产线</span></div>
          <div><strong>{num(dataset.summary.production_units)}</strong><span>生产需求（件）</span></div><div><strong>{num(dataset.issues.length)}</strong><span>数据提示</span></div>
          <a className="button quiet" href={endpoint+'/datasets/'+dataset.id+'/audit.xlsx'}><Download size={16}/> 下载对账表</a></div>
        <div className="table-scroll scroll-issues"><table><thead><tr><th>数据提示</th><th>源工作表</th><th>行号</th><th>产品或产线</th><th>说明</th></tr></thead>
          <tbody>{dataset.issues.map((issue,i)=><tr key={i}><td>{issue.category}</td><td>{issue.sheet}</td><td>{issue.row||'—'}</td><td>{issue.reference||'—'}</td><td className="soft-cell">{issue.detail}</td></tr>)}</tbody></table></div>
      </>}
    </Panel>
  }

  const views:Record<Page,()=>ReactNode>={
    overview,planning,release,data:dataPage,
    orders:()=> <Panel title="订单交付" detail={plan ? plan.strategy+' · '+plan.metrics.orders+' 笔订单' : undefined}>
      {plan ? <OrdersTable key={plan.id} orders={plan.orders} onExplain={explain}/> :
        <Empty title="暂无订单交付结果" detail="先打开一个计划版本，查看库存覆盖、预计完工和未排原因。"/>}
    </Panel>,
    resources:()=> dataset ? <Resources dataset={dataset}/> : <Panel title="生产资源"><Empty title="暂无产线数据" detail="导入数据后查看产能、型号适配、换型和日历来源。"/></Panel>,
    execution:()=> <Panel title="车间报工" detail={executionView ? executionView.plan.strategy+' · '+keyLabel(executionView.plan.id) : undefined}
      actions={planner && executionView?.plan.status==='received' && <button type="button" className="button primary" onClick={()=>openReplan(executionView.plan,true,executionCutoff)}>按报工重排 <ArrowRight size={15}/></button>}>
      {executionLoading ? <div className="padded" role="status">正在读取当前发布版本和报工…</div> : executionView ? <>
        {executionView.plan.status==='published'&&<p className="review-note">车间签收后，可登记实际开工、完工和完成量。</p>}
        <ExecutionTable key={executionView.plan.id} plan={executionView.plan} records={executionView.entries}
          until={executionCutoff} onUntil={setExecutionCutoff}
          editable={executionView.plan.status==='received'&&(user.role==='operator'||user.role==='admin')}
          onRecord={(batch,entry)=>{setActualType(entry?.kind??'closed');open({kind:'actual',batch,entry,planId:executionView.plan.id})}}/>
      </> : <Empty title="暂无发布计划" detail="审批并发布计划后，车间可签收并登记作业。"/>}
    </Panel>,
    event:()=> <Panel title="基准计划" detail={plan ? plan.strategy+' · '+keyLabel(plan.id) : undefined}
      actions={plan&&planner&&<button type="button" className="button primary" onClick={()=>openReplan(plan,canUseActual)}><Plus size={17}/> 发起重排</button>}>
      {!plan ? <Empty title="请先选择计划" detail="从计划视图打开一个版本，作为突发事件的比较基准。"/> :
        <div className="padded"><StatusBadge status={plan.status}/><p>插单、停机或物料延迟会在独立候选中试算，当前发布版本保持有效。</p>
          {plan.changes.length>0 && <ul>{plan.changes.map((text,i)=><li key={i}>{text}</li>)}</ul>}
          <button className="button quiet" onClick={()=>go('planning')} type="button">查看基准版本 <ChevronRight size={15}/></button></div>}
    </Panel>,
    audit:()=> <Panel title="工厂操作记录" detail="记录导入、排程任务、审批、发布与车间签收">
      {scope.activity.length ? <div className="table-scroll"><table><thead><tr><th>操作</th><th>操作者</th><th>对象</th><th>时间</th><th>备注</th></tr></thead>
        <tbody>{scope.activity.map((entry,i)=><tr key={i}><td>{activityName[entry.event]??entry.event}</td><td>{entry.name}</td><td className="mono">{keyLabel(entry.object_id)}</td><td>{createdAt(entry.created)}</td><td className="soft-cell">{entry.note||'—'}</td></tr>)}</tbody></table></div>
       : <Empty title="暂无操作记录" detail="工厂内的操作会自动记录。"/>}
    </Panel>,
  }

  const dialogTitles={import:'导入排程数据',run:'新建排程',replan:'突发事件重排',action:dialog?.kind==='action'?verb[dialog.action]:'计划流转',batch:'作业详情',evidence:'订单排程证据',manual:'调整草案作业',actual:'登记作业执行'}
  return <div className="app-shell">
    <aside className={'sidebar '+(drawer?'drawer-open':'')}>
      <button className="shell-brand" type="button" onClick={()=>go('overview')} aria-label="新大地首页"><span className="brand-mark"><Aperture size={24}/></span>
        <span><b>新大地 APS</b><small>生产计划运营</small></span></button>
      <div className="plant-lock"><Factory size={18}/><div><b>{scope.plant.name}</b><small>{config?.demo?'本机试点':'生产工作区'}</small></div></div>
      <nav aria-label="工作台导航">{allowedSections.map((bar,i)=><div key={bar.id}>
        {(i===0||bar.id==='release')&&<span className="nav-label">{i===0?'计划运营':'流程管理'}</span>}
        <button className={'nav-entry '+(bar.id===page?'nav-active':'')} type="button" aria-current={bar.id===page?'page':undefined}
          onClick={()=>go(bar.id)}><bar.icon size={18}/><span>{bar.name}</span>
          {bar.id==='release' && scope.plans.some(p=>p.status==='review') && <i className="nav-count">{scope.plans.filter(p=>p.status==='review').length}</i>}
        </button></div>)}</nav>
      <div className="sidebar-foot"><span className="live-dot"/><span>工厂工作区</span><ShieldCheck size={16}/></div>
    </aside>
    {drawer&&<button type="button" className="menu-scrim" onClick={()=>setDrawer(false)} aria-label="关闭导航"/>}
    <div className="main-shell">
      <header className="topbar">
        <button className="icon-button menu-action" type="button" onClick={()=>setDrawer(!drawer)} aria-label="打开导航"><Menu size={21}/></button>
        <div className="breadcrumb"><span>工作区</span><ChevronRight size={13}/><strong>{name}</strong></div>
        <div className="topbar-right">
          <div className="user-menu"><button type="button" className="profile" onClick={()=>setDismissUser(!dismissUser)} aria-expanded={dismissUser} aria-label="角色和账户"><span className="avatar">{user.name.slice(0,1)}</span>
            <span className="profile-text"><b>{user.name}</b>{user.name!==roleName[user.role]&&<small>{roleName[user.role]}</small>}</span><ChevronDown size={14}/></button>
          {dismissUser&&<div className="profile-popover">{config?.demo && config.roles.filter(r=>r.id!==user.role).map(r=>
            <button type="button" key={r.id} onClick={()=>demo(r.id)}><Workflow size={15}/>切换为{r.name}</button>)}
            <button type="button" onClick={logout}><LogOut size={15}/>退出登录</button></div>}</div>
        </div>
      </header>
      <main className="main-content">
        <div className="page-head"><div><h1>{name}</h1><p>{page==='overview' || page==='planning' ? period :
          page==='orders' ? '按订单追溯库存覆盖、计划完工和未排原因。' :
          page==='release' ? '选择方案、确认审核、发布并记录车间签收。' :
          page==='data' ? '导入、校验和下载当前工作簿对账。' :
          page==='resources' ? '查看当前工厂的数据快照与产线能力。' :
          page==='execution' ? '在当前签收版本上登记或核对实际完成量。' :
          page==='event' ? '基于固定版本比较紧急变化后的生产计划。' : '追溯工厂内关键计划操作。'}</p></div>
          {(page==='overview'||page==='planning')&&planner&&<div className="page-actions">{importButton}{primary}</div>}
        </div>
        {toast&&<div className="toast" role="status"><Check size={17}/>{toast}<button type="button" className="text-button" onClick={()=>setToast('')}>关闭</button></div>}
        {error&&!dialog&&<div className="warning-box" role="alert">{error}</div>}
        {views[page]()}
      </main>
    </div>
    {dialog&&<Suspense fallback={<div className="dialog-loading" role="status">正在打开…</div>}><Modal title={dialogTitles[dialog.kind]} open footer={null} onCancel={()=>{if(!busy){setDialog(null);setError('')}}}
      maskClosable={!busy} keyboard={!busy} closable={!busy} width={540} destroyOnHidden>
      {error&&dialog&&<div className="warning-box" role="alert">{error}</div>}
      {dialog?.kind==='import'&&<><form className="form-stack" onSubmit={upload}><label>新大地排程工作簿<input type="file" name="file" accept=".xlsx" required/></label>
        <div className="modal-actions"><button type="submit" className="button primary" disabled={busy}><Upload size={15}/> 校验并导入</button></div></form>
        {config?.sample&&<button type="button" className="text-button" disabled={busy} onClick={()=>upload()}>读取新大地原始样本</button>}</>}
      {dialog?.kind==='run'&&dataset&&<form className="form-stack" onSubmit={create}>
        <p className="muted">{dataset.name} · {dataset.summary.orders} 笔订单 · {dataset.summary.lines} 条产线</p>
        <label>排产时点<input name="at" type="datetime-local" defaultValue={analysisTime} required/></label>
        <label>规划结束<input name="end" type="datetime-local" defaultValue={stopTime} required/></label>
        <div className="modal-actions"><JobProgress value={job}/><button type="submit" className="button primary" disabled={busy}>运行排程</button></div></form>}
      {dialog?.kind==='replan'&&plan&&<form className="form-stack" onSubmit={create}>
        <div className="form-pair"><label>重排时点<input type="datetime-local" name="at" defaultValue={eventTime} required/></label>
          <label>冻结小时<input type="number" name="freeze" defaultValue={8} min={0} max={48} required/></label></div>
        {canUseActual && <label className="check-row"><input type="checkbox" checked={useActual} onChange={e=>{
          setUseActual(e.target.checked)
          if(!e.target.checked&&eventType==='none')setEventType('insert')
        }}/>依据车间已登记的实际完成量</label>}
        <p className="muted">{useActual?'核对时点前的作业需要报工，或标记未开工。':'模拟将按原计划推定已过时点的作业进度。'}</p>
        <label>事件类型<select value={eventType} onChange={e=>setEventType(e.target.value)}>
          {useActual&&<option value="none">仅按报工重排</option>}<option value="insert">紧急插单</option><option value="fault">产线故障</option><option value="delay">物料延期</option></select></label>
        {eventType==='insert' ? <><div className="form-pair"><label>产品编码<input name="code" maxLength={70} required/></label><label>产品型号<input name="model" maxLength={100} required/></label></div>
          <div className="form-pair"><label>数量<input type="number" name="qty" min={1} defaultValue={3000} required/></label><label>交期<input type="datetime-local" name="due" defaultValue={eventDue} required/></label></div></> :
          eventType==='fault' ? <><label>故障产线<select name="line" required>{plan.lines.map(l=><option key={l}>{l}</option>)}</select></label>
            <div className="form-pair"><label>停机分钟<input type="number" name="duration" defaultValue={240} min={1} required/></label>
            {!useActual&&<label>停机前实际完工量<input type="number" name="completed_qty" min={0} placeholder="在制故障时必填"/></label>}</div></> : eventType==='none' ? null :
            <><label>受影响的产品编码<input name="code" required/></label><label>物料可用时点<input type="datetime-local" name="available" defaultValue={eventDue} required/></label></>}
        {['fault','delay'].includes(eventType)&&plan.batches.some(batch=>batch.state==='locked')&&
          <label className="check-row"><input name="release_locks" type="checkbox"/>仅解除与本次事件冲突的手工锁定</label>}
        <div className="modal-actions"><JobProgress value={job}/><button className="button primary" disabled={busy} type="submit">生成重排方案</button></div>
      </form>}
      {dialog?.kind==='action'&&<form className="form-stack" onSubmit={apply}><p>{dialog.plan.strategy} · {keyLabel(dialog.plan.id)} · <StatusBadge status={dialog.plan.status}/></p>
        <label>{dialog.action==='publish' && dialog.plan.unassigned.length ? '未排任务处置说明' : '操作备注'}
          <textarea name="note" rows={3} maxLength={500} required={dialog.action==='publish'&&dialog.plan.unassigned.length>0}/></label>
        <div className="modal-actions"><button className="button primary" disabled={busy} type="submit">{verb[dialog.action]}</button></div></form>}
      {dialog?.kind==='batch'&&<div className="evidence"><dl><dt>产线</dt><dd>{dialog.batch.line}</dd><dt>产品</dt><dd>{dialog.batch.model} · {dialog.batch.code}</dd>
        <dt>数量</dt><dd>{num(dialog.batch.qty)} 件</dd><dt>开工</dt><dd>{dateTime(dialog.batch.start)}</dd><dt>完工</dt><dd>{dateTime(dialog.batch.end)}</dd>
        <dt>换型</dt><dd>{dialog.batch.setup_minutes} 分钟</dd><dt>状态</dt><dd>{dialog.batch.status}</dd></dl>
        <div className="evidence-actions">
          {dialog.batch.order_id&&<button type="button" className="button quiet" onClick={()=>explain(dialog.batch.order_id!)}>追溯订单 <ArrowRight size={15}/></button>}
          {canEdit&&['locked','planned'].includes(dialog.batch.state)&&<button type="button" className="button primary" onClick={()=>{
            const batch=dialog.batch
            open({kind:'manual',batch,line:batch.line,start:batch.start})
          }}>{dialog.batch.state==='locked'?'解锁作业':'调整作业'}</button>}
        </div></div>}
      {dialog?.kind==='manual'&&plan&&<form className="form-stack" onSubmit={manual}>
        <p>{dialog.batch.job} · {dialog.batch.model} · {num(dialog.batch.qty)} 件</p>
        <label>目标产线<select name="line" defaultValue={dialog.line} disabled={dialog.batch.state==='locked'} required>
          {plan.lines.map(line=><option key={line}>{line}</option>)}</select></label>
        <label>计划开工<input name="start" type="datetime-local" defaultValue={dialog.start} readOnly={dialog.batch.state==='locked'} required/></label>
        <label className="check-row"><input type="checkbox" name="locked" defaultChecked={dialog.batch.state==='locked'}/>锁定此作业的产线和时间</label>
        <p className="muted">{dialog.batch.state==='locked'?'取消锁定并保存草案后，即可移动该作业。':'系统会重算换型并检查占用冲突。原计划保留。'}</p>
        <div className="modal-actions"><button className="button primary" type="submit" disabled={busy}>校验并保存草案</button></div>
      </form>}
      {dialog?.kind==='actual'&&<form className="form-stack" onSubmit={saveActual}>
        <p>{dialog.batch.line} · {dialog.batch.job} · 计划 {num(dialog.batch.qty)} 件</p>
        <label>执行结果<select value={actualType} onChange={event=>setActualType(event.target.value as typeof actualType)}>
          <option value="closed">实际结批</option><option value="not_started">确认未开工</option></select></label>
        {actualType==='closed'&&<>
          <label>实际完成量<input type="number" name="qty" min={0} max={dialog.batch.qty} defaultValue={dialog.entry?.qty??''} required/></label>
          <div className="form-pair"><label>实际开工<input type="datetime-local" name="start" defaultValue={dialog.entry?.start??''} required/></label>
          <label>实际结束<input type="datetime-local" name="end" defaultValue={dialog.entry?.end??''} required/></label></div>
          <p className="muted">如已停工但未做完，填实际结束时间和合格完成量，余量在重排中重新安排。</p>
        </>}
        {actualType==='not_started'&&<label>确认截至时点<input type="datetime-local" name="checked_at" defaultValue={dialog.entry?.checked_at??executionCutoff} required/></label>}
        <div className="modal-actions"><button type="submit" className="button primary" disabled={busy}>保存报工</button></div>
      </form>}
      {dialog?.kind==='evidence'&&<EvidenceView e={dialog.evidence}/>}
    </Modal></Suspense>}
  </div>
}

function JobProgress({value}:{value:Job|null}) {
  if(!value||value.status==='failed'||value.status==='succeeded') return null
  return <span role="status" className="progress"><i/>{value.status==='queued'?'已进入后台队列':'正在求解及校验'}</span>
}
function EvidenceView({e}:{e:Evidence}) {
  const o=e.order
  return <div className="evidence"><Badge tone={o.status==='未排完'?'review':'approved'}>{o.status}</Badge>
    <p className="muted">{e.source} · {keyLabel(e.plan_id)}</p>
    <dl><dt>订单</dt><dd>{o.id} · {o.code}</dd><dt>库存覆盖</dt><dd>{num(o.stock)} 件</dd><dt>安排生产</dt><dd>{num(o.planned)} / {num(o.qty-o.stock)} 件</dd>
      <dt>订单交期</dt><dd>{dateTime(o.due)}</dd><dt>预计完工</dt><dd>{dateTime(o.completion)}</dd>
      {o.tardy_hours!==null&&o.tardy_hours>0&&<><dt>预计延期</dt><dd>{num(o.tardy_hours)} 小时</dd></>}</dl>
    {e.unassigned.map(g=><div key={g.job} className="warning-box">{g.reason} · {num(g.qty)} 件</div>)}
    {e.batches.map((b,i)=><div key={b.job+i} className="evidence-source"><b>{b.line}</b> {dateTime(b.start)} 至 {dateTime(b.end)} · {num(b.qty)} 件</div>)}
  </div>
}
