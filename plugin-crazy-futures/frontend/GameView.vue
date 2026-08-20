<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Banknote,
  Eye as EyeIcon,
  Gavel,
  Hand,
  Layers3,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  WalletCards,
} from '@lucide/vue'
import {
  usePluginGameActions,
  type ArcadeSnapshot,
} from '@game-hall/plugin-sdk'

import CardFace from './components/CardFace.vue'
import MoneyStack from './components/MoneyStack.vue'
import PriceBoard from './components/PriceBoard.vue'
import type {
  ActiveEffectView,
  CardView,
  CommodityId,
  CrazyFuturesGame,
  LedgerView,
  PlayableCard,
} from './types'

import copperImage from '../image/commodity-copper.png'
import cottonImage from '../image/commodity-cotton.png'
import goldImage from '../image/commodity-gold.png'
import oilImage from '../image/commodity-crude-oil.png'

const props = defineProps<{ snapshot: ArcadeSnapshot }>()
const actions = usePluginGameActions()
const busy = ref(false)
const borrowAmount = ref(0)
const auctionCommodity = ref<CommodityId>('oil')
const auctionSide = ref<'buy' | 'sell'>('buy')
const auctionQuoteIndex = ref<number | null>(null)
const bidQuoteIndex = ref<number | null>(null)
const selectedCardId = ref<string | null>(null)
const selectedCommodity = ref<CommodityId | null>(null)
const selectedEffectId = ref<string | null>(null)
const selectedDiscardIds = ref<string[]>([])
const marketFlashes = ref<Record<string, 'up' | 'down'>>({})
const moneyDelta = ref<number | null>(null)
const dealNonce = ref(0)
const eventFlipNonce = ref(0)

const marketImages: Record<CommodityId, string> = {
  oil: oilImage,
  gold: goldImage,
  cotton: cottonImage,
  copper: copperImage,
}

const game = computed(() => props.snapshot.game as CrazyFuturesGame)
const legal = computed(() => game.value.legalActions ?? {})
const selfLedger = computed(() => ledgerFor(props.snapshot.self.id))
const currentPlayer = computed(() => playerFor(game.value.currentPlayerId))
const auctionLeader = computed(() => playerFor(game.value.auction?.leaderId))
const publicEvents = computed(() => [...(game.value.publicEvents ?? [])].reverse().slice(0, 4))
const latestEvents = computed(() => [...(game.value.events ?? [])].reverse().slice(0, 18))
const playableById = computed(() => new Map(
  (legal.value.playableCards ?? []).map((option) => [option.instanceId, option]),
))
const selectedCard = computed(() => (
  game.value.hand?.find((card) => card.instanceId === selectedCardId.value) ?? null
))
const selectedPlayOption = computed<PlayableCard | null>(() => (
  selectedCardId.value ? playableById.value.get(selectedCardId.value) ?? null : null
))
const auctionStartOption = computed(() => (
  legal.value.auctionStarts?.find((option) => (
    option.commodity === auctionCommodity.value && option.side === auctionSide.value
  )) ?? null
))
const auctionQuoteIndices = computed(() => auctionStartOption.value?.quoteIndices ?? [])
const myPendingDiscard = computed(() => (
  game.value.pendingChoice?.isMine && legal.value.discardCount
    ? legal.value.discardCount
    : 0
))
const canPlaySelected = computed(() => {
  const option = selectedPlayOption.value
  if (!option || busy.value) return false
  if (option.commodities?.length && !selectedCommodity.value) return false
  if (option.effectIds?.length && !selectedEffectId.value) return false
  return true
})
const canSubmitDiscard = computed(() => (
  myPendingDiscard.value > 0
  && selectedDiscardIds.value.length === myPendingDiscard.value
  && !busy.value
))
const statusTitle = computed(() => {
  if (props.snapshot.phase === 'finished') return '八个月交易已经结束'
  if (game.value.pendingChoice?.isMine) {
    return game.value.pendingChoice.kind === 'liquidation'
      ? '请选择一份头寸强制平仓'
      : `请选择 ${game.value.pendingChoice.count ?? 0} 张牌弃置`
  }
  if (game.value.currentPlayerId === props.snapshot.self.id) return `轮到你：${game.value.stageLabel}`
  return `${game.value.stageLabel} · 等待 ${currentPlayer.value?.name ?? '系统结算'}`
})
const statusDetail = computed(() => {
  if (props.snapshot.phase === 'finished') return props.snapshot.winReason ?? '最终净资产已经确定'
  if (game.value.auction) {
    return `${auctionLeader.value?.name ?? '领先者'} · ${game.value.auction.price} 万 · ${game.value.auction.side === 'buy' ? '最高买价' : '最低卖价'}`
  }
  if (game.value.stage === 'card') return '打一张牌、对封板商品只减仓，或放弃；连续一整圈无人行动后结算'
  return `第 ${game.value.round}/${game.value.maxRounds} 个月 · 所有金额单位均为万金币`
})

