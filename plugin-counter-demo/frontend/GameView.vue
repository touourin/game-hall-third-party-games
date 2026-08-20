<script setup lang="ts">
import { computed } from 'vue'
import {
  PluginButton,
  PluginMetricGrid,
  usePluginGameActions,
  type ArcadeSnapshot,
  type PluginMetricItem,
} from '@game-hall/plugin-sdk'

const props = defineProps<{ snapshot: ArcadeSnapshot }>()
const actions = usePluginGameActions()
const game = computed(() => props.snapshot.game as {
  targetScore?: number
  currentPlayerId?: string | null
  scores?: Record<string, number>
})
const canIncrement = computed(() => (
  props.snapshot.phase === 'playing'
  && game.value.currentPlayerId === props.snapshot.self.id
))
const scoreItems = computed<PluginMetricItem[]>(() => props.snapshot.players.map((player) => ({
  label: player.id === props.snapshot.self.id ? `${player.name}（你）` : player.name,
  value: score(player.id),
  tone: game.value.currentPlayerId === player.id ? 'success' : 'default',
})))

function score(playerId: string): number {
  return game.value.scores?.[playerId] ?? 0
}
</script>

<template>
  <section class="counter-demo surface">
    <header>
      <small>COMMUNITY GAME PLUGIN</small>
      <h2>计数竞速</h2>
      <p>双方轮流计数，率先达到 {{ game.targetScore ?? 10 }} 分者获胜。</p>
    </header>
    <PluginMetricGrid :items="scoreItems" :columns="2" value-first aria-label="双方得分" />
    <PluginButton
      block
      variant="primary"
      :disabled="!canIncrement"
      @click="actions.action('increment')"
    >
      {{ canIncrement ? '计数 +1' : '等待对手' }}
    </PluginButton>
  </section>
</template>

<style scoped>
.counter-demo { width: min(100%, 720px); min-width: 0; max-width: 100%; display: grid; gap: 22px; margin: 0 auto; padding: clamp(20px, 5vw, 38px); }
.counter-demo header { text-align: center; }.counter-demo small { color: var(--gold); font-weight: 850; letter-spacing: .12em; }.counter-demo h2 { margin: 7px 0; font-family: "Songti SC", serif; font-size: clamp(30px, 7vw, 48px); }.counter-demo p { margin: 0; color: var(--muted); }
.counter-demo :deep(.solo-metric-card strong) { font-size: clamp(38px, 10vw, 62px); }
</style>
