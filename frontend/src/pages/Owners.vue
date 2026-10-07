<template>
  <div style="padding:16px">
    <h1>物主一览</h1>
    <div v-for="i in rows" :key="i.id" class="item">
      {{ i.owner || '（空）' }} · {{ i.title }} · {{ i.status }}
      <template v-if="loanOf(i.id)">
        <div class="muted">借给 {{ loanOf(i.id).borrower }} · 应还 {{ loanOf(i.id).due_date }}</div>
        <template v-if="!loanOf(i.id).recall_id">
          <button @click="recall(loanOf(i.id), 'settle')">发起收回·当场结还</button>
          <button @click="recall(loanOf(i.id), 'nudge')">发起收回·只催待还</button>
        </template>
        <div v-else class="muted">已挂收回工单 #{{ loanOf(i.id).recall_id }}（见下方收回名单）</div>
      </template>
    </div>
    <h2>收回名单（未完成工单 {{ openRecalls.length }}）</h2>
    <div v-for="r in openRecalls" :key="r.id" class="item">
      <strong>{{ r.title }}</strong> → {{ r.borrower }}
      <span class="recall-tag">{{ r.effect === 'settle' ? '当场结还' : '只催待还' }}</span>
      <div class="muted">工单 #{{ r.id }} · 在借 #{{ r.loan_id }} · 应还 {{ r.due_date }}</div>
      <button @click="confirm(r)">确认</button>
      <button @click="cancel(r)">撤回</button>
    </div>
    <div v-if="!openRecalls.length" class="muted">暂无未完成工单</div>
    <p v-if="err" class="err">{{ err }}</p>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const loans = ref({ active: [], overdue: [] })
const recalls = ref([])
const err = ref('')
const openRecalls = computed(() => recalls.value.filter(r => r.status === 'open'))
function loanOf(itemId) {
  return [...loans.value.overdue, ...loans.value.active].find(l => l.item_id === itemId)
}
async function load() {
  const [its, lns, rcs] = await Promise.all([api('/items'), api('/loans'), api('/recalls')])
  rows.value = its
  loans.value = lns
  recalls.value = rcs
}
async function run(fn) {
  err.value = ''
  try { await fn() } catch (e) { err.value = e.message }
  await load()  // 成功失败都以服务端为准：失败时收回名单条数回到点下去之前
}
async function recall(loan, effect) {
  await run(() => api('/loans/' + loan.id + '/recalls', { method: 'POST', body: JSON.stringify({ effect }) }))
}
async function confirm(r) {
  await run(() => api('/recalls/' + r.id + '/confirm', { method: 'POST', body: '{}' }))
}
async function cancel(r) {
  await run(() => api('/recalls/' + r.id + '/cancel', { method: 'POST', body: '{}' }))
}
onMounted(load)
</script>
