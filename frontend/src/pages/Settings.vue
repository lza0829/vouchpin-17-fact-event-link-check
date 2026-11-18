<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
const day = ref('')
const policy = ref('')
const policyMeta = ref(null)
const msg = ref('')
const err = ref('')

async function load() {
  try {
    const r = await api.getDay()
    day.value = r.business_day
    const p = await api.getChainPolicy()
    policy.value = p.policy
    policyMeta.value = p
  } catch (e) { err.value = e.message }
}
async function saveDay() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.setDay(day.value)
    msg.value = `业务日已设为 ${r.business_day}`
  } catch (e) { err.value = e.message }
}
async function savePolicy() {
  msg.value = ''; err.value = ''
  try {
    const r = await api.setChainPolicy(policy.value)
    policy.value = r.policy
    msg.value = `链完整性策略已全局切换为 ${r.policy}（全局只贯彻这一种）`
  } catch (e) { err.value = e.message }
}
onMounted(load)
</script>
<template>
  <div class="panel">
    <div class="row">
      <label>业务日 <input v-model="day" placeholder="YYYY-MM-DD" /></label>
      <button type="button" @click="saveDay">保存业务日</button>
    </div>
  </div>

  <div class="panel">
    <h4>事实-事件链完整性策略（全局互斥，二选一）</h4>
    <div class="policy-options">
      <label
        v-for="c in (policyMeta?.choices || ['reject', 'materialize'])"
        :key="c"
        class="policy-card"
        :class="{ on: policy === c }"
      >
        <input type="radio" name="chain-policy" :value="c" v-model="policy" />
        <span class="policy-name">{{ c }}</span>
        <span class="policy-desc">{{ policyMeta?.labels?.[c] }}</span>
      </label>
    </div>
    <div class="row">
      <button type="button" @click="savePolicy">保存策略</button>
    </div>
    <p class="msg" v-if="msg">{{ msg }}</p>
    <p class="msg err" v-if="err">{{ err }}</p>
  </div>
</template>