function playerFor(playerId?: string | null) {
  return props.snapshot.players.find((player) => player.id === playerId)
}

function ledgerFor(playerId?: string | null): LedgerView | undefined {
  return game.value.ledgers?.find((ledger) => ledger.playerId === playerId)
}

function priceAt(index: number): number {
  return game.value.priceLadder?.[index] ?? 0
}

function effectLabel(effect: ActiveEffectView): string {
  const moves = effect.moves.map((move) => {
    const market = game.value.markets.find((item) => item.commodity === move.commodity)
    return `${market?.name ?? move.commodity}${move.delta > 0 ? '+' : ''}${move.delta}`
  }).join('、')
  return `${effect.cardName}（${moves}，剩 ${effect.remainingTriggers} 次）`
}

async function send(action: string, payload: Record<string, unknown>) {
  if (busy.value) return
  busy.value = true
  try {
    await actions.action(action, payload)
  }
  finally {
    busy.value = false
  }
}

function submitBorrow() {
  void send('borrow', { amount: borrowAmount.value })
}

function submitAuction() {
  if (auctionQuoteIndex.value === null) return
  void send('start_auction', {
    commodity: auctionCommodity.value,
    side: auctionSide.value,
    quoteIndex: auctionQuoteIndex.value,
  })
}

function submitBid() {
  if (bidQuoteIndex.value === null) return
  void send('bid', { quoteIndex: bidQuoteIndex.value })
}

function selectCard(card: CardView) {
  if (myPendingDiscard.value) {
    if (selectedDiscardIds.value.includes(card.instanceId)) {
      selectedDiscardIds.value = selectedDiscardIds.value.filter((id) => id !== card.instanceId)
    }
    else if (selectedDiscardIds.value.length < myPendingDiscard.value) {
      selectedDiscardIds.value = [...selectedDiscardIds.value, card.instanceId]
    }
    return
  }
  if (!playableById.value.has(card.instanceId)) return
  selectedCardId.value = selectedCardId.value === card.instanceId ? null : card.instanceId
}

function submitCard() {
  if (!canPlaySelected.value || !selectedCardId.value) return
  const payload: Record<string, unknown> = { instanceId: selectedCardId.value }
  if (selectedCommodity.value) payload.commodity = selectedCommodity.value
  if (selectedEffectId.value) payload.effectId = selectedEffectId.value
  void send('play_card', payload)
}

function submitDiscard() {
  if (!canSubmitDiscard.value) return
  void send('discard_cards', { instanceIds: [...selectedDiscardIds.value] })
}

watch(
  () => legal.value.borrowAmounts?.join('|') ?? '',
  () => {
    if (!legal.value.borrowAmounts?.includes(borrowAmount.value)) {
      borrowAmount.value = legal.value.borrowAmounts?.[0] ?? 0
    }
  },
  { immediate: true },
)

watch(
  () => JSON.stringify(legal.value.auctionStarts ?? []),
  () => {
    const options = legal.value.auctionStarts ?? []
    if (!options.some((item) => item.commodity === auctionCommodity.value && item.side === auctionSide.value)) {
      const first = options[0]
      if (first) {
        auctionCommodity.value = first.commodity
        auctionSide.value = first.side
      }
    }
  },
  { immediate: true },
)

watch(
  auctionQuoteIndices,
  (indices) => {
    if (auctionQuoteIndex.value === null || !indices.includes(auctionQuoteIndex.value)) {
      auctionQuoteIndex.value = indices[0] ?? null
    }
  },
  { immediate: true },
)

watch(
  () => legal.value.bidQuoteIndices?.join('|') ?? '',
  () => {
    const indices = legal.value.bidQuoteIndices ?? []
    if (bidQuoteIndex.value === null || !indices.includes(bidQuoteIndex.value)) {
      bidQuoteIndex.value = indices[0] ?? null
    }
  },
  { immediate: true },
)

watch(selectedPlayOption, (option) => {
  selectedCommodity.value = option?.commodities?.[0] ?? null
  selectedEffectId.value = option?.effectIds?.[0] ?? null
})

watch(
  () => game.value.hand?.map((card) => card.instanceId).join('|') ?? '',
  () => {
    const available = new Set(game.value.hand?.map((card) => card.instanceId) ?? [])
    if (selectedCardId.value && !available.has(selectedCardId.value)) selectedCardId.value = null
    selectedDiscardIds.value = selectedDiscardIds.value.filter((id) => available.has(id))
  },
)

