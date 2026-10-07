<template>
  <div style="padding:16px">
    <h1>物主一览</h1>
    <div v-for="i in items" :key="i.id" class="item">
      {{ i.owner || '（空）' }} · {{ i.title }} · {{ i.status }}
      <template v-if="loanOf(i.id)">
        <div class="muted">在借 #{{ loanOf(i.id).id }} → {{ loanOf(i.id).borrower }} · 应还 {{ loanOf(i.id).due_date }}</div>
        <span v-if="loanOf(i.id).recall_id" class="recall-badge">
          工单 #{{ loanOf(i.id).recall_id }} 进行中 · {{ effectLabel(loanOf(i.id).recall_effect) }}
        </span>
        <template v-else>
          <button @click="startRecall(loanOf(i.id), 'close_return')">发起收回 · 当场结还</button>
          <button class="ghost" @click="startRecall(loanOf(i.id), 'remind')">发起收回 · 只催</button>
        </template>
      </template>
    </div>

    <h2>收回名单（未完成工单）</h2>
    <div v-for="r in recalls" :key="r.id" class="item">
      <strong>#{{ r.id }} {{ r.title }}</strong> → {{ r.borrower }}
      <div class="muted">
        在借 #{{ r.loan_id }} · 效果 {{ effectLabel(r.effect) }} · 应还 {{ r.due_date }} · 发起 {{ r.created_at }}
      </div>
      <button @click="confirm(r.id)">确认执行</button>
      <button class="ghost" @click="cancel(r.id)">撤回工单</button>
    </div>
    <div v-if="!recalls.length" class="muted">暂无未完成收回工单</div>
    <div v-if="err" class="err">{{ err }}</div>
  </div>
</template>
<script setup>
import { ref, inject, onMounted } from 'vue'
import { api } from '../api'
const reloadBoard = inject('reloadBoard')
const items = ref([])
const loans = ref([])
const recalls = ref([])
const err = ref('')
const EFFECTS = { close_return: '当场结还', remind: '只催' }
const effectLabel = (e) => EFFECTS[e] || e
const loanOf = (itemId) => loans.value.find((l) => l.item_id === itemId)
async function load() {
  const [its, ls, rs] = await Promise.all([
    api('/items'), api('/loans'), api('/recalls?status=open'),
  ])
  items.value = its
  loans.value = [...ls.overdue, ...ls.active]
  recalls.value = rs
}
async function run(fn) {
  err.value = ''
  try { await fn() } catch (e) { err.value = e.message }
  await load()          // 失败也重新拉取：收回名单条数回到点下去之前
  await reloadBoard()   // 顶细条与在借栏同步
}
const startRecall = (loan, effect) => run(() =>
  api('/loans/' + loan.id + '/recalls', { method: 'POST', body: JSON.stringify({ effect }) }))
const confirm = (rid) => run(() => api('/recalls/' + rid + '/confirm', { method: 'POST', body: '{}' }))
const cancel = (rid) => run(() => api('/recalls/' + rid + '/cancel', { method: 'POST', body: '{}' }))
onMounted(load)
</script>
