<template>
  <section class="page" data-module="patrol_route">
    <header class="page-head">
      <div>
        <h2>巡查任务路线台账</h2>
        <p class="page-desc">台账直接从巡查记录、巡查路段、巡查人员生成；排班变化、路段停用、两人同时认领时可调整路线顺序与责任人并留下冲突提示；台账、地图轨迹、巡查记录明细始终同一状态，撤回不保留旧路线。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="generateLedgers">从巡查记录生成台账</button>
        <button class="btn" type="button" @click="exportRows">导出台账</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="{ warn: item.warn }">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>台账/巡查编号/责任人</span>
        <input v-model="keyword" placeholder="按台账编号、巡查编号或责任人检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>台账编号</th>
          <th>关联巡查编号</th>
          <th>责任人</th>
          <th>路线段数</th>
          <th>状态</th>
          <th>巡查记录状态</th>
          <th>冲突提示</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ 'row-conflict': row.冲突数 > 0 }">
          <td>{{ row.台账编号 }}</td>
          <td>{{ row.关联巡查编号 }}</td>
          <td>{{ row.责任人 || '—' }}</td>
          <td>{{ row.路线段数 }}</td>
          <td><span class="status-tag" :class="statusClass(row.状态)">{{ row.状态 }}</span></td>
          <td>{{ row.巡查记录状态 ?? '—' }}</td>
          <td>
            <span v-if="row.冲突数" class="conflict-pill">{{ row.冲突数 }} 条冲突</span>
            <span v-else class="muted">无</span>
          </td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row.id)">详情/调整</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="8" class="empty-state">暂无路线台账，可点击右上方「从巡查记录生成台账」</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 份台账</span>
      <span v-if="messageText" :class="messageKind === 'error' ? 'error-text' : 'warn-text'">{{ messageText }}</span>
    </footer>

    <!-- 台账明细 / 路线调整 / 轨迹 -->
    <div v-if="detail" class="drawer-mask" @click.self="closeDetail">
      <section class="drawer">
        <header class="drawer-head">
          <div>
            <h3>{{ detail.台账编号 }} <small>关联 {{ detail.关联巡查编号 }}</small></h3>
            <div class="consistency">
              <span
                v-for="part in statusParts"
                :key="part.label"
                class="status-pill"
                :class="statusesConsistent ? 'ok' : 'bad'"
              >{{ part.label }}：{{ part.value ?? '—' }}</span>
              <span v-if="detail.状态 === '已撤回'" class="withdraw-note">已撤回：旧路线与地图轨迹已清空，不再保留</span>
              <span v-else-if="statusesConsistent" class="consistent-note">三处状态一致</span>
              <span v-else class="conflict-note">状态不一致，请刷新或重新执行动作</span>
            </div>
          </div>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </header>

        <div v-if="detail.冲突提示.length" class="conflict-box">
          <strong>冲突提示（{{ detail.冲突提示.length }}）</strong>
          <ul>
            <li v-for="(item, index) in detail.冲突提示" :key="index">⚠ {{ item }}</li>
          </ul>
        </div>

        <div class="drawer-body">
          <div class="panel">
            <h4>路线顺序（{{ editableStops.length }} 段）</h4>
            <template v-if="editableStops.length">
              <ol class="stop-list">
                <li v-for="(stop, index) in editableStops" :key="stop.路段">
                  <span class="stop-seq">{{ index + 1 }}</span>
                  <span class="stop-name">{{ stop.路段 }}</span>
                  <span v-if="stop.停用" class="disabled-tag">已停用</span>
                  <span class="stop-actions" v-if="canAdjust">
                    <button class="link" type="button" :disabled="index === 0" @click="moveStop(index, -1)">上移</button>
                    <button class="link" type="button" :disabled="index === editableStops.length - 1" @click="moveStop(index, 1)">下移</button>
                    <button
                      v-if="!stop.停用"
                      class="link danger"
                      type="button"
                      @click="disableStop(stop.路段)"
                    >停用</button>
                    <button class="link danger" type="button" @click="removeStop(index)">移除</button>
                  </span>
                </li>
              </ol>
              <p v-if="!orderSaved" class="hint">顺序有未保存的调整，保存后生效。</p>
              <button
                v-if="canAdjust"
                class="btn"
                type="button"
                :disabled="orderSaved"
                @click="saveOrder"
              >保存路线顺序</button>
            </template>
            <p v-else-if="detail.状态 === '已撤回'" class="muted">任务已撤回，旧路线未保留；需要时请重新生成台账。</p>
            <p v-else class="muted">路线为空。</p>
          </div>

          <div class="panel">
            <h4>地图轨迹</h4>
            <div v-if="track && track.points.length" class="track-wrap">
              <svg :viewBox="`0 0 ${TRACK_W} ${TRACK_H}`" class="track-svg">
                <polyline
                  :points="trackPath"
                  fill="none"
                  stroke="#1f6feb"
                  stroke-width="2"
                  stroke-dasharray="5 4"
                />
                <template v-for="(point, index) in trackPoints" :key="`${point.seq}-${index}`">
                  <circle :cx="point.x" :cy="point.y" r="5" :fill="point.停用 ? '#d92d20' : '#1f6feb'" />
                  <text :x="point.x + 8" :y="point.y - 8" class="track-label">{{ point.seq }}.{{ point.路段 }}</text>
                </template>
              </svg>
              <p class="hint">蓝点为正常路段，红点为已停用路段；轨迹随台账路线实时变化。</p>
            </div>
            <p v-else class="muted">无轨迹数据{{ detail.状态 === '已撤回' ? '（撤回后旧轨迹已清空）' : '' }}</p>
          </div>

          <div v-if="canAdjust" class="panel">
            <h4>认领与排班调整</h4>
            <div v-if="detail.状态 === '待认领'" class="inline-form">
              <input v-model="claimName" placeholder="认领人姓名" />
              <button class="btn primary" type="button" @click="claim">确认认领</button>
              <span class="hint">若认领人与排班责任人不一致，会留下冲突提示。</span>
            </div>
            <div class="inline-form">
              <input v-model="ownerName" placeholder="新责任人（排班变化）" />
              <input v-model="ownerReason" placeholder="调整原因（选填）" />
              <button class="btn" type="button" @click="changeOwner">调整责任人</button>
            </div>
          </div>

          <div v-if="availableActions.length" class="panel">
            <h4>状态流转</h4>
            <div class="inline-form">
              <button
                v-for="action in availableActions"
                :key="action"
                class="btn"
                :class="{ primary: action !== '撤回', danger: action === '撤回' }"
                type="button"
                @click="runAction(action)"
              >{{ action }}</button>
            </div>
          </div>

          <div class="panel">
            <h4>调整记录</h4>
            <ol class="log-list">
              <li v-for="(item, index) in detail.调整记录" :key="index">{{ item }}</li>
            </ol>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Stop = { seq: number; 路段: string; 坐标: [number, number]; 停用: boolean }