let initializedEvents = false
let seenEventSeq = 0
watch(
  () => game.value.events?.map((event) => event.seq).join('|') ?? '',
  () => {
    const events = game.value.events ?? []
    if (!initializedEvents) {
      seenEventSeq = Math.max(0, ...events.map((event) => event.seq))
      initializedEvents = true
      return
    }
    const fresh = events.filter((event) => event.seq > seenEventSeq)
    seenEventSeq = Math.max(seenEventSeq, ...fresh.map((event) => event.seq))
    for (const event of fresh) {
      const commodity = event.data.commodity
      const fromIndex = Number(event.data.fromIndex)
      const toIndex = Number(event.data.toIndex)
      if (typeof commodity === 'string' && Number.isFinite(fromIndex) && Number.isFinite(toIndex)) {
        const rail = event.type === 'spot_move' ? 'spot' : 'futures'
        marketFlashes.value = {
          ...marketFlashes.value,
          [`${rail}-${commodity}`]: toIndex >= fromIndex ? 'up' : 'down',
        }
        window.setTimeout(() => {
          const next = { ...marketFlashes.value }
          delete next[`${rail}-${commodity}`]
          marketFlashes.value = next
        }, 760)
      }
      if (event.type === 'deal' && event.data.playerId === props.snapshot.self.id) dealNonce.value += 1
      if (event.type === 'public_event') eventFlipNonce.value += 1
      if (event.data.playerId === props.snapshot.self.id && typeof event.data.cashDelta === 'number') {
        moneyDelta.value = event.data.cashDelta
        window.setTimeout(() => { moneyDelta.value = null }, 760)
      }
    }
  },
  { immediate: true },
)
</script>

