<template>
  <q-page class="page-pad">
    <div class="row items-center q-mb-md">
      <div class="text-h5">指标门槛检索</div>
      <q-space />
      <q-btn flat label="返回历史" to="/jobs" />
    </div>

    <q-banner rounded class="bg-grey-2 text-dark q-mb-md">
      至少设置一个门槛，多个门槛为「同时满足」关系；检索在服务端完成，仅显示命中作业。
    </q-banner>

    <q-card flat bordered class="q-mb-md">
      <q-card-section>
        <div class="row q-col-gutter-md items-end">
          <div class="col-12 col-sm-3">
            <q-input
              v-model.number="form.minReads"
              type="number"
              min="0"
              outlined
              dense
              clearable
              label="读段数下限（reads ≥）"
            />
          </div>
          <div class="col-12 col-sm-3">
            <q-input
              v-model.number="form.minMeanQuality"
              type="number"
              min="0"
              step="0.1"
              outlined
              dense
              clearable
              label="平均质量下限（mean_quality ≥）"
            />
          </div>
          <div class="col-12 col-sm-3">
            <q-input
              v-model.number="form.maxNRate"
              type="number"
              min="0"
              max="1"
              step="0.001"
              outlined
              dense
              clearable
              label="N 率上限（n_rate ≤，如 0.05）"
            />
          </div>
          <div class="col-12 col-sm-3">
            <q-btn
              color="primary"
              icon="search"
              label="查询"
              :loading="loading"
              @click="search"
            />
            <q-btn flat label="重置" class="q-ml-sm" @click="reset" />
          </div>
        </div>
      </q-card-section>
    </q-card>

    <template v-if="searched">
      <div class="text-caption text-grey-7 q-mb-sm">
        当前门槛：{{ activeCriteria }} —— 命中 {{ rows.length }} 条
      </div>
      <q-table
        flat
        bordered
        row-key="id"
        :rows="rows"
        :columns="columns"
        :loading="loading"
        hide-pagination
        :pagination="{ rowsPerPage: 0 }"
      >
        <template #body-cell-status="props">
          <q-td :props="props">
            <q-badge :color="statusColor(props.row.status)">
              {{ statusLabel(props.row.status) }}
            </q-badge>
          </q-td>
        </template>
        <template #body-cell-actions="props">
          <q-td :props="props">
            <q-btn dense flat color="primary" label="详情" :to="`/jobs/${props.row.id}`" />
          </q-td>
        </template>
        <template #no-data>
          <div class="full-width row flex-center q-pa-lg text-grey-7">
            <q-icon name="search_off" size="sm" class="q-mr-sm" />
            <span>
              没有作业满足当前门槛组合（{{ activeCriteria }}）。结果为空即真实结果，
              未回退显示全部作业；可放宽门槛后重新查询。
            </span>
          </div>
        </template>
      </q-table>
    </template>

    <q-banner v-else rounded class="bg-grey-2 text-dark">
      尚未查询。设置门槛后点击「查询」，命中结果将列在此处。
    </q-banner>
  </q-page>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { searchJobs } from '../api/client'

const $q = useQuasar()
const loading = ref(false)
const searched = ref(false)
const rows = ref([])

const form = reactive({
  minReads: null,
  minMeanQuality: null,
  maxNRate: null,
})

// 查询时实际生效的门槛快照，用于结果区说明（避免查询后改表单导致说明与结果不一致）
const applied = ref({})

const columns = [
  { name: 'id', label: 'ID', field: 'id', align: 'left' },
  { name: 'sample_name', label: '样例', field: 'sample_name', align: 'left' },
  { name: 'status', label: '状态', field: 'status', align: 'left' },
  {
    name: 'reads',
    label: '读段数',
    align: 'left',
    field: (r) => r.metrics?.reads,
    format: (v) => v ?? '—',
  },
  {
    name: 'mean_quality',
    label: '平均质量',
    align: 'left',
    field: (r) => r.metrics?.mean_quality,
    format: (v) => v ?? '—',
  },
  {
    name: 'n_rate',
    label: 'N 率',
    align: 'left',
    field: (r) => r.metrics?.n_rate,
    format: (v) => v ?? '—',
  },
  { name: 'created_by', label: '提交人', field: 'created_by', align: 'left' },
  {
    name: 'created_at',
    label: '创建时间',
    field: 'created_at',
    align: 'left',
    format: (v) => (v ? new Date(v).toLocaleString() : ''),
  },
  { name: 'actions', label: '操作', field: 'actions', align: 'left' },
]

const activeCriteria = computed(() => {
  const parts = []
  if (applied.value.minReads != null) parts.push(`reads ≥ ${applied.value.minReads}`)
  if (applied.value.minMeanQuality != null) {
    parts.push(`mean_quality ≥ ${applied.value.minMeanQuality}`)
  }
  if (applied.value.maxNRate != null) parts.push(`n_rate ≤ ${applied.value.maxNRate}`)
  return parts.join('，') || '（无）'
})

function statusLabel(s) {
  return { pending: '排队中', running: '运行中', success: '成功', failed: '失败' }[s] || s
}

function statusColor(s) {
  return { pending: 'grey', running: 'info', success: 'positive', failed: 'negative' }[s] || 'grey'
}

async function search() {
  // v-model.number 清空后可能是 ''，统一归一为 null 再判断/传参
  const norm = (v) => (v === '' || v === undefined ? null : v)
  const criteria = {
    minReads: norm(form.minReads),
    minMeanQuality: norm(form.minMeanQuality),
    maxNRate: norm(form.maxNRate),
  }
  if (
    criteria.minReads == null &&
    criteria.minMeanQuality == null &&
    criteria.maxNRate == null
  ) {
    $q.notify({ type: 'warning', message: '请至少设置一个指标门槛' })
    return
  }
  loading.value = true
  try {
    const data = await searchJobs(criteria)
    applied.value = criteria
    searched.value = true
    // 空结果即空表：绝不回退为全量列表
    rows.value = data
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '检索失败' })
  } finally {
    loading.value = false
  }
}

function reset() {
  form.minReads = null
  form.minMeanQuality = null
  form.maxNRate = null
  searched.value = false
  rows.value = []
  applied.value = {}
}
</script>
