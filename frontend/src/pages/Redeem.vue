<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const preview = ref(null)
const policyInfo = ref(null)
const msg = ref('')
const err = ref('')
const rejectBreaks = ref([])
const sealResult = ref(null)

const policyLabel = computed(() => {
  const p = preview.value?.policy
  if (!policyInfo.value) return p
  return policyInfo.value.labels[p] || p
})

async function load() {
  err.value = ''
  msg.value = ''
  try {
    const [pv, pol] = await Promise.all([api.preview(), api.getChainPolicy()])
    preview.value = pv
    policyInfo.value = pol
  } catch (e) { err.value = e.message }
}

async function seal() {
  msg.value = ''; err.value = ''
  rejectBreaks.value = []
  sealResult.value = null
  try {
    const r = await api.seal({})
    sealResult.value = r
    msg.value = r.materialized_fact_count
      ? `已钉扎批次 #${r.batch_id} · ${r.fact_count} 笔 · 合计 ${r.total_amount}（物化重写 ${r.materialized_fact_count} 条，见审计）`
      : `已钉扎批次 #${r.batch_id} · ${r.fact_count} 笔 · 合计 ${r.total_amount}`
    await load()
  } catch (e) {
    err.value = e.message
    if (e.payload?.breaks) rejectBreaks.value = e.payload.breaks
  }
}

function reasonTag(b) {
  return b.reason_label || b.reason
}
onMounted(load)
</script>
<template>
  <div class="panel" v-if="preview">
    <div class="grid">
      <div class="stat"><div class="k">业务日</div><div class="v">{{ preview.business_day }}</div></div>
      <div class="stat"><div class="k">待钉笔数</div><div class="v ticket">{{ preview.open_count }}</div></div>
      <div class="stat"><div class="k">待钉合计</div><div class="v ticket">{{ preview.open_total }}</div></div>
      <div class="stat"><div class="k">已钉扎</div><div class="v">{{ preview.already_sealed ? '是' : '否' }}</div></div>
    </div>
    <p class="policy-line">
      全局链完整性策略：<strong>{{ preview.policy }}</strong>
      <em>（{{ policyLabel }}）</em>
    </p>
    <div class="chain-banner" :class="preview.break_count ? (preview.blocking_count ? 'bad' : 'warn') : 'ok'">
      <template v-if="preview.chain_ok">事实-事件链完整，可以钉扎。</template>
      <template v-else>
        检出 {{ preview.break_count }} 条断链，其中
        <strong>{{ preview.blocking_count }}</strong> 条在当前策略下阻断钉扎。
        <template v-if="preview.policy === 'reject'">拒钉策略：任一条失败都会整批拒钉，不写批、不锁日。</template>
        <template v-else>物化策略：金额/业务日漂移将按事件重写并留审计；事件缺失仍拒钉。</template>
      </template>
    </div>
    <div class="row">
      <button
        type="button"
        @click="seal"
        :disabled="preview.already_sealed || preview.open_count === 0 || preview.blocking_count > 0"
      >钉扎本日核销</button>
      <button type="button" class="ghost" @click="load">刷新预览</button>
    </div>
    <p class="msg" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>

    <div v-if="sealResult?.rewrites?.length" class="rewrite-box">
      <h4>物化重写审计（旧值 → 事件新值）</h4>
      <table>
        <thead><tr><th>事实ID</th><th>事件ID</th><th>金额旧→新</th><th>业务日旧→新</th></tr></thead>
        <tbody>
          <tr v-for="rw in sealResult.rewrites" :key="rw.fact_id">
            <td>{{ rw.fact_id }}</td>
            <td>{{ rw.event_id }}</td>
            <td>{{ rw.amount_old }} → {{ rw.amount_new }}</td>
            <td>{{ rw.business_day_old }} → {{ rw.business_day_new }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="panel" v-if="preview?.breaks?.length">
    <h4>钉前链校验断链清单（预览，未落库；钉扎拒钉后的清单可在「钉扎批次」页复查）</h4>
    <table>
      <thead><tr><th>事实ID</th><th>事件ID</th><th>券码</th><th>原因</th><th>详情</th><th>阻断</th></tr></thead>
      <tbody>
        <tr v-for="(b, i) in preview.breaks" :key="b.fact_id + '-' + b.reason + i">
          <td>{{ b.fact_id }}</td>
          <td>{{ b.event_id ?? '—' }}</td>
          <td>{{ b.coupon_code }}</td>
          <td><span class="tag" :class="b.blocking ? 'bad-tag' : 'warn'">{{ reasonTag(b) }}</span></td>
          <td>{{ b.detail }}</td>
          <td>{{ b.blocking ? '是' : '否' }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="panel" v-if="rejectBreaks.length">
    <h4 class="err">钉扎被拒 · 断链清单（已落库可复查）</h4>
    <table>
      <thead><tr><th>事实ID</th><th>事件ID</th><th>原因</th><th>详情</th></tr></thead>
      <tbody>
        <tr v-for="(b, i) in rejectBreaks" :key="'rj-' + b.fact_id + '-' + b.reason + i">
          <td>{{ b.fact_id }}</td>
          <td>{{ b.event_id ?? '—' }}</td>
          <td>{{ reasonTag(b) }}</td>
          <td>{{ b.detail }}</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="panel">
    <table>
      <thead><tr><th>事实ID</th><th>券码</th><th>活动</th><th>金额</th><th>业务日</th><th>事件ID</th><th>状态</th></tr></thead>
      <tbody>
        <tr v-for="f in (preview?.facts || [])" :key="f.id">
          <td>{{ f.id }}</td>
          <td>{{ f.coupon_code }}</td>
          <td>{{ f.campaign_code }}</td>
          <td>{{ f.amount }}</td>
          <td>{{ f.business_day }}</td>
          <td>{{ f.event_id }}</td>
          <td><span class="tag warn">{{ f.status }}</span></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