<template>
  <section class="crazy-futures surface">
    <header class="game-header">
      <div>
        <small>CRAZY FUTURES · 8 MONTHS</small>
        <h2>疯狂期货</h2>
        <p>公开新闻属于所有人，真正昂贵的是你手里的信息。</p>
      </div>
      <div class="round-meter" aria-label="游戏轮次">
        <span>MONTH</span><strong>{{ game.round }}</strong><i>/ {{ game.maxRounds }}</i>
      </div>
    </header>

    <div class="status-banner" :class="{ mine: game.currentPlayerId === snapshot.self.id || game.pendingChoice?.isMine }" role="status">
      <Gavel v-if="game.stage === 'auction'" :size="22" />
      <ShieldAlert v-else-if="game.stage === 'liquidation'" :size="22" />
      <Sparkles v-else :size="22" />
      <span><strong>{{ statusTitle }}</strong><small>{{ statusDetail }}</small></span>
    </div>

    <section class="commodity-grid" aria-label="四种商品行情">
      <article v-for="market in game.markets" :key="market.commodity" :class="market.commodity">
        <img :src="marketImages[market.commodity]" :alt="market.name">
        <div class="commodity-shade" />
        <header><strong>{{ market.name }}</strong><small v-if="market.seal">{{ market.seal === 'up' ? '涨停封板' : '跌停封板' }}</small></header>
        <dl>
          <div><dt>现货</dt><dd>{{ market.spotPrice }}</dd></div>
          <div><dt>期货</dt><dd>{{ market.currentPrice }}</dd></div>
          <div><dt>区间</dt><dd>{{ market.lowLimitPrice }}–{{ market.highLimitPrice }}</dd></div>
        </dl>
        <footer>最后有效成交：{{ market.validTradePrices.length ? market.validTradePrices.join(' / ') : '本月暂无' }}</footer>
      </article>
    </section>

    <PriceBoard :markets="game.markets" :ladder="game.priceLadder" :flashes="marketFlashes" />

    <div class="center-grid">
      <section class="event-table">
        <header><span><b>公共事件</b><small>所有玩家可见 · 只移动现货</small></span><strong>{{ game.deckCounts.public }} 张待翻</strong></header>
        <div class="event-cards">
          <div class="event-deck" :key="eventFlipNonce"><CardFace kind="event" back compact /><b>{{ game.deckCounts.public }}</b></div>
          <TransitionGroup name="event-card">
            <CardFace v-for="card in publicEvents" :key="card.instanceId" :card="card" kind="event" compact />
          </TransitionGroup>
        </div>
        <div class="effect-strip">
          <span v-if="!game.activeEffects.length">当前没有持续现货效果</span>
          <span v-for="effect in game.activeEffects" :key="effect.effectId" :class="effect.direction">
            {{ effectLabel(effect) }}
          </span>
        </div>
      </section>

      <aside class="action-panel">
        <header><span><b>交易席操作</b><small>{{ game.stageLabel }}</small></span><i :class="{ live: game.currentPlayerId === snapshot.self.id }" /></header>

        <div v-if="snapshot.phase === 'finished'" class="final-panel">
          <TrendingUp :size="30" />
          <strong>最终资产排名</strong>
          <ol>
            <li v-for="(playerId, index) in game.rankings" :key="playerId">
              <b>#{{ index + 1 }} {{ playerFor(playerId)?.name }}</b>
              <span>{{ ledgerFor(playerId)?.bankrupt ? '破产' : `${ledgerFor(playerId)?.finalScore ?? 0} 万` }}</span>
            </li>
          </ol>
        </div>

        <div v-else-if="myPendingDiscard" class="action-block">
          <Hand :size="24" /><strong>整理手牌</strong>
          <p>已选 {{ selectedDiscardIds.length }} / {{ myPendingDiscard }} 张。请从下方手牌中选择。</p>
          <button class="primary" :disabled="!canSubmitDiscard" @click="submitDiscard">确认弃牌</button>
        </div>

        <div v-else-if="legal.liquidationCommodities?.length" class="action-block danger">
          <ShieldAlert :size="24" /><strong>交易所强制平仓</strong>
          <p>封板不能阻止强平。请选择一份头寸按本轮官方收盘价关闭。</p>
          <div class="button-grid">
            <button v-for="commodity in legal.liquidationCommodities" :key="commodity" :disabled="busy" @click="send('choose_liquidation', { commodity })">
              {{ game.markets.find((market) => market.commodity === commodity)?.name }}
            </button>
          </div>
        </div>

        <div v-else-if="legal.borrowAmounts" class="action-block">
          <Banknote :size="24" /><strong>本月借贷</strong>
          <p>累计本金最多 100 万；本月借款从本月起计单利。</p>
          <div class="amount-grid">
            <button v-for="amount in legal.borrowAmounts" :key="amount" :class="{ selected: borrowAmount === amount }" @click="borrowAmount = amount">{{ amount }}</button>
          </div>
          <button class="primary" :disabled="busy" @click="submitBorrow">{{ borrowAmount ? `借入 ${borrowAmount} 万` : '本月不借贷' }}</button>
        </div>

        <div v-else-if="legal.auctionStarts" class="action-block">
          <Gavel :size="24" /><strong>强制发起竞价</strong>
          <p>第 {{ game.initiationNumber }} / {{ game.initiationTotal }} 次发起；每次固定成交一份合约。</p>
          <div class="commodity-buttons">
            <button v-for="option in legal.auctionStarts" :key="`${option.commodity}-${option.side}`" :class="{ selected: auctionCommodity === option.commodity && auctionSide === option.side }" @click="auctionCommodity = option.commodity; auctionSide = option.side">
              {{ game.markets.find((market) => market.commodity === option.commodity)?.name }} · {{ option.side === 'buy' ? '买' : '卖' }}
            </button>
          </div>
          <div class="quote-grid">
            <button v-for="index in auctionQuoteIndices" :key="index" :class="{ selected: auctionQuoteIndex === index }" @click="auctionQuoteIndex = index">{{ priceAt(index) }}</button>
          </div>
          <button class="primary" :disabled="auctionQuoteIndex === null || busy" @click="submitAuction">发起 {{ auctionSide === 'buy' ? '买入' : '卖出' }}竞价</button>
        </div>

        <div v-else-if="legal.canSkipAuction" class="action-block">
          <ShieldAlert :size="24" /><strong>没有合法订单</strong><p>当前现金、头寸与价格限制下无法发起成交。</p>
          <button class="primary" :disabled="busy" @click="send('pass_bid', {})">确认跳过</button>
        </div>

        <div v-else-if="legal.canPassBid" class="action-block">
          <Gavel :size="24" /><strong>参与当前竞价</strong>
          <p>{{ game.auction?.side === 'buy' ? '只能提高买价' : '只能降低卖价' }}；一旦放弃不能重新加入。</p>
          <div v-if="legal.bidQuoteIndices?.length" class="quote-grid">
            <button v-for="index in legal.bidQuoteIndices" :key="index" :class="{ selected: bidQuoteIndex === index }" @click="bidQuoteIndex = index">{{ priceAt(index) }}</button>
          </div>
          <div class="split-actions">
            <button class="primary" :disabled="bidQuoteIndex === null || busy" @click="submitBid">提交新报价</button>
            <button :disabled="busy" @click="send('pass_bid', {})">放弃竞价</button>
          </div>
        </div>

        <div v-else-if="legal.canPassCard" class="action-block">
          <WalletCards :size="24" /><strong>出牌行动</strong>
          <p>每次最多打一张牌，也可以保留全部手牌。</p>
          <template v-if="selectedPlayOption?.commodities?.length">
            <small>选择目标商品</small>
            <div class="button-grid">
              <button v-for="commodity in selectedPlayOption.commodities" :key="commodity" :class="{ selected: selectedCommodity === commodity }" @click="selectedCommodity = commodity">
                {{ game.markets.find((market) => market.commodity === commodity)?.name }}
              </button>
            </div>
          </template>
          <template v-if="selectedPlayOption?.effectIds?.length">
            <small>选择持续效果</small>
            <select v-model="selectedEffectId">
              <option v-for="effectId in selectedPlayOption.effectIds" :key="effectId" :value="effectId">{{ game.activeEffects.find((effect) => effect.effectId === effectId)?.cardName }}</option>
            </select>
          </template>
          <div v-if="legal.reduceOnlyCommodities?.length" class="reduce-row">
            <small>封板只减仓</small>
            <button v-for="commodity in legal.reduceOnlyCommodities" :key="commodity" :disabled="busy" @click="send('reduce_only', { commodity })">平 {{ game.markets.find((market) => market.commodity === commodity)?.name }} 1 份</button>
          </div>
          <div class="split-actions">
            <button class="primary" :disabled="!canPlaySelected" @click="submitCard">{{ selectedCard ? `打出《${selectedCard.name}》` : '先选择一张可用手牌' }}</button>
            <button :disabled="busy" @click="send('pass_card', {})">本次不行动</button>
          </div>
        </div>

        <div v-else class="wait-block">
          <Layers3 :size="25" /><strong>等待其他交易员</strong><p>{{ statusDetail }}</p>
        </div>
      </aside>
    </div>

    <section class="ledger-panel">
      <header><span><b>公开玩家账本</b><small>现金、保证金、贷款和净头寸均为公开信息</small></span></header>
      <div class="ledger-scroll">
        <article v-for="ledger in game.ledgers" :key="ledger.playerId" :class="{ current: ledger.playerId === game.currentPlayerId, bankrupt: ledger.bankrupt }">
          <header><b>{{ playerFor(ledger.playerId)?.name }}</b><span v-if="ledger.bankrupt">破产</span><span v-else>{{ ledger.estimatedEquity }} 万权益</span></header>
          <dl>
            <div><dt>现金</dt><dd>{{ ledger.cash }}</dd></div><div><dt>保证金</dt><dd>{{ ledger.margin }}</dd></div>
            <div><dt>贷款</dt><dd>{{ ledger.loanPrincipal }}</dd></div><div><dt>终局利息</dt><dd>{{ ledger.loanInterest }}</dd></div>
          </dl>
          <div class="positions">
            <span v-for="market in game.markets" :key="market.commodity" :class="{ long: ledger.positions[market.commodity].quantity > 0, short: ledger.positions[market.commodity].quantity < 0 }">
              {{ market.name.slice(0, 1) }} {{ ledger.positions[market.commodity].quantity > 0 ? '+' : '' }}{{ ledger.positions[market.commodity].quantity }}
            </span>
          </div>
        </article>
      </div>
    </section>

    <section class="portfolio-panel">
      <div class="cash-tray">
        <header><span><b>你的资金匣</b><small>可用现金 {{ selfLedger?.cash ?? 0 }} 万</small></span><strong>保证金 {{ selfLedger?.margin ?? 0 }}</strong></header>
        <MoneyStack :amount="selfLedger?.cash ?? 0" :delta="moneyDelta" />
      </div>
      <div class="hand-tray" :class="{ dealing: dealNonce }">
        <header><span><b>你的个人牌</b><small>轮末上限 6 张 · 当前 {{ game.hand?.length ?? 0 }} 张</small></span><strong>{{ game.deckCounts.personal }} 张牌堆</strong></header>
        <TransitionGroup name="hand-card" tag="div" class="hand-cards">
          <button
            v-for="card in game.hand"
            :key="card.instanceId"
            type="button"
            :class="{
              selected: selectedCardId === card.instanceId || selectedDiscardIds.includes(card.instanceId),
              playable: playableById.has(card.instanceId),
              unavailable: !myPendingDiscard && !playableById.has(card.instanceId),
            }"
            :aria-disabled="!myPendingDiscard && !playableById.has(card.instanceId)"
            :aria-label="`${card.name}。${card.text} ${card.durationText}`"
            @click="selectCard(card)"
          ><CardFace :card="card" kind="personal" compact :focusable="false" /></button>
        </TransitionGroup>
        <p v-if="!game.hand?.length">当前没有个人牌。</p>
      </div>
    </section>

    <section v-if="game.peekCards?.length" class="peek-panel">
      <header><EyeIcon /><span><b>预见：下个月公共事件</b><small>仅你可见，顺序保持不变</small></span></header>
      <div><CardFace v-for="card in game.peekCards" :key="card.instanceId" :card="card" kind="event" compact /></div>
    </section>

    <section class="history-panel">
      <header><span><b>交易所播报</b><small>最近 {{ latestEvents.length }} 条服务端裁定</small></span></header>
      <ol>
        <li v-for="event in latestEvents" :key="event.seq"><i>{{ event.seq }}</i><span>{{ event.message }}</span></li>
      </ol>
    </section>
  </section>