type Ledger = {
  id: number
  台账编号: string
  巡查记录ID: number
  关联巡查编号: string
  责任人: string
  路线: Stop[]
  status: string
  状态: string
  pending: boolean
  abnormal: boolean
  冲突提示: string[]
  调整记录: string[]
  路线段数: number
  冲突数: number
  巡查记录状态: string | null
}
type TrackPoint = { seq: number; 路段: string; lng: number; lat: number; 停用: boolean }
type Track = { 台账编号: string; 状态: string; 巡查记录状态: string | null; points: TrackPoint[] }
type ActionResponse = { ok: boolean; message: string; entry?: Ledger }

const ENDPOINT = '/api/patrol_route'
const statuses = ['待认领', '已认领', '巡查中', '已完成', '已撤回']
const ACTION_MAP: Record<string, string[]> = {
  待认领: ['撤回'],
  已认领: ['开始巡查', '撤回'],
  巡查中: ['完成巡查'],
  已完成: [],
  已撤回: [],
}

const TRACK_W = 520
const TRACK_H = 200
const TRACK_PAD = 30

const rows = ref<Ledger[]>([])
const total = ref(0)
const keyword = ref('')
const statusFilter = ref('')
const messageText = ref('')
const messageKind = ref<'info' | 'error'>('info')

const detail = ref<Ledger | null>(null)
const track = ref<Track | null>(null)
const editableStops = ref<Stop[]>([])
const originalOrder = ref<string[]>([])
const claimName = ref('')
const ownerName = ref('')
const ownerReason = ref('')

const stats = computed(() => [
  { label: '待认领', value: rows.value.filter((r) => r.状态 === '待认领').length, warn: false },
  { label: '巡查中', value: rows.value.filter((r) => r.状态 === '巡查中').length, warn: false },
  { label: '带冲突提示', value: rows.value.filter((r) => r.冲突数 > 0).length, warn: true },
  { label: '已撤回', value: rows.value.filter((r) => r.状态 === '已撤回').length, warn: false },
])

const canAdjust = computed(() => {
  const status = detail.value?.状态
  return status === '待认领' || status === '已认领' || status === '巡查中'
})

const availableActions = computed(() => (detail.value ? ACTION_MAP[detail.value.状态] ?? [] : []))

const statusParts = computed(() => {
  if (!detail.value || !track.value) return []
  return [
    { label: '台账状态', value: detail.value.状态 },
    { label: '地图轨迹', value: track.value.状态 },
    { label: '巡查记录明细', value: detail.value.巡查记录状态 },
  ]
})

