export type Role = 'planner' | 'reviewer' | 'operator' | 'admin'
export type Status = 'draft' | 'review' | 'approved' | 'published' | 'received'
export type Action = 'submit' | 'approve' | 'reject' | 'publish' | 'receive'
export type User = { id: string; plant_id: string; name: string; role: Role }
export type Config = { demo: boolean; sample: boolean; roles: { id: Role; name: string }[] }
export type Metrics = {
  orders: number; on_time: number; otd: number; weighted_tardy_hours: number; setup_hours: number;
  load_cv: number; pending_orders: number; missing_order_units: number; shortfall_units: number;
  makespan_hours: number; moved: number; comparable: number;
}
export type Dataset = {
  id: string; name: string; created: number; lines: string[];
  summary: { orders: number; lines: number; codes: number; order_units: number; positive_codes: number;
    net_units: string; production_units: number; first_order: string; last_order: string; last_due: string };
  issues: { category: string; sheet: string; row: number; reference: string; detail: string }[];
  resources: { id: string; families: string[]; capacity: number; major: number | null;
    minor: number; cycle: number; calendar: boolean }[];
}
export type PlanSummary = {
  id: string; job_id: string; dataset_id: string; parent_id?: string; strategy: string;
  status: Status; revision: number; submitted_by: string | null; execution_mode?: 'actual' | 'simulation' | null; created: number; at: string; end: string; metrics: Metrics;
}
export type Batch = {
  key: string; line: string; job: string; order_id: string | null; code: string; model: string; qty: number;
  start: string; end: string; setup_start: string; setup_minutes: number; state: string; status: string;
}
export type Order = {
  id: string; customer: string; code: string; qty: number; stock: number; planned: number;
  due: string; completion: string | null; tardy_hours: number | null; status: string;
}
export type Gap = { job: string; order_id: string | null; qty: number; code: string; model: string; reason: string }
export type Plan = PlanSummary & {
  start: string; lines: string[]; batches: Batch[]; orders: Order[]; unassigned: Gap[]; changes: string[];
}
export type Activity = { event: string; note: string; created: number; object_id: string; name: string }
export type Workspace = {
  plant: { id: string; name: string; active_plan: string | null; latest_dataset: string | null };
  dataset: Dataset | null; plans: PlanSummary[]; activity: Activity[];
}
export type Job = {
  id: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; error: string | null;
  output: { plan_ids: string[]; timings: { request_seconds: number } } | null;
}
export type ActualEntry = { batch_key:string; line:string; kind:'closed'|'not_started'; qty:number; start:string|null; end:string|null; checked_at:string|null; revision:number }
export type Evidence = { source: string; plan_id: string; order: Order; batches: Batch[]; unassigned: Gap[] }