</template>

<style scoped>
.crazy-futures { width: min(100%, 1600px); min-width: 0; max-width: 100%; display: grid; gap: 16px; margin: 0 auto; padding: clamp(13px, 2vw, 26px); overflow: hidden; background: radial-gradient(circle at 92% 3%, color-mix(in srgb, var(--gold) 8%, transparent), transparent 28%); }
.game-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--line); padding-bottom: 14px; }.game-header small { color: var(--gold); font-size: 8px; font-weight: 900; letter-spacing: .17em; }.game-header h2 { margin: 3px 0 0; font-family: "Songti SC", "STSong", serif; font-size: clamp(31px, 5vw, 48px); line-height: 1; }.game-header p { margin: 7px 0 0; color: var(--muted); font-size: 9px; }.round-meter { display: grid; grid-template-columns: auto auto auto; align-items: end; gap: 6px; color: var(--muted); }.round-meter span { align-self: center; font-size: 7px; letter-spacing: .13em; }.round-meter strong { color: var(--gold); font-family: Georgia, serif; font-size: 39px; line-height: .8; }.round-meter i { font-size: 10px; font-style: normal; }
.status-banner { min-width: 0; min-height: 66px; display: flex; align-items: center; gap: 11px; border: 1px solid var(--line); border-radius: 15px; padding: 11px 14px; color: var(--muted); background: var(--surface-inset); }.status-banner.mine { border-color: color-mix(in srgb, var(--gold) 55%, var(--line)); color: var(--gold); background: color-mix(in srgb, var(--gold) 8%, var(--surface-inset)); }.status-banner > span { min-width: 0; display: grid; gap: 4px; }.status-banner strong,.status-banner small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.status-banner strong { color: var(--text); font-size: 13px; }.status-banner small { color: var(--muted); font-size: 8px; }
.commodity-grid { min-width: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px; }.commodity-grid article { position: relative; min-width: 0; min-height: 148px; display: grid; align-content: end; overflow: hidden; border: 1px solid var(--line); border-radius: 16px; padding: 11px; color: white; isolation: isolate; }.commodity-grid article > img { position: absolute; inset: 0; z-index: -2; width: 100%; height: 100%; object-fit: cover; transition: transform .35s ease; }.commodity-shade { position: absolute; inset: 0; z-index: -1; background: linear-gradient(180deg, rgb(5 15 24 / 4%), rgb(5 15 24 / 88%)); }.commodity-grid article > header { display: flex; align-items: center; justify-content: space-between; gap: 6px; }.commodity-grid article > header strong { font-family: "Songti SC", "STSong", serif; font-size: 20px; }.commodity-grid article > header small { border: 1px solid rgb(255 255 255 / 45%); border-radius: 999px; padding: 3px 5px; font-size: 6px; }.commodity-grid dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; margin: 7px 0 0; }.commodity-grid dl div { min-width: 0; display: grid; gap: 2px; border-left: 1px solid rgb(255 255 255 / 20%); padding-left: 6px; }.commodity-grid dt { color: rgb(255 255 255 / 62%); font-size: 6px; }.commodity-grid dd { overflow: hidden; margin: 0; font-size: 11px; font-weight: 900; text-overflow: ellipsis; white-space: nowrap; }.commodity-grid footer { overflow: hidden; margin-top: 6px; color: rgb(255 255 255 / 63%); font-size: 6px; text-overflow: ellipsis; white-space: nowrap; }
.center-grid { min-width: 0; display: grid; grid-template-columns: minmax(0, 1.08fr) minmax(390px, .92fr); gap: 14px; }.event-table,.action-panel,.ledger-panel,.cash-tray,.hand-tray,.peek-panel,.history-panel { min-width: 0; border: 1px solid var(--line); border-radius: 17px; background: color-mix(in srgb, var(--surface-elevated) 46%, transparent); }.event-table,.ledger-panel,.cash-tray,.hand-tray,.peek-panel,.history-panel { padding: 13px; }.action-panel { padding: clamp(16px, 1.6vw, 22px); }.event-table > header,.action-panel > header,.ledger-panel > header,.cash-tray > header,.hand-tray > header,.history-panel > header { display: flex; align-items: center; justify-content: space-between; gap: 9px; border-bottom: 1px solid var(--line); padding-bottom: 10px; }.event-table header > span,.action-panel header > span,.ledger-panel header > span,.cash-tray header > span,.hand-tray header > span,.history-panel header > span { display: grid; gap: 2px; }.event-table header b,.action-panel header b,.ledger-panel header b,.cash-tray header b,.hand-tray header b,.history-panel header b { color: var(--gold); font-size: 10px; letter-spacing: .08em; }.event-table header small,.action-panel header small,.ledger-panel header small,.cash-tray header small,.hand-tray header small,.history-panel header small { color: var(--muted); font-size: 8px; }.event-table header > strong,.cash-tray header > strong,.hand-tray header > strong { color: var(--muted); font-size: 9px; }
.event-cards { min-width: 0; display: grid; grid-template-columns: repeat(5, minmax(104px, 1fr)); gap: 9px; padding-top: 13px; }.event-deck { position: relative; }.event-deck > b { position: absolute; right: 7px; bottom: 7px; min-width: 25px; border-radius: 999px; padding: 4px 6px; color: var(--accent-contrast); background: var(--gold); font-size: 9px; text-align: center; }.effect-strip { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 11px; }.effect-strip span { border: 1px solid var(--line); border-radius: 999px; padding: 6px 8px; color: var(--muted); background: var(--surface-inset); font-size: 8px; }.effect-strip span.up { border-color: color-mix(in srgb, #65c787 45%, var(--line)); }.effect-strip span.down { border-color: color-mix(in srgb, #e36b70 45%, var(--line)); }
.action-panel { display: grid; align-content: start; gap: 15px; }.action-panel > header i { width: 9px; height: 9px; border-radius: 50%; background: var(--line); }.action-panel > header i.live { background: var(--gold); box-shadow: 0 0 0 5px color-mix(in srgb, var(--gold) 10%, transparent); }.action-block,.wait-block,.final-panel { display: grid; gap: 12px; align-content: start; color: var(--muted); }.action-block > svg,.wait-block > svg,.final-panel > svg { color: var(--gold); }.action-block > strong,.wait-block > strong,.final-panel > strong { color: var(--text); font-size: 17px; }.action-block p,.wait-block p { margin: 0; font-size: 10px; line-height: 1.65; }.action-block > small,.reduce-row > small { color: var(--muted); font-size: 9px; font-weight: 900; }.action-block.danger > svg,.action-block.danger > strong { color: #e36b70; }.action-panel button,.action-panel select { min-height: 48px; border: 1px solid var(--line); border-radius: 11px; padding: 8px 10px; color: var(--text); background: var(--surface-inset); font: inherit; font-size: 10px; cursor: pointer; }.action-panel button.selected { border-color: var(--gold); color: var(--accent-contrast); background: var(--gold); }.action-panel button.primary { border-color: var(--gold); color: var(--accent-contrast); background: var(--gold); font-weight: 900; }.action-panel button:disabled { opacity: .4; cursor: not-allowed; }.amount-grid,.quote-grid,.button-grid,.commodity-buttons { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 7px; }.quote-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }.commodity-buttons { grid-template-columns: repeat(2, minmax(0, 1fr)); }.split-actions { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(108px, .5fr); gap: 8px; }.reduce-row { display: grid; grid-template-columns: 1fr; gap: 6px; border-top: 1px solid var(--line); padding-top: 9px; }.final-panel ol { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }.final-panel li { display: flex; justify-content: space-between; gap: 8px; border-bottom: 1px solid var(--line); padding: 7px 0; font-size: 9px; }.final-panel li b { color: var(--text); }
.ledger-scroll { min-width: 0; display: grid; grid-template-columns: repeat(4, minmax(185px, 1fr)); gap: 8px; padding-top: 10px; overflow-x: auto; }.ledger-scroll article { min-width: 185px; display: grid; gap: 8px; border: 1px solid var(--line); border-radius: 12px; padding: 9px; background: var(--surface-inset); }.ledger-scroll article.current { border-color: color-mix(in srgb, var(--gold) 60%, var(--line)); }.ledger-scroll article.bankrupt { opacity: .48; filter: grayscale(.8); }.ledger-scroll article > header { display: flex; justify-content: space-between; gap: 7px; }.ledger-scroll article > header b,.ledger-scroll article > header span { overflow: hidden; font-size: 8px; text-overflow: ellipsis; white-space: nowrap; }.ledger-scroll article > header span { color: var(--gold); }.ledger-scroll dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 5px; margin: 0; }.ledger-scroll dl div { display: flex; justify-content: space-between; gap: 5px; }.ledger-scroll dt { color: var(--muted); font-size: 7px; }.ledger-scroll dd { margin: 0; font-size: 8px; font-weight: 900; }.positions { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 3px; }.positions span { border-radius: 6px; padding: 4px 2px; color: var(--muted); background: color-mix(in srgb, var(--surface-elevated) 60%, transparent); font-size: 7px; text-align: center; }.positions span.long { color: #65c787; }.positions span.short { color: #e36b70; }
.portfolio-panel { min-width: 0; display: grid; grid-template-columns: minmax(230px, .34fr) minmax(0, 1.66fr); gap: 12px; }.cash-tray { display: grid; align-content: start; gap: 13px; }.hand-tray { display: grid; gap: 10px; }.hand-cards { min-width: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(132px, 1fr)); gap: 10px; }.hand-cards > button { min-width: 0; border: 2px solid transparent; border-radius: 12px; padding: 0; background: transparent; cursor: default; transition: transform .18s ease, border-color .18s ease, opacity .18s ease; }.hand-cards > button.playable { cursor: pointer; }.hand-cards > button.unavailable { opacity: .9; }.hand-cards > button.selected { transform: translateY(-8px); border-color: var(--gold); box-shadow: 0 10px 24px color-mix(in srgb, var(--gold) 15%, transparent); }.hand-tray > p { margin: 16px 0; color: var(--muted); font-size: 9px; text-align: center; }
.peek-panel > header { display: flex; align-items: center; gap: 9px; color: var(--gold); }.peek-panel header > span { display: grid; gap: 2px; }.peek-panel header b { font-size: 10px; }.peek-panel header small { color: var(--muted); font-size: 7px; }.peek-panel > div { width: min(100%, 330px); display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }.history-panel ol { max-height: 220px; display: grid; gap: 5px; margin: 9px 0 0; padding: 0; overflow-y: auto; list-style: none; }.history-panel li { display: grid; grid-template-columns: 27px minmax(0, 1fr); gap: 7px; border-left: 2px solid var(--line); padding: 5px 7px; color: var(--muted); font-size: 8px; }.history-panel li i { color: var(--gold); font-style: normal; font-weight: 900; }
.event-card-enter-active { animation: event-flip .48s ease both; }.event-card-leave-active { display: none; }.hand-card-enter-active { animation: deal-in .42s cubic-bezier(.2,.8,.2,1) both; }.hand-card-leave-active { transition: opacity .2s ease, transform .2s ease; }.hand-card-leave-to { opacity: 0; transform: translateY(-22px) rotate(4deg); }
@keyframes event-flip { from { opacity: 0; transform: perspective(600px) rotateY(90deg) translateX(-24px); } to { opacity: 1; transform: perspective(600px) rotateY(0); } } @keyframes deal-in { from { opacity: 0; transform: translate(-70px,-42px) rotate(-8deg) scale(.65); } to { opacity: 1; transform: none; } }
@media (hover: hover) { .commodity-grid article:hover > img { transform: scale(1.04); }.hand-cards > button.playable:hover { transform: translateY(-5px); }.action-panel button:hover:not(:disabled) { border-color: color-mix(in srgb, var(--gold) 65%, var(--line)); } }
@media (max-width: 1100px) { .center-grid { grid-template-columns: 1fr; }.event-table { order: 2; }.action-panel { order: 1; } }
@media (max-width: 1000px) { .commodity-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }.commodity-grid article { min-height: 140px; }.ledger-scroll { grid-template-columns: repeat(4, minmax(175px, 1fr)); }.portfolio-panel { grid-template-columns: 1fr; }.cash-tray { grid-template-columns: minmax(180px, .4fr) minmax(0, 1.6fr); align-items: start; }.cash-tray > header { border-bottom: 0; padding-bottom: 0; } }
@media (max-width: 650px) { .crazy-futures { gap: 11px; padding: 12px 9px; }.game-header { align-items: flex-start; }.game-header p { max-width: 230px; }.commodity-grid { gap: 7px; }.commodity-grid article { min-height: 126px; padding: 9px; }.commodity-grid article > header strong { font-size: 17px; }.commodity-grid dl { grid-template-columns: repeat(2, minmax(0, 1fr)); }.commodity-grid dl div:last-child { grid-column: 1 / -1; }.event-cards { grid-template-columns: repeat(3, minmax(96px, 1fr)); overflow-x: auto; }.event-cards > * { min-width: 96px; }.ledger-scroll { display: flex; }.ledger-scroll article { flex: 0 0 178px; }.hand-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }.cash-tray { grid-template-columns: 1fr; }.cash-tray > header { border-bottom: 1px solid var(--line); padding-bottom: 9px; }.amount-grid,.quote-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }.split-actions { grid-template-columns: 1fr; }.action-panel { padding: 14px; } }
@media (max-width: 380px) { .game-header p { display: none; }.round-meter strong { font-size: 32px; }.commodity-grid article { min-height: 118px; }.commodity-grid footer { display: none; }.hand-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); }.event-cards { grid-template-columns: repeat(2, minmax(104px, 1fr)); }.commodity-buttons { grid-template-columns: 1fr; } }
@media (prefers-reduced-motion: reduce) { .event-card-enter-active,.hand-card-enter-active { animation: none; }.hand-cards > button,.commodity-grid article > img { transition: none; } }
</style>