const statusesConsistent = computed(() => {
  if (!detail.value || !track.value) return false
  const ledger = detail.value.状态
  const trackStatus = track.value.状态
  const inspectStatus = detail.value.巡查记录状态
  if (ledger !== trackStatus) return false
  if (ledger === '巡查中') return inspectStatus === '巡查中'
  if (ledger === '已完成') return inspectStatus === '已完成' || inspectStatus === '已转病害'
  if (ledger === '已撤回') return inspectStatus === '已排班'
  return inspectStatus === '已排班' // 待认领/已认领对应巡查记录仍在排班
})

const orderSaved = computed(() => {
  const current = editableStops.value.map((stop) => stop.路段)
  return current.length === originalOrder.value.length
    && current.every((name, index) => name === originalOrder.value[index])
})

const trackPoints = computed(() => {
  const points = track.value?.points ?? []
  if (!points.length) return []
  const lngs = points.map((p) => p.lng)
  const lats = points.map((p) => p.lat)
  const minLng = Math.min(...lngs)
  const maxLng = Math.max(...lngs)
  const minLat = Math.min(...lats)
  const maxLat = Math.max(...lats)
  return points.map((point) => ({
    ...point,
    x: TRACK_PAD + (maxLng === minLng ? 0.5 : (point.lng - minLng) / (maxLng - minLng)) * (TRACK_W - 2 * TRACK_PAD),
    y: TRACK_H - TRACK_PAD - (maxLat === minLat ? 0.5 : (point.lat - minLat) / (maxLat - minLat)) * (TRACK_H - 2 * TRACK_PAD),
  }))
})

const trackPath = computed(() =>
  trackPoints.value.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' '),
)

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function statusClass(status: string) {
  return {
    'st-active': status === '巡查中',
    'st-done': status === '已完成',
    'st-off': status === '已撤回',
  }
}

function notify(message: string, kind: 'info' | 'error' = 'info') {
  messageText.value = message
  messageKind.value = kind
}

async function generateLedgers() {
  try {
    const result = await postAction(`${ENDPOINT}/generate`, undefined, 'POST')
    notify(result.message, result.ok ? 'info' : 'error')
    await reload()
  } catch (error) {
    notify(error instanceof Error ? error.message : '台账生成失败', 'error')
  }
}

