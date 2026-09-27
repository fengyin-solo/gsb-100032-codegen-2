<template>
  <section class="page" data-module="inspect_route">
    <header class="page-head">
      <div>
        <h2>巡查任务路线台账</h2>
        <p class="page-desc">
          台账由巡查记录、巡查路段、巡查人员实时生成；可调整路线顺序与责任人，
          排班变化、路段停用、两人同时认领都会留下冲突提示。台账、地图轨迹与巡查记录明细同源同状态。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新台账</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="conflictFeed.length" class="conflict-feed">
      <strong>当前冲突提示（{{ conflictFeed.length }}）：</strong>
      <span v-for="item in conflictFeed" :key="`${item.inspect_id}-${item.section_code}-${item.kind}`" class="conflict-chip">
        {{ item['巡查编号'] }} · {{ item.kind }} · {{ item.section_code }}
      </span>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>巡查编号</th>
          <th>巡查日期</th>
          <th>管线类型</th>
          <th>台账状态</th>
          <th>停靠点</th>
          <th>已认领</th>
          <th>冲突</th>
          <th>版本</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.inspect_id)" :class="{ 'row-conflict': row.conflict_count > 0 }">
          <td>{{ row['巡查编号'] }}</td>
          <td>{{ row['巡查日期'] }}</td>
          <td>{{ row['管线类型'] }}</td>
          <td>
            <span class="status-tag">{{ row.status }}</span>
            <span v-if="row.generated" class="muted-inline">已生成</span>
            <span v-else class="muted-inline">未生成</span>
            <span v-if="row.adjusted" class="muted-inline">·已调整</span>
          </td>
          <td>{{ row.stop_count }}</td>
          <td>{{ row.claimed_count }}</td>
          <td>
            <span v-if="row.conflict_count" class="conflict-count">{{ row.conflict_count }} 条</span>
            <span v-else class="muted-inline">无</span>
          </td>
          <td>v{{ row.version }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row.inspect_id)">
              {{ row.generated ? '打开台账' : '查看/生成' }}
            </button>
            <button
              v-if="row.generated"
              class="link danger-link"
              type="button"
              @click="withdraw(row.inspect_id)"
            >
              撤回
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="9" class="empty-state">暂无巡查任务，可先到巡查记录页登记</td>
        </tr>
      </tbody>
    </table>

    <!-- 台账明细抽屉：明细、调整、地图轨迹共用同一份 route 数据 -->
    <div v-if="detail" class="drawer-mask" @click.self="closeDetail">
      <div class="drawer">
        <header class="drawer-head">
          <div>
            <h3>{{ detail['巡查编号'] }} 路线台账</h3>
            <p class="page-desc">
              {{ detail['巡查日期'] }} · {{ detail['管线类型'] }} ·
              状态 <span class="status-tag">{{ detail.status }}</span>
              · 版本 v{{ detail.version }}
              <span v-if="detail.adjusted">· 责任人/顺序已人工调整</span>
            </p>
          </div>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </header>

        <div v-if="!detail.generated" class="empty-block">
          <p>该任务尚未生成路线台账（或台账已撤回）。</p>
          <p class="page-desc">生成将按当前巡查记录、巡查路段、人员排班实时计算；撤回后旧路线不会保留。</p>
          <button class="btn primary" type="button" :disabled="busy" @click="generate(detail.inspect_id)">生成路线台账</button>
        </div>

        <template v-else>
          <div v-if="detail.conflicts.length" class="conflict-box">
            <p v-for="(c, i) in detail.conflicts" :key="i" class="conflict-line">
              ⚠ {{ c.message }}
            </p>
          </div>
          <div v-else class="ok-box">暂无冲突：路段与排班均有效，未发现两人同时认领。</div>

          <div class="drawer-toolbar">
            <button v-if="!editing" class="btn" type="button" @click="startEdit">调整顺序/责任人</button>
            <template v-else>
              <button class="btn primary" type="button" :disabled="busy" @click="saveAdjust">保存调整</button>
              <button class="btn ghost" type="button" :disabled="busy" @click="cancelEdit">放弃修改</button>
            </template>
            <button class="btn" type="button" :disabled="busy" @click="reloadDetail">按源数据重算</button>
          </div>

          <table class="data-table stop-table">
            <thead>
              <tr>
                <th v-if="editing">新顺序</th>
                <th>顺序</th>
                <th>路段编码</th>
                <th>路段名称</th>
                <th>责任人</th>
                <th>认领人</th>
                <th>停靠点冲突</th>
                <th>现场认领</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="stop in editingStops" :key="stop.section_code">
                <td v-if="editing"><input v-model.number="stop.order" class="seq-input" type="number" min="1" /></td>
                <td>{{ stop.order }}</td>
                <td>{{ stop.section_code }}</td>
                <td>{{ stop.section_name || '—' }}</td>
                <td>
                  <select v-if="editing" v-model="stop.staff_code" class="staff-select">
                    <option v-for="p in staff" :key="String(p['人员编码'])" :value="p['人员编码']">
                      {{ p['姓名'] }}（{{ p['人员编码'] }}）
                    </option>
                  </select>
                  <template v-else>
                    {{ stop.staff_name || stop.staff_code || '—' }}
                    <span class="muted-inline">[{{ stop.owner_source }}]</span>
                  </template>
                </td>
                <td>
                  <span v-for="name in stop.claim_staff_names" :key="name" class="claim-tag">{{ name }}</span>
                  <span v-if="!stop.claim_staff_names.length" class="muted-inline">—</span>
                </td>
                <td>
                  <span v-for="kind in stop.conflicts" :key="kind" class="conflict-chip small">{{ kind }}</span>
                  <span v-if="!stop.conflicts.length" class="muted-inline">无</span>
                </td>
                <td>
                  <button
                    v-if="!editing"
                    class="link"
                    type="button"
                    :disabled="busy"
                    @click="claim(detail.inspect_id, stop.section_code)"
                  >
                    我认领
                  </button>
                </td>
              </tr>
            </tbody>
          </table>

          <h4 class="track-title">地图轨迹（与台账、巡查记录明细同源，状态同为「{{ detail.status }}」）</h4>
          <div class="track-map">
            <template v-for="(point, i) in trackPoints" :key="point.section_code">
              <div class="track-node" :class="{ conflicted: hasConflict(point.section_code) }">
                <span class="track-order">{{ point.order }}</span>
                <span class="track-name">{{ point.section_name || point.section_code }}</span>
                <span class="track-staff">{{ point.staff_name || point.staff_code }}</span>
              </div>
              <span v-if="i < trackPoints.length - 1" class="track-arrow">→</span>
            </template>
          </div>
        </template>

        <p v-if="detailMessage" class="detail-message">{{ detailMessage }}</p>
      </div>
    </div>

    <div class="admin-grid">
      <section class="admin-card">
        <h4>巡查路段（停用后相关台账立即提示冲突）</h4>
        <table class="data-table mini-table">
          <thead>
            <tr><th>编码</th><th>路段名称</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in sections" :key="String(item.id)">
              <td>{{ item['路段编码'] }}</td>
              <td>{{ item['路段名称'] }}</td>
              <td>{{ item.status }}</td>
              <td>
                <button class="link" type="button" @click="toggleSection(item)">
                  {{ item.status === '启用' ? '停用' : '启用' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="admin-card">
        <h4>巡查人员排班（置休即触发「排班变化」提示）</h4>
        <table class="data-table mini-table">
          <thead>
            <tr><th>编码</th><th>姓名</th><th>班次</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in staff" :key="String(item.id)">
              <td>{{ item['人员编码'] }}</td>
              <td>{{ item['姓名'] }}</td>
              <td>{{ item['班次'] }}</td>
              <td>{{ item.status }}</td>
              <td class="row-actions">
                <button class="link" type="button" @click="setSchedule(item, '排班')">排白班</button>
                <button class="link danger-link" type="button" @click="setSchedule(item, '置休')">置休</button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 个巡查任务台账</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

const route = useRoute()

type Row = Record<string, string | number | boolean | null>
type RouteSummary = {
  inspect_id: number
  巡查编号: string
  巡查日期: string
  管线类型: string
  status: string
  generated: boolean
  adjusted: boolean
  version: number
  stop_count: number
  claimed_count: number
  conflict_count: number
  conflict_kinds: string[]
}
type Stop = {
  order: number
  section_code: string
  section_name: string
  start: string
  end: string
  staff_code: string
  staff_name: string
  owner_source: string
  claim_staff_codes: string[]
  claim_staff_names: string[]
  conflicts: string[]
}
type Route = {
  inspect_id: number
  巡查编号: string
  巡查日期: string
  管线类型: string
  status: string
  generated: boolean
  adjusted: boolean
  version: number
  stops: Stop[]
  conflicts: Array<Record<string, string | number>>
}

const ENDPOINT = '/api/inspect-routes'

const rows = ref<RouteSummary[]>([])
const total = ref(0)
const errorMessage = ref('')
const sections = ref<Row[]>([])
const staff = ref<Row[]>([])
const conflictFeed = ref<Row[]>([])

const detail = ref<Route | null>(null)
const editing = ref(false)
const editingStops = ref<Stop[]>([])
const busy = ref(false)
const detailMessage = ref('')

const stats = computed(() => [
  { label: '巡查任务', value: total.value },
  { label: '已生成台账', value: rows.value.filter((r) => r.generated).length },
  { label: '存在冲突', value: rows.value.reduce((n, r) => n + Number(r.conflict_count ?? 0), 0) },
  { label: '已人工调整', value: rows.value.filter((r) => r.adjusted).length },
])

const trackPoints = computed(() => detail.value?.stops ?? [])

function hasConflict(sectionCode: string): boolean {
  return Boolean(detail.value?.stops.find((s) => s.section_code === sectionCode)?.conflicts.length)
}

async function post(path: string, body?: unknown): Promise<{ ok: boolean; message: string; entry?: unknown }> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body ?? {}),
  })
  return (await response.json()) as { ok: boolean; message: string; entry?: unknown }
}

