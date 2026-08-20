<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Clock3,
  Layers3,
  Maximize2,
  Minimize2,
  MousePointerClick,
  RotateCcw,
  Sparkles,
  Trophy,
} from '@lucide/vue'
import {
  formatPluginDuration,
  usePluginFullscreen,
  usePluginGameActions,
  type ArcadeSnapshot,
} from '@game-hall/plugin-sdk'

interface PlayingCard {
  id: string
  suit: 'spades' | 'hearts' | 'diamonds' | 'clubs'
  suitSymbol: string
  rank: number
  label: string
  color: 'red' | 'black'
  exposed: boolean
}

interface PyramidGameState {
  targetSum?: number
  pyramid?: Array<PlayingCard | null>
  pyramidCleared?: number
  stockRemaining?: number
  stockPass?: number
  maxStockPasses?: number
  wasteCount?: number
  wasteTop?: PlayingCard | null
  availableCardIds?: string[]
  canDraw?: boolean
  removalMoves?: number
  draws?: number
  cardsRemoved?: number
  elapsedMs?: number
  lastRemovedIds?: string[]
  won?: boolean
  result?: string | null
}

const props = defineProps<{ snapshot: ArcadeSnapshot }>()
const actions = usePluginGameActions()
const game = computed(() => props.snapshot.game as PyramidGameState)
const selectedIds = ref<string[]>([])
const sending = ref(false)
const gameRoot = ref<HTMLElement | null>(null)
const { isFullscreen, toggle: togglePluginFullscreen } = usePluginFullscreen(gameRoot)
const hint = ref('选择一张露出的牌，再找一张与它合计为 13 的牌')
const clockBase = ref(0)
const clockSyncedAt = ref(0)
const clockNow = ref(0)
let clockTimer: number | null = null

const pyramidCards = computed<Array<PlayingCard | null>>(() => (
  Array.from({ length: 28 }, (_, index) => game.value.pyramid?.[index] ?? null)
))
const pyramidRows = computed(() => {
  let offset = 0
  return Array.from({ length: 7 }, (_, rowIndex) => {
    const row = pyramidCards.value.slice(offset, offset + rowIndex + 1)
    offset += rowIndex + 1
    return row
  })
})
const availableIds = computed(() => new Set(game.value.availableCardIds ?? []))
const targetSum = computed(() => game.value.targetSum ?? 13)
const canDraw = computed(() => (
  props.snapshot.phase === 'playing'
  && props.snapshot.actions.canAct
  && game.value.canDraw === true
  && !sending.value
))
const selectedCard = computed(() => (
  selectedIds.value.length ? cardById(selectedIds.value[0] ?? '') : null
))
const instruction = computed(() => {
  if (props.snapshot.phase === 'finished') {
    return game.value.won ? '28 张金字塔牌已全部清空' : '牌库耗尽，场上已经没有可消除的组合'
  }
  if (selectedCard.value) {
    const needed = targetSum.value - selectedCard.value.rank
    return `已选择 ${selectedCard.value.label}${selectedCard.value.suitSymbol}，再选择点数为 ${rankLabel(needed)} 的牌`
  }
  return hint.value
})
const displayedElapsedMs = computed(() => {
  if (props.snapshot.phase !== 'playing') return game.value.elapsedMs ?? 0
  return clockBase.value + Math.max(0, clockNow.value - clockSyncedAt.value)
})

function cardById(cardId: string): PlayingCard | null {
  return pyramidCards.value.find((card) => card?.id === cardId) ?? (
    game.value.wasteTop?.id === cardId ? game.value.wasteTop : null
  )
}

function rankLabel(rank: number): string {
  if (rank === 1) return 'A'
  if (rank === 11) return 'J'
  if (rank === 12) return 'Q'
  if (rank === 13) return 'K'
  return String(rank)
}

function canSelect(card: PlayingCard): boolean {
  return props.snapshot.phase === 'playing'
    && props.snapshot.actions.canAct
    && card.exposed
    && availableIds.value.has(card.id)
    && !sending.value
}