async function postAction(
  url: string,
  values: Record<string, unknown> | undefined,
  method: 'POST' | 'PUT' = 'POST',
): Promise<ActionResponse> {
  const response = await request(url, {
    method,
    body: JSON.stringify(values === undefined ? {} : { values }),
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null
    throw new Error(payload?.detail ?? `接口返回 ${response.status}`)
  }
  return (await response.json()) as ActionResponse
}

async function reload() {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  query.set('size', '200')
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('台账列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    notify(error instanceof Error ? error.message : '台账列表读取失败', 'error')
  }
}

async function openDetail(id: number) {
  messageText.value = ''
  try {
    const [ledgerResponse, trackResponse] = await Promise.all([
      request(`${ENDPOINT}/${id}`),
      request(`${ENDPOINT}/${id}/track`),
    ])
    if (!ledgerResponse.ok || !trackResponse.ok) throw new Error('台账明细读取失败')
    detail.value = (await ledgerResponse.json()) as Ledger
    track.value = (await trackResponse.json()) as Track
    editableStops.value = detail.value.路线.map((stop) => ({ ...stop, 坐标: [...stop.坐标] as [number, number] }))
    originalOrder.value = editableStops.value.map((stop) => stop.路段)
    claimName.value = detail.value.责任人
    ownerName.value = detail.value.责任人
    ownerReason.value = ''
  } catch (error) {
    notify(error instanceof Error ? error.message : '台账明细读取失败', 'error')
  }
}

function closeDetail() {
  detail.value = null
  track.value = null
  editableStops.value = []
  originalOrder.value = []
}

async function refreshDetail() {
  if (!detail.value) return
  const id = detail.value.id
  await reload()
  await openDetail(id)
}

function moveStop(index: number, direction: number) {
  const target = index + direction
  if (target < 0 || target >= editableStops.value.length) return
  const list = [...editableStops.value]
  const [item] = list.splice(index, 1)
  list.splice(target, 0, item)
  editableStops.value = list.map((stop, i) => ({ ...stop, seq: i + 1 }))
}

function removeStop(index: number) {
  editableStops.value = editableStops.value
    .filter((_, i) => i !== index)
    .map((stop, i) => ({ ...stop, seq: i + 1 }))
}

async function disableStop(name: string) {
  await submitAdjust({ 停用路段: name }, `路段「${name}」已停用，记得调整路线顺序或移除`)
}

async function saveOrder() {
  await submitAdjust({ 路线顺序: editableStops.value.map((stop) => stop.路段) }, '路线顺序已保存')
}

async function changeOwner() {
  const owner = ownerName.value.trim()
  if (!owner) {
    notify('请填写新责任人', 'error')
    return
  }
  await submitAdjust({ 责任人: owner, 原因: ownerReason.value.trim() }, `责任人已调整为 ${owner}`)
}

async function submitAdjust(values: Record<string, unknown>, successMessage: string) {
  if (!detail.value) return
  try {
    const result = await postAction(`${ENDPOINT}/${detail.value.id}/adjust`, values, 'PUT')
    notify(result.ok ? successMessage : result.message, result.ok ? 'info' : 'error')
    if (result.ok) await refreshDetail()
  } catch (error) {
    notify(error instanceof Error ? error.message : '调整失败', 'error')
  }
}

async function claim() {
  if (!detail.value) return
  const name = claimName.value.trim()
  if (!name) {
    notify('请填写认领人', 'error')
    return
  }
  await runAction('认领', { 认领人: name })
}

async function runAction(action: string, extra: Record<string, unknown> = {}) {
  if (!detail.value) return
  if (action === '撤回'
    && !window.confirm('撤回后旧路线与地图轨迹将被清空且不能保留，确定撤回该任务？')) {
    return
  }
  try {
    const result = await postAction(`${ENDPOINT}/${detail.value.id}/actions`, { action, ...extra })
    notify(result.message, result.ok ? 'info' : 'error')
    if (result.ok) {
      await refreshDetail()
      if (action === '撤回') closeDetail()
    }
  } catch (error) {
    notify(error instanceof Error ? error.message : '动作执行失败', 'error')
  }
}

onMounted(reload)
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.warn-text { color: #b54708; }
.warn { color: #b54708; }
.muted { color: var(--muted); font-size: 12px; }
.row-conflict { background: #fffaeb; }
.status-tag { padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #eef2f7; }
.st-active { background: #e0f2fe; color: #0369a1; }
.st-done { background: #dcfce7; color: #15803d; }
.st-off { background: #f1f5f9; color: #64748b; }
.conflict-pill { color: #b42318; background: #fee4e2; padding: 2px 8px; border-radius: 10px; font-size: 12px; }

.drawer-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); z-index: 20; display: flex; justify-content: flex-end; }
.drawer { width: 760px; max-width: 94vw; background: #fff; height: 100%; overflow-y: auto; padding: 16px 20px; box-shadow: -8px 0 24px rgba(15, 23, 42, 0.2); }
.drawer-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
.drawer-head h3 { margin: 0 0 8px; }
.drawer-head small { color: var(--muted); font-weight: 400; }
.consistency { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.status-pill { font-size: 12px; padding: 2px 8px; border-radius: 10px; }
.status-pill.ok { background: #dcfce7; color: #15803d; }
.status-pill.bad { background: #fee4e2; color: #b42318; }
.consistent-note { font-size: 12px; color: #15803d; }
.conflict-note, .withdraw-note { font-size: 12px; color: #b54708; }

.conflict-box { background: #fffaeb; border: 1px solid #fedf89; border-radius: 8px; padding: 8px 12px; margin: 12px 0; }
.conflict-box ul { margin: 6px 0 0; padding-left: 18px; }
.conflict-box li { color: #b54708; font-size: 13px; line-height: 1.7; }

.drawer-body { display: flex; flex-direction: column; gap: 14px; margin-top: 12px; }
.panel { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.panel h4 { margin: 0 0 8px; font-size: 14px; }

.stop-list { list-style: none; margin: 0 0 8px; padding: 0; }
.stop-list li { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px dashed var(--border); font-size: 13px; }
.stop-seq { width: 20px; height: 20px; border-radius: 50%; background: var(--brand); color: #fff; font-size: 12px; display: inline-flex; align-items: center; justify-content: center; }
.stop-name { flex: 1; }
.stop-actions { display: flex; gap: 8px; }
.disabled-tag { background: #fee4e2; color: #b42318; border-radius: 10px; padding: 1px 8px; font-size: 12px; }
.danger { color: #b42318; }
.hint { font-size: 12px; color: var(--muted); margin: 0 8px 8px 0; }
.link:disabled { color: #94a3b8; cursor: not-allowed; }

.track-wrap { border: 1px solid var(--border); border-radius: 8px; padding: 8px; }
.track-svg { width: 100%; height: auto; background: #f8fafc; }
.track-label { font-size: 11px; fill: #334155; }

.inline-form { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 6px; }
.inline-form input { border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; min-width: 160px; }

.log-list { margin: 0; padding-left: 18px; font-size: 12px; color: #475569; max-height: 180px; overflow-y: auto; }
.log-list li { line-height: 1.8; }
</style>
