<template>
  <q-page class="page-pad">
    <div class="row items-center q-mb-md">
      <div class="text-h5">按指标门槛检索作业</div>
      <q-space />
      <q-btn flat icon="help_outline" no-caps>
        <q-tooltip>
          三个门槛可任意组合（AND），过滤在服务端完成；只返回全部达标的作业，
          不设置门槛或无人命中时为空表，不会展示全部作业。
        </q-tooltip>
      </q-btn>
    </div>

    <q-card flat bordered class="q-mb-md">
      <q-card-section class="row q-col-gutter-md items-end">
        <div class="col-12 col-sm-4 col-md-3">
          <q-input
            v-model.number="minReads"
            type="number"
            outlined
            dense
            label="读段数下限（reads ≥）"
            hint="整数，留空表示不限"
            :min="0"
            step="1"
          />
        </div>
        <div class="col-12 col-sm-4 col-md-3">
          <q-input
            v-model.number="minMeanQuality"
            type="number"
            outlined
            dense
            label="平均质量下限（mean_quality ≥）"
            hint="如 38.5，留空表示不限"
            :min="0"
            step="0.1"
          />
        </div>
        <div class="col-12 col-sm-4 col-md-3">
          <q-input
            v-model.number="maxNRate"
            type="number"
            outlined
            dense
            label="N 率上限（n_rate ≤）"
            hint="范围 0~1，如 0.05，留空表示不限"
            :min="0"
            :max="1"
            step="0.01"
          />
        </div>
        <div class="col-12 col-md-3 text-right">
          <q-btn flat label="重置" :disable="loading" @click="resetForm" class="q-mr-sm" />
          <q-btn
            color="primary"
            icon="search"
            label="检索"
            :loading="loading"
            @click="search"
          />
        </div>
      </q-card-section>
    </q-card>

    <div v-if="hasQueried" class="row items-center q-mb-sm">
      <div class="text-subtitle2">
        命中 <span class="text-primary">{{ rows.length }}</span> 条
      </div>
      <q-space />
      <div class="text-caption text-grey-7">当前门槛：{{ criteriaSummary }}</div>
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
      <template #body-cell-reads="props">
        <q-td :props="props">{{ props.row.metrics?.reads ?? '—' }}</q-td>
      </template>
      <template #body-cell-mean_quality="props">
        <q-td :props="props">{{ props.row.metrics?.mean_quality ?? '—' }}</q-td>
      </template>
      <template #body-cell-n_rate="props">
        <q-td :props="props">{{ props.row.metrics?.n_rate ?? '—' }}</q-td>
      </template>
      <template #body-cell-actions="props">
        <q-td :props="props">
          <q-btn dense flat color="primary" label="查看详情" :to="`/jobs/${props.row.id}`" />
        </q-td>
      </template>

      <template #no-data>
        <div class="full-width q-pa-lg text-center text-grey-7">
          <template v-if="loading">检索中…</template>
          <template v-else-if="!hasQueried">
            <q-icon name="manage_search" size="40px" class="q-mb-sm" />
            <div>请设置一个或多个指标门槛后点击「检索」。</div>
            <div class="text-caption q-mt-xs">
              查询在服务端完成，未发起检索前不加载任何作业数据。
            </div>
          </template>
          <template v-else>
            <q-icon name="search_off" size="40px" class="q-mb-sm" />
            <div>没有符合当前门槛的作业。</div>
            <div class="text-caption q-mt-xs">
              可放宽门槛后重新检索；空结果不会回退为全部作业。
            </div>
          </template>
        </div>
      </template>
    </q-table>
  </q-page>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useQuasar } from 'quasar'
import { searchJobs } from '../api/client'

const $q = useQuasar()
const loading = ref(false)
const hasQueried = ref(false)
const rows = ref([])

const minReads = ref(null)
const minMeanQuality = ref(null)
const maxNRate = ref(null)

const columns = [
  { name: 'sample_name', label: '样例名', field: 'sample_name', align: 'left' },
  { name: 'status', label: '状态', field: 'status', align: 'left' },
  { name: 'reads', label: '读段数', field: 'reads', align: 'left' },
  { name: 'mean_quality', label: '平均质量', field: 'mean_quality', align: 'left' },
  { name: 'n_rate', label: 'N 率', field: 'n_rate', align: 'left' },
  { name: 'created_by', label: '提交人', field: 'created_by', align: 'left' },
  { name: 'actions', label: '操作', field: 'actions', align: 'left' },
]

const lastParams = ref(null)

const criteriaSummary = computed(() => {
  const p = lastParams.value
  if (!p) return '—'
  const parts = []
  if (p.min_reads != null) parts.push(`reads ≥ ${p.min_reads}`)
  if (p.min_mean_quality != null) parts.push(`mean_quality ≥ ${p.min_mean_quality}`)
  if (p.max_n_rate != null) parts.push(`n_rate ≤ ${p.max_n_rate}`)
  return parts.length ? parts.join('，') : '（无条件）'
})

function statusLabel(s) {
  return { pending: '排队中', running: '运行中', success: '成功', failed: '失败' }[s] || s
}

function statusColor(s) {
  return { pending: 'grey', running: 'info', success: 'positive', failed: 'negative' }[s] || 'grey'
}

function buildParams() {
  const params = {}

  const reads = minReads.value
  if (reads !== null && reads !== '' && !Number.isNaN(reads)) {
    if (!Number.isInteger(reads) || reads < 0) {
      $q.notify({ type: 'warning', message: '读段数下限须为不小于 0 的整数' })
      return null
    }
    params.min_reads = reads
  }

  const meanQ = minMeanQuality.value
  if (meanQ !== null && meanQ !== '' && !Number.isNaN(meanQ)) {
    if (meanQ < 0) {
      $q.notify({ type: 'warning', message: '平均质量下限不能为负数' })
      return null
    }
    params.min_mean_quality = meanQ
  }

  const nRate = maxNRate.value
  if (nRate !== null && nRate !== '' && !Number.isNaN(nRate)) {
    if (nRate < 0 || nRate > 1) {
      $q.notify({ type: 'warning', message: 'N 率上限须在 0 到 1 之间' })
      return null
    }
    params.max_n_rate = nRate
  }

  if (Object.keys(params).length === 0) {
    $q.notify({ type: 'warning', message: '请至少设置一个指标门槛' })
    return null
  }
  return params
}

async function search() {
  const params = buildParams()
  if (!params) return
  loading.value = true
  try {
    // 结果完全由服务端按门槛过滤；前端不做二次补全或回退全量
    const data = await searchJobs(params)
    rows.value = data
    lastParams.value = params
    hasQueried.value = true
  } catch (e) {
    $q.notify({ type: 'negative', message: e.message || '检索失败' })
  } finally {
    loading.value = false
  }
}

function resetForm() {
  minReads.value = null
  minMeanQuality.value = null
  maxNRate.value = null
  rows.value = []
  lastParams.value = null
  hasQueried.value = false
}
</script>