async function selectCard(card: PlayingCard) {
  if (!canSelect(card)) return
  if (selectedIds.value.includes(card.id)) {
    selectedIds.value = []
    hint.value = '已取消选择'
    return
  }
  if (card.rank === targetSum.value) {
    await removeCards([card.id])
    return
  }
  const first = selectedCard.value
  if (!first) {
    selectedIds.value = [card.id]
    return
  }
  if (first.rank + card.rank === targetSum.value) {
    await removeCards([first.id, card.id])
    return
  }
  selectedIds.value = [card.id]
  hint.value = `${first.label} 与 ${card.label} 不能凑成 13，已改选后者`
}

async function removeCards(cardIds: string[]) {
  if (sending.value) return
  sending.value = true
  try {
    const removed = await actions.action('remove', { cardIds })
    hint.value = removed ? '消除成功，新的纸牌可能已经露出' : '这组牌暂时不能消除'
    selectedIds.value = []
  } finally {
    sending.value = false
  }
}

async function drawCard() {
  if (!canDraw.value) return
  selectedIds.value = []
  sending.value = true
  try {
    const drawn = await actions.action('draw')
    hint.value = drawn ? '已翻开一张牌，可与金字塔中的露出牌配对' : '没有可以继续翻开的牌'
  } finally {
    sending.value = false
  }
}

async function resetGame() {
  if (sending.value || props.snapshot.phase !== 'playing') return
  sending.value = true
  try {
    const reset = await actions.action('reset')
    if (reset) {
      selectedIds.value = []
      hint.value = '牌局已经重新洗牌，计时从零开始'
    }
  } finally {
    sending.value = false
  }
}

async function restartGame() {
  if (sending.value) return
  sending.value = true
  try {
    await actions.restart()
  } finally {
    sending.value = false
  }
}

function syncClock() {
  const current = performance.now()
  clockBase.value = game.value.elapsedMs ?? 0
  clockSyncedAt.value = current
  clockNow.value = current
}

async function toggleFullscreen() {
  if (!await togglePluginFullscreen()) {
    hint.value = '浏览器未允许进入全屏，请检查浏览器权限后重试'
  }
}

watch(
  () => [props.snapshot.revision, props.snapshot.phase, game.value.elapsedMs] as const,
  syncClock,
  { immediate: true },
)

watch(availableIds, (current) => {
  selectedIds.value = selectedIds.value.filter((cardId) => current.has(cardId))
})

onMounted(() => {
  clockTimer = window.setInterval(() => {
    clockNow.value = performance.now()
  }, 100)
})

onBeforeUnmount(() => {
  if (clockTimer !== null) window.clearInterval(clockTimer)
})
</script>