async function reload() {
  errorMessage.value = ''
  try {
    const [routesResp, conflictResp, sectionResp, staffResp] = await Promise.all([
      request(`${ENDPOINT}?size=200`),
      request(`${ENDPOINT}/conflicts`),
      request(`${ENDPOINT}/sections/all`),
      request(`${ENDPOINT}/staff/all`),
    ])
    if (!routesResp.ok) {
      throw new Error('路线台账列表读取失败')
    }
    const payload = await routesResp.json()
    rows.value = (payload.items ?? []) as RouteSummary[]
    total.value = Number(payload.total ?? rows.value.length)
    const conflictPayload = conflictResp.ok ? await conflictResp.json() : { items: [] }
    conflictFeed.value = (conflictPayload.items ?? []) as Row[]
    const sectionPayload = sectionResp.ok ? await sectionResp.json() : { items: [] }
    sections.value = (sectionPayload.items ?? []) as Row[]
    const staffPayload = staffResp.ok ? await staffResp.json() : { items: [] }
    staff.value = (staffPayload.items ?? []) as Row[]
    if (detail.value) {
      await reloadDetail()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '路线台账读取失败'
  }
}

async function openDetail(inspectId: number) {
  editing.value = false
  detailMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${inspectId}`)
    if (!response.ok) {
      throw new Error('路线台账明细读取失败')
    }
    detail.value = (await response.json()) as Route
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '路线台账明细读取失败'
  }
}

function closeDetail() {
  detail.value = null
  editing.value = false
}

async function reloadDetail() {
  if (!detail.value) {
    return
  }
  const id = detail.value.inspect_id
  await openDetail(id)
}

async function generate(inspectId: number) {
  busy.value = true
  detailMessage.value = ''
  try {
    const result = await post(`${ENDPOINT}/${inspectId}/generate`)
    if (!result.ok) {
      detailMessage.value = result.message
      return
    }
    detail.value = result.entry as Route
    await reload()
  } finally {
    busy.value = false
  }
}

async function withdraw(inspectId: number) {
  if (!window.confirm('撤回将清除本任务的路线顺序、责任人调整、认领与冲突记录，确定撤回？')) {
    return
  }
  try {
    const result = await post(`${ENDPOINT}/${inspectId}/withdraw`)
    if (!result.ok) {
      errorMessage.value = result.message
      return
    }
    if (detail.value?.inspect_id === inspectId) {
      // 撤回后重新打开：只呈现未生成态，旧路线不保留
      await openDetail(inspectId)
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '撤回失败'
  }
}

function startEdit() {
  if (!detail.value) {
    return
  }
  editingStops.value = detail.value.stops.map((stop) => ({ ...stop, conflicts: [...stop.conflicts] }))
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  editingStops.value = []
}

async function saveAdjust() {
  if (!detail.value) {
    return
  }
  const orders = editingStops.value.map((stop) => Number(stop.order))
  if (orders.some((n) => !Number.isInteger(n) || n < 1)) {
    detailMessage.value = '新顺序必须是从 1 开始的正整数'
    return
  }
  if (new Set(orders).size !== orders.length) {
    detailMessage.value = '新顺序不能有重复序号'
    return
  }
  const ordered = [...editingStops.value].sort((a, b) => Number(a.order) - Number(b.order))
  busy.value = true
  detailMessage.value = ''
  try {
    const result = await post(`${ENDPOINT}/${detail.value.inspect_id}/adjust`, {
      assignments: ordered.map((stop) => ({
        section_code: stop.section_code,
        staff_code: stop.staff_code,
      })),
    })
    if (!result.ok) {
      detailMessage.value = result.message
      return
    }
    detail.value = result.entry as Route
    editing.value = false
    editingStops.value = []
    detailMessage.value = '调整已保存，冲突提示已按最新数据重算'
    await reload()
  } finally {
    busy.value = false
  }
}

async function claim(inspectId: number, sectionCode: string) {
  const options = (staff.value as Record<string, string | number>[])
    .filter((p) => p.status === '在岗')
    .map((p) => `${p['人员编码']} ${p['姓名']}`)
  const picked = window.prompt(
    `请输入认领路段 ${sectionCode} 的人员编码（在岗人员：${options.join('；')}）`,
    String(staff.value.find((p) => p.status === '在岗')?.['人员编码'] ?? ''),
  )
  const staffCode = (picked ?? '').trim().split(/\s+/)[0]
  if (!staffCode) {
    return
  }
  try {
    const result = await post(`${ENDPOINT}/${inspectId}/claim`, {
      section_code: sectionCode,
      staff_code: staffCode,
    })
    detailMessage.value = result.message
    if (!result.ok) {
      return
    }
    detail.value = result.entry as Route
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '认领失败'
  }
}

async function toggleSection(item: Row) {
  const action = item.status === '启用' ? '停用' : '启用'
  const result = await post(
    `${ENDPOINT}/sections/${String(item.id)}/actions`,
    { action },
  )
  if (!result.ok) {
    errorMessage.value = result.message
    return
  }
  await reload()
}

async function setSchedule(item: Row, action: '排班' | '置休') {
  const result = await post(
    `${ENDPOINT}/staff/${String(item.id)}/schedule`,
    action === '排班'
      ? { action, 班次: '白班', 排班日期: '2026-09-27' }
      : { action },
  )
  if (!result.ok) {
    errorMessage.value = result.message
    return
  }
  await reload()
}

onMounted(async () => {
  await reload()
  const taskId = Number(route.query.task)
  if (taskId > 0) {
    await openDetail(taskId)
  }
})
</script>

<style scoped>
.muted-inline { color: var(--muted); font-size: 12px; margin-left: 4px; }
.status-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  background: #e8f0fe;
  color: var(--brand);
  font-size: 12px;
}
.row-conflict { background: #fff7ed; }
.conflict-count { color: #b42318; font-weight: 600; }
.conflict-feed {
  background: #fef3f2;
  border: 1px solid #fecdca;
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.conflict-chip {
  background: #fee4e2;
  color: #b42318;
  border-radius: 10px;
  padding: 1px 8px;
  font-size: 12px;
}
.conflict-chip.small { margin-right: 4px; }
.danger-link { color: #b42318; }
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 20;
}
.drawer {
  width: 960px;
  max-width: 92vw;
  height: 100%;
  background: #fff;
  padding: 16px 20px;
  overflow-y: auto;
}
.drawer-head { display: flex; justify-content: space-between; align-items: flex-start; }
.empty-block { text-align: center; padding: 32px 0; }
.conflict-box {
  background: #fef3f2;
  border: 1px solid #fecdca;
  border-radius: 8px;
  padding: 8px 12px;
  margin: 10px 0;
}
.conflict-line { margin: 4px 0; color: #b42318; font-size: 13px; }
.ok-box {
  background: #ecfdf3;
  border: 1px solid #abefc6;
  color: #067647;
  border-radius: 8px;
  padding: 8px 12px;
  margin: 10px 0;
  font-size: 13px;
}
.drawer-toolbar { display: flex; gap: 8px; margin: 10px 0; }
.stop-table { margin: 8px 0 16px; }
.seq-input { width: 64px; }
.staff-select { min-width: 160px; }
.claim-tag {
  background: #e0f2fe;
  color: #075985;
  border-radius: 10px;
  padding: 1px 8px;
  font-size: 12px;
  margin-right: 4px;
}
.track-title { margin: 18px 0 8px; }
.track-map {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  background: #f8fafc;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 12px;
}
.track-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  min-width: 120px;
}
.track-node.conflicted { border-color: #fecdca; background: #fef3f2; }
.track-order {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--brand);
  color: #fff;
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.track-name { font-size: 12px; margin-top: 4px; }
.track-staff { font-size: 12px; color: var(--muted); }
.track-arrow { color: var(--muted); }
.detail-message { color: var(--brand); font-size: 13px; }
.admin-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 16px;
}
.admin-card {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
}
.admin-card h4 { margin: 0 0 8px; font-size: 14px; }
.mini-table { font-size: 12px; }
</style>
