<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const rows = ref([])
const breaksData = ref(null)
const detail = ref({})
const err = ref('')

async function load() {
  try {
    rows.value = await api.batches()
    breaksData.value = await api.chainBreaks()
  } catch (e) { err.value = e.message }
}

async function toggle(b) {
  if (detail.value[b.id]) {
    const next = { ...detail.value }
    delete next[b.id]
    detail.value = next
    return
  }
  try {
    const d = await api.batchDetail(b.id)
    detail.value = { ...detail.value, [b.id]: d }
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel">
    <p class="msg err" v-if="err">{{ err }}</p>
    <table>
      <thead>
        <tr><th>批次</th><th>业务日</th><th>状态</th><th>笔数</th><th>合计</th><th>检查运行</th><th>钉扎时刻</th><th></th></tr>
      </thead>
      <tbody>
        <template v-for="b in rows" :key="b.id">
          <tr>
            <td>{{ b.id }}</td>
            <td>{{ b.business_day }}</td>
            <td><span class="tag ok">{{ b.status }}</span></td>
            <td>{{ b.fact_count }}</td>
            <td>{{ b.total_amount }}</td>
            <td>{{ b.check_run_id ?? '—' }}</td>
            <td>{{ b.sealed_at }}</td>
            <td><button type="button" class="ghost mini" @click="toggle(b)">明细</button></td>
          </tr>
          <tr v-if="detail[b.id]" class="detail-row">
            <td colspan="8">
              <div class="detail-box">
                <p v-if="detail[b.id].check_run">
                  检查运行 #{{ detail[b.id].check_run.id }} ·
                  策略 {{ detail[b.id].check_run.policy }} ·
                  结果 {{ detail[b.id].check_run.outcome }} ·
                  断链 {{ detail[b.id].check_run.break_count }} 条 ·
                  重写 {{ detail[b.id].check_run.rewrite_count }} 条
                </p>
                <table>
                  <thead><tr><th>事实ID</th><th>券码</th><th>金额</th><th>业务日</th><th>事件ID</th><th>状态</th></tr></thead>
                  <tbody>
                    <tr v-for="f in detail[b.id].facts" :key="f.id">
                      <td>{{ f.id }}</td>
                      <td>{{ f.coupon_code }}</td>
                      <td>{{ f.amount }}</td>
                      <td>{{ f.business_day }}</td>
                      <td>{{ f.event_id }}</td>
                      <td>{{ f.status }}</td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="detail[b.id].rewrites.length" class="rewrite-box">
                  <h4>重写审计（旧值 → 新值）</h4>
                  <table>
                    <thead><tr><th>事实ID</th><th>金额</th><th>业务日</th><th>事件ID</th></tr></thead>
                    <tbody>
                      <tr v-for="rw in detail[b.id].rewrites" :key="rw.id">
                        <td>{{ rw.fact_id }}</td>
                        <td>{{ rw.amount_old }} → {{ rw.amount_new }}</td>
                        <td>{{ rw.business_day_old }} → {{ rw.business_day_new }}</td>
                        <td>{{ rw.event_id_old }} → {{ rw.event_id_new }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <h4>断链清单复查（全量留痕，含已物化修复行）</h4>
    <p class="msg" v-if="breaksData && !breaksData.break_count">暂无断链记录。</p>
    <table v-if="breaksData?.break_count">
      <thead>
        <tr><th>记录ID</th><th>运行</th><th>业务日</th><th>策略</th><th>事实ID</th><th>事件ID</th><th>原因</th><th>详情</th><th>已修复</th></tr>
      </thead>
      <tbody>
        <tr v-for="x in breaksData.breaks" :key="x.id">
          <td>{{ x.id }}</td>
          <td>#{{ x.run_id }}（{{ x.run_outcome }}）</td>
          <td>{{ x.run_business_day }}</td>
          <td>{{ x.run_policy }}</td>
          <td>{{ x.fact_id }}</td>
          <td>{{ x.event_id ?? '—' }}</td>
          <td>{{ x.reason }}</td>
          <td>{{ x.detail }}</td>
          <td>
            <span class="tag" :class="x.resolved ? 'ok' : 'bad-tag'">
              {{ x.resolved ? '已物化修复' : '未修复' }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