<template>
  <section ref="gameRoot" class="pyramid-game" :class="{ 'is-fullscreen': isFullscreen }">
    <div class="pyramid-dashboard">
      <header class="pyramid-metrics" aria-label="金字塔纸牌挑战状态">
        <div>
          <Clock3 :size="18" />
          <span><b>{{ formatPluginDuration(displayedElapsedMs) }}</b><small>本轮用时</small></span>
        </div>
        <div>
          <Layers3 :size="18" />
          <span><b>{{ game.pyramidCleared ?? 0 }} / 28</b><small>金字塔已清除</small></span>
        </div>
        <div>
          <Sparkles :size="18" />
          <span><b>{{ game.removalMoves ?? 0 }}</b><small>消除次数</small></span>
        </div>
        <div>
          <MousePointerClick :size="18" />
          <span><b>{{ game.draws ?? 0 }}</b><small>翻牌次数</small></span>
        </div>
      </header>

      <button
        type="button"
        class="fullscreen-button"
        :aria-label="isFullscreen ? '退出全屏牌桌' : '全屏显示牌桌'"
        :title="isFullscreen ? '退出全屏' : '全屏显示牌桌'"
        @click="toggleFullscreen"
      >
        <Minimize2 v-if="isFullscreen" :size="19" />
        <Maximize2 v-else :size="19" />
        <span>{{ isFullscreen ? '退出全屏' : '全屏牌桌' }}</span>
      </button>
    </div>

    <div class="pyramid-layout">
      <section class="surface pyramid-table">
        <header>
          <div><small>PYRAMID · SUM TO 13</small><strong>凑成 K，拆掉整座金字塔</strong></div>
          <span>{{ game.pyramidCleared ?? 0 }} / 28</span>
        </header>

        <div class="pyramid-stage" aria-label="七层金字塔牌阵">
          <div
            v-for="(row, rowIndex) in pyramidRows"
            :key="rowIndex"
            class="pyramid-row"
          >
            <template v-for="(card, columnIndex) in row" :key="card?.id ?? `empty-${rowIndex}-${columnIndex}`">
              <span v-if="!card" class="empty-card-slot" aria-hidden="true" />
              <button
                v-else
                type="button"
                class="playing-card pyramid-card"
                :class="{
                  red: card.color === 'red',
                  covered: !card.exposed,
                  selected: selectedIds.includes(card.id),
                  available: canSelect(card),
                  removed: game.lastRemovedIds?.includes(card.id),
                }"
                :data-card-id="card.id"
                :disabled="!canSelect(card)"
                :aria-label="`${card.label}${card.suitSymbol}${card.exposed ? '，可用' : '，被压住'}`"
                @click="selectCard(card)"
              >
                <span class="card-corner top"><b>{{ card.label }}</b><i>{{ card.suitSymbol }}</i></span>
                <strong class="card-suit">{{ card.suitSymbol }}</strong>
                <span class="card-corner bottom"><b>{{ card.label }}</b><i>{{ card.suitSymbol }}</i></span>
                <span v-if="!card.exposed" class="covered-mark">压</span>
              </button>
            </template>
          </div>
        </div>
      </section>

      <aside class="surface draw-panel">
        <header>
          <div><small>STOCK · ONE PASS</small><strong>翻牌区</strong></div>
          <span>第 {{ game.stockPass ?? 1 }} / {{ game.maxStockPasses ?? 1 }} 轮</span>
        </header>

        <div class="card-piles">
          <div class="pile">
            <small>牌库</small>
            <button
              type="button"
              class="card-back"
              :disabled="!canDraw"
              :aria-label="`翻牌，牌库剩余 ${game.stockRemaining ?? 0} 张`"
              @click="drawCard"
            >
              <span><i /><i /><i /></span>
              <b>{{ game.stockRemaining ?? 0 }}</b>
            </button>
          </div>

          <div class="pile waste-pile">
            <small>弃牌堆顶</small>
            <button
              v-if="game.wasteTop"
              type="button"
              class="playing-card waste-card"
              :class="{
                red: game.wasteTop.color === 'red',
                selected: selectedIds.includes(game.wasteTop.id),
                available: canSelect(game.wasteTop),
              }"
              :data-card-id="game.wasteTop.id"
              :disabled="!canSelect(game.wasteTop)"
              :aria-label="`${game.wasteTop.label}${game.wasteTop.suitSymbol}，弃牌堆顶可用牌`"
              @click="selectCard(game.wasteTop)"
            >
              <span class="card-corner top"><b>{{ game.wasteTop.label }}</b><i>{{ game.wasteTop.suitSymbol }}</i></span>
              <strong class="card-suit">{{ game.wasteTop.suitSymbol }}</strong>
              <span class="card-corner bottom"><b>{{ game.wasteTop.label }}</b><i>{{ game.wasteTop.suitSymbol }}</i></span>
            </button>
            <span v-else class="waste-placeholder">等待翻牌</span>
          </div>
        </div>

        <div class="play-guide" role="status">
          <MousePointerClick :size="19" />
          <span><strong>{{ instruction }}</strong><small>K 会单独消除，其他牌选中两张后自动验证</small></span>
        </div>

        <button
          v-if="snapshot.phase === 'playing'"
          type="button"
          class="reset-button"
          :disabled="sending"
          @click="resetGame"
        >
          <RotateCcw :size="16" />重新洗牌并计时
        </button>

        <details class="rules-card">
          <summary>查看规则与配对</summary>
          <p>A=1、J=11、Q=12、K=13；只有没有被下一层牌压住的牌可用。</p>
          <div><span>A + Q</span><span>2 + J</span><span>3 + 10</span><span>4 + 9</span><span>5 + 8</span><span>6 + 7</span></div>
          <small>牌库只翻一轮。弃牌堆只可使用最上面一张。</small>
        </details>
      </aside>
    </div>

    <section v-if="snapshot.phase === 'finished'" class="surface pyramid-result" :class="{ success: game.won }">
      <span class="result-icon"><Trophy v-if="game.won" :size="28" /><Layers3 v-else :size="28" /></span>
      <div>
        <small>{{ game.won ? 'PYRAMID CLEARED' : 'NO MORE MOVES' }}</small>
        <h2>{{ game.won ? '金字塔已清空' : '本轮牌局结束' }}</h2>
        <p>{{ snapshot.winReason }}</p>
      </div>
      <div class="result-metrics">
        <span><b>{{ game.pyramidCleared ?? 0 }}</b>清除牌数</span>
        <span><b>{{ game.removalMoves ?? 0 }}</b>消除次数</span>
        <span><b>{{ formatPluginDuration(game.elapsedMs ?? 0) }}</b>完成用时</span>
      </div>
      <button
        v-if="snapshot.actions.canRestart"
        type="button"
        class="primary-button"
        :disabled="sending"
        @click="restartGame"
      >
        <RotateCcw :size="18" />再玩一局
      </button>
    </section>
  </section>
</template>

<style scoped>
.pyramid-game {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  display: grid;
  gap: 15px;
  margin: 0 auto;
  container-name: pyramid-game;
  container-type: inline-size;
}

.pyramid-game:fullscreen {
  width: 100%;
  height: 100dvh;
  min-height: 100dvh;
  max-width: none;
  grid-template-rows: auto minmax(0, 1fr);
  align-content: stretch;
  overflow: auto;
  padding: clamp(14px, 2vw, 30px);
  color: var(--text);
  background: var(--bg);
}

.pyramid-game::backdrop {
  background: var(--bg);
}

.pyramid-dashboard {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 9px;
}

.pyramid-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 9px;
}

.pyramid-metrics > div {
  min-width: 0;
  min-height: 62px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  border: 1px solid var(--line);
  border-radius: 13px;
  padding: 9px;
  color: var(--gold);
  background: var(--surface);
}

.pyramid-metrics span,
.pyramid-metrics b,
.pyramid-metrics small {
  min-width: 0;
  display: block;
}

.fullscreen-button {
  min-width: 110px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid color-mix(in srgb, var(--gold) 34%, var(--line));
  border-radius: 13px;
  padding: 9px 13px;
  color: var(--gold);
  background: color-mix(in srgb, var(--gold) 7%, var(--surface));
  font: inherit;
  font-size: 11px;
  font-weight: 850;
  cursor: pointer;
  transition: border-color .15s ease, background .15s ease, transform .15s ease;
}

.fullscreen-button:hover {
  border-color: color-mix(in srgb, var(--gold) 70%, var(--line));
  background: color-mix(in srgb, var(--gold) 13%, var(--surface));
  transform: translateY(-1px);
}

.pyramid-metrics b {
  overflow: hidden;
  color: var(--text);
  font-size: clamp(14px, 2.4vw, 19px);
  font-variant-numeric: tabular-nums;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pyramid-metrics small {
  margin-top: 2px;
  color: var(--muted);
  font-size: 9px;
}

.pyramid-layout {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) clamp(300px, 22cqi, 360px);
  gap: 14px;
  align-items: stretch;
}

.pyramid-table,
.draw-panel {
  min-width: 0;
  overflow: hidden;
}

.pyramid-table {
  position: relative;
  display: grid;
  align-content: start;
  padding: clamp(14px, 2.2cqi, 28px);
  container-name: pyramid-table;
  container-type: inline-size;
  background:
    radial-gradient(circle at 50% 78%, color-mix(in srgb, var(--gold) 12%, transparent), transparent 44%),
    linear-gradient(155deg, color-mix(in srgb, #123d37 44%, var(--surface)), var(--surface) 58%);
}

.pyramid-table::before {
  position: absolute;
  inset: 0;
  border: 1px solid color-mix(in srgb, var(--gold) 18%, transparent);
  border-radius: inherit;
  pointer-events: none;
  content: '';
}

.pyramid-table > header,
.draw-panel > header {
  position: relative;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 12px;
}

.pyramid-table header div,
.draw-panel header div {
  min-width: 0;
}

.pyramid-table header small,
.draw-panel header small,
.pyramid-table header strong,
.draw-panel header strong {
  display: block;
}

.pyramid-table header small,
.draw-panel header small {
  color: var(--gold);
  font-size: 8px;
  font-weight: 900;
  letter-spacing: .14em;
}

.pyramid-table header strong,
.draw-panel header strong {
  margin-top: 4px;
  font-family: "Songti SC", "STSong", serif;
  font-size: clamp(15px, 2.5vw, 20px);
}

.pyramid-table header > span,
.draw-panel header > span {
  flex: 0 0 auto;
  border: 1px solid color-mix(in srgb, var(--gold) 33%, var(--line));
  border-radius: 999px;
  padding: 6px 9px;
  color: var(--gold);
  background: color-mix(in srgb, var(--gold) 7%, transparent);
  font-size: 9px;
  font-weight: 900;
}

.pyramid-stage {
  --card-width: clamp(34px, min(12cqi, 8.7dvh), 98px);
  position: relative;
  display: grid;
  align-content: center;
  justify-content: stretch;
  min-height: clamp(330px, 65dvh, 680px);
  padding: clamp(16px, 2.5cqi, 30px) 0 4px;
}

.pyramid-game:fullscreen .pyramid-table {
  grid-template-rows: auto minmax(0, 1fr);
}

.pyramid-game:fullscreen .pyramid-stage {
  --card-width: clamp(34px, min(12cqi, 9.5dvh), 118px);
  min-height: 0;
}

.pyramid-row {
  position: relative;
  display: flex;
  justify-content: center;
  gap: clamp(2px, .7cqi, 8px);
}

.pyramid-row + .pyramid-row {
  margin-top: clamp(-36px, -3.5cqi, -14px);
}

.playing-card,
.empty-card-slot,
.card-back,
.waste-placeholder {
  width: var(--card-width);
  aspect-ratio: 5 / 7;
  flex: 0 0 auto;
}

.playing-card {
  position: relative;
  z-index: 1;
  border: 1px solid #c9c5b9;
  border-radius: clamp(5px, .8cqi, 11px);
  padding: 0;
  overflow: hidden;
  color: #172025;
  background:
    linear-gradient(135deg, #fffef9, #e9e5d9 78%, #d4cfc1);
  box-shadow: 0 5px 11px #0008, inset 0 0 0 1px #fff9;
  cursor: pointer;
  transition: filter .14s ease, transform .14s ease, box-shadow .14s ease, opacity .14s ease;
}

.playing-card.red {
  color: #b22f3c;
}

.playing-card.covered {
  filter: brightness(.66) saturate(.7);
  cursor: default;
}

.playing-card.covered::after {
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, #07181710, #07181756);
  content: '';
}

.playing-card.available:not(:disabled):hover {
  z-index: 20;
  filter: brightness(1.08);
  transform: translateY(-4px);
  box-shadow: 0 9px 18px #0009, 0 0 0 2px color-mix(in srgb, var(--gold) 54%, transparent);
}

.playing-card.selected {
  z-index: 21;
  border-color: var(--gold);
  transform: translateY(-7px);
  box-shadow: 0 11px 22px #0009, 0 0 0 3px color-mix(in srgb, var(--gold) 47%, transparent);
}

.playing-card:disabled {
  opacity: 1;
}

.card-corner {
  position: absolute;
  z-index: 2;
  left: 9%;
  display: grid;
  justify-items: center;
  line-height: .86;
}

.card-corner.top {
  top: 7%;
}

.card-corner.bottom {
  right: 9%;
  bottom: 7%;
  left: auto;
  transform: rotate(180deg);
}

.card-corner b {
  font-family: Georgia, serif;
  font-size: clamp(9px, 1.8cqi, 17px);
}

.card-corner i {
  margin-top: 2px;
  font-size: clamp(7px, 1.35cqi, 14px);
  font-style: normal;
}

.card-suit {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-family: Georgia, serif;
  font-size: clamp(18px, 4cqi, 42px);
  font-weight: 400;
  text-shadow: 0 1px #fff;
}

.covered-mark {
  position: absolute;
  z-index: 3;
  top: 50%;
  left: 50%;
  width: 42%;
  aspect-ratio: 1;
  display: grid;
  place-items: center;
  transform: translate(-50%, -50%);
  border: 1px solid #ffffff4c;
  border-radius: 50%;
  color: #fff9;
  background: #071817a6;
  font-size: clamp(7px, 1cqi, 11px);
  font-weight: 900;
}

.empty-card-slot {
  position: relative;
  z-index: 0;
  border: 1px dashed color-mix(in srgb, var(--gold) 18%, transparent);
  border-radius: clamp(5px, .8cqi, 11px);
  background: #00000012;
}

.draw-panel {
  display: grid;
  align-content: start;
  gap: 15px;
  padding: 18px;
}

.draw-panel > header {
  padding-bottom: 13px;
}

.draw-panel header > span {
  padding-inline: 7px;
  font-size: 8px;
}

.card-piles {
  --card-width: 75px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 13px 9px;
  background: var(--surface-inset);
}

.pile {
  min-width: 0;
  display: grid;
  justify-items: center;
  align-content: start;
  gap: 8px;
}

.pile > small {
  color: var(--muted);
  font-size: 8px;
  font-weight: 850;
}

.card-back {
  position: relative;
  display: grid;
  place-items: center;
  border: 2px solid #cfb66e;
  border-radius: 9px;
  padding: 5px;
  color: #f1d68b;
  background: #163b38;
  box-shadow: 0 6px 12px #0008, inset 0 0 0 2px #0b2422, inset 0 0 0 4px #d0ae5655;
  cursor: pointer;
  transition: filter .15s, transform .15s;
}

.card-back::before {
  position: absolute;
  inset: 7px;
  border: 1px solid #d8bc6e70;
  border-radius: 5px;
  background:
    repeating-linear-gradient(45deg, transparent 0 5px, #d7ba6920 5px 7px),
    repeating-linear-gradient(-45deg, transparent 0 5px, #d7ba6917 5px 7px);
  content: '';
}

.card-back span {
  position: relative;
  z-index: 1;
  display: flex;
  gap: 3px;
  transform: rotate(-45deg);
}

.card-back i {
  width: 7px;
  height: 7px;
  border: 1px solid currentColor;
  transform: rotate(45deg);
}

.card-back b {
  position: absolute;
  z-index: 2;
  right: 5px;
  bottom: 4px;
  min-width: 20px;
  border-radius: 999px;
  padding: 2px 4px;
  color: #102421;
  background: #e0c779;
  font-size: 9px;
}

.card-back:hover:not(:disabled) {
  filter: brightness(1.12);
  transform: translateY(-3px);
}

.card-back:disabled {
  filter: grayscale(.7) brightness(.55);
  cursor: not-allowed;
}

.waste-card .card-corner b {
  font-size: 14px;
}

.waste-card .card-corner i {
  font-size: 11px;
}

.waste-card .card-suit {
  font-size: 32px;
}

.waste-placeholder {
  display: grid;
  place-items: center;
  border: 1px dashed var(--line);
  border-radius: 9px;
  color: var(--muted);
  background: #0002;
  font-size: 8px;
}

.play-guide {
  min-height: 76px;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border: 1px solid color-mix(in srgb, var(--gold) 31%, var(--line));
  border-radius: 14px;
  padding: 12px;
  color: var(--gold);
  background: color-mix(in srgb, var(--gold) 7%, var(--surface-inset));
}

.play-guide svg {
  flex: 0 0 auto;
  margin-top: 2px;
}

.play-guide span,
.play-guide strong,
.play-guide small {
  min-width: 0;
  display: block;
}

.play-guide strong {
  color: var(--text);
  font-size: 10px;
  line-height: 1.55;
}

.play-guide small {
  margin-top: 4px;
  color: var(--muted);
  font-size: 8px;
  line-height: 1.45;
}

.reset-button {
  min-height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 1px solid var(--line);
  border-radius: 11px;
  color: var(--muted);
  background: #0002;
  font-weight: 800;
  cursor: pointer;
}

.reset-button:hover:not(:disabled) {
  border-color: color-mix(in srgb, var(--gold) 45%, var(--line));
  color: var(--gold);
}

.reset-button:disabled {
  opacity: .45;
  cursor: not-allowed;
}

.rules-card {
  border-top: 1px solid var(--line);
  padding-top: 12px;
  color: var(--muted);
  font-size: 9px;
}

.rules-card summary {
  color: var(--gold);
  font-weight: 850;
  cursor: pointer;
}

.rules-card p {
  margin: 10px 0 8px;
  line-height: 1.55;
}

.rules-card div {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px;
}

.rules-card div span {
  border: 1px solid var(--line);
  border-radius: 7px;
  padding: 5px 2px;
  color: var(--text);
  background: var(--surface-inset);
  text-align: center;
}

.rules-card > small {
  display: block;
  margin-top: 8px;
  line-height: 1.5;
}

.pyramid-result {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 16px;
  padding: clamp(16px, 3vw, 24px);
  border-color: color-mix(in srgb, #b46565 35%, var(--line));
}

.pyramid-result.success {
  border-color: color-mix(in srgb, #73d6aa 44%, var(--line));
  background:
    radial-gradient(circle at 10% 50%, color-mix(in srgb, #73d6aa 10%, transparent), transparent 26%),
    var(--surface);
}

.result-icon {
  width: 54px;
  aspect-ratio: 1;
  display: grid;
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--gold) 38%, var(--line));
  border-radius: 17px;
  color: var(--gold);
  background: color-mix(in srgb, var(--gold) 8%, var(--surface-inset));
}

.pyramid-result small {
  color: var(--gold);
  font-size: 8px;
  font-weight: 900;
  letter-spacing: .14em;
}

.pyramid-result h2 {
  margin: 3px 0;
  font-family: "Songti SC", "STSong", serif;
  font-size: clamp(23px, 4vw, 34px);
}

.pyramid-result p {
  margin: 0;
  color: var(--muted);
  font-size: 9px;
}

.result-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.result-metrics span {
  min-width: 70px;
  border-left: 1px solid var(--line);
  padding: 4px 10px;
  color: var(--muted);
  font-size: 8px;
  text-align: center;
}

.result-metrics b {
  display: block;
  margin-bottom: 2px;
  color: var(--text);
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.pyramid-result .primary-button {
  min-height: 43px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}

@container pyramid-game (max-width: 1050px) {
  .pyramid-layout {
    grid-template-columns: 1fr;
  }

  .draw-panel {
    grid-template-columns: minmax(190px, .72fr) minmax(0, 1.28fr);
    align-items: start;
  }

  .draw-panel > header {
    grid-column: 1 / -1;
  }

  .card-piles {
    grid-row: span 3;
  }

  .pyramid-result {
    grid-template-columns: auto minmax(0, 1fr) auto;
  }

  .result-metrics {
    grid-column: 1 / -1;
    grid-row: 2;
  }
}

@container pyramid-game (max-width: 620px) {
  .pyramid-dashboard {
    grid-template-columns: 1fr;
  }

  .fullscreen-button {
    min-height: 42px;
  }

  .pyramid-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 7px;
  }

  .pyramid-metrics > div {
    min-height: 54px;
  }

  .pyramid-stage {
    --card-width: clamp(32px, 12cqi, 50px);
    min-height: 330px;
    padding-top: 18px;
  }

  .pyramid-row + .pyramid-row {
    margin-top: -14px;
  }

  .draw-panel {
    grid-template-columns: 1fr;
    gap: 11px;
    padding: 14px;
  }

  .card-piles {
    grid-row: auto;
  }

  .pyramid-result {
    grid-template-columns: auto minmax(0, 1fr);
    gap: 11px;
  }

  .pyramid-result .primary-button,
  .result-metrics {
    grid-column: 1 / -1;
  }

  .pyramid-result .primary-button {
    justify-content: center;
  }
}

@container pyramid-game (max-width: 380px) {
  .pyramid-table {
    padding-inline: 8px;
  }

  .pyramid-stage {
    --card-width: 34px;
    min-height: 305px;
  }

  .pyramid-row {
    gap: 1px;
  }

  .pyramid-table header strong {
    font-size: 14px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .playing-card,
  .card-back,
  .fullscreen-button {
    transition: none;
  }
}
</style>
