import { flushPromises, mount } from '@vue/test-utils'
import type { ArcadeSnapshot } from '@game-hall/plugin-sdk'
import GameView from './GameView.vue'

const pluginActions = vi.hoisted(() => ({
  action: vi.fn(async () => true),
  rapidAction: vi.fn(async () => true),
  restart: vi.fn(async () => true),
  publishSpectatorFrame: vi.fn(() => true),
}))

vi.mock('@game-hall/plugin-sdk', async (importOriginal) => ({
  ...await importOriginal<typeof import('@game-hall/plugin-sdk')>(),
  usePluginGameActions: () => pluginActions,
}))

interface TestCard {
  id: string
  suit: 'spades' | 'hearts' | 'diamonds' | 'clubs'
  suitSymbol: string
  rank: number
  label: string
  color: 'red' | 'black'
  exposed: boolean
}

function card(id: string, rank: number, suit: TestCard['suit'] = 'spades', exposed = true): TestCard {
  const symbols = { spades: '♠', hearts: '♥', diamonds: '♦', clubs: '♣' }
  const labels: Record<number, string> = { 1: 'A', 11: 'J', 12: 'Q', 13: 'K' }
  return {
    id,
    suit,
    suitSymbol: symbols[suit],
    rank,
    label: labels[rank] ?? String(rank),
    color: ['hearts', 'diamonds'].includes(suit) ? 'red' : 'black',
    exposed,
  }
}

function snapshot(phase: 'playing' | 'finished' = 'playing', won = false): ArcadeSnapshot {
  const pyramid = Array<TestCard | null>(28).fill(null)
  pyramid[0] = card('covered-ace', 1, 'hearts', false)
  pyramid[21] = card('six', 6, 'clubs')
  pyramid[22] = card('seven', 7, 'hearts')
  return {
    revision: 1,
    phase,
    winReason: phase === 'finished'
      ? (won ? '清空 28 张金字塔牌，用时 1 分 05.2 秒，完成 17 次消除' : '牌库耗尽且没有可用组合')
      : null,
    actions: {
      canAct: phase === 'playing',
      canRestart: phase === 'finished',
    },
    game: {
      targetSum: 13,
      pyramid: phase === 'finished' && won ? Array(28).fill(null) : pyramid,
      pyramidCleared: phase === 'finished' && won ? 28 : 0,
      stockRemaining: phase === 'playing' ? 24 : 0,
      stockPass: 1,
      maxStockPasses: 1,
      wasteCount: 0,
      wasteTop: null,
      availableCardIds: phase === 'playing' ? ['six', 'seven'] : [],
      canDraw: phase === 'playing',
      removalMoves: phase === 'finished' && won ? 17 : 0,
      draws: phase === 'finished' ? 24 : 0,
      cardsRemoved: phase === 'finished' && won ? 34 : 0,
      elapsedMs: phase === 'finished' ? 65_200 : 1_200,
      lastRemovedIds: [],
      won,
      result: phase === 'finished' ? (won ? 'completed' : 'failed') : null,
    },
  } as unknown as ArcadeSnapshot
}

describe('pyramid solitaire plugin view', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders seven rows and distinguishes covered from exposed cards', () => {
    const wrapper = mount(GameView, {
      props: { snapshot: snapshot() },
    })

    expect(wrapper.findAll('.pyramid-row')).toHaveLength(7)
    expect(wrapper.get('[data-card-id="covered-ace"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-card-id="six"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('牌库只翻一轮')
    wrapper.unmount()
  })

  it('selects a valid pair and sends only the card ids to the server', async () => {
    const wrapper = mount(GameView, {
      props: { snapshot: snapshot() },
    })

    await wrapper.get('[data-card-id="six"]').trigger('click')
    expect(wrapper.get('[data-card-id="six"]').classes()).toContain('selected')
    await wrapper.get('[data-card-id="seven"]').trigger('click')
    await flushPromises()

    expect(pluginActions.action).toHaveBeenCalledWith(
      'remove',
      { cardIds: ['six', 'seven'] },
    )
    wrapper.unmount()
  })

  it('draws from the stock and removes a waste king by itself', async () => {
    const active = snapshot()
    active.game = {
      ...active.game,
      wasteTop: card('waste-king', 13, 'diamonds'),
      wasteCount: 1,
      availableCardIds: ['six', 'seven', 'waste-king'],
    }
    const wrapper = mount(GameView, {
      props: { snapshot: active },
    })

    await wrapper.get('.card-back').trigger('click')
    await flushPromises()
    expect(pluginActions.action).toHaveBeenCalledWith('draw')

    await wrapper.get('[data-card-id="waste-king"]').trigger('click')
    await flushPromises()
    expect(pluginActions.action).toHaveBeenCalledWith(
      'remove',
      { cardIds: ['waste-king'] },
    )
    wrapper.unmount()
  })

  it('offers a fullscreen table and uses the native fullscreen API', async () => {
    const wrapper = mount(GameView, {
      props: { snapshot: snapshot() },
    })
    const gameRoot = wrapper.get('.pyramid-game').element as HTMLElement
    const requestFullscreen = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(gameRoot, 'requestFullscreen', {
      configurable: true,
      value: requestFullscreen,
    })

    expect(wrapper.get('.fullscreen-button').text()).toContain('全屏牌桌')
    await wrapper.get('.fullscreen-button').trigger('click')
    await flushPromises()

    expect(requestFullscreen).toHaveBeenCalledOnce()

    const fullscreenDescriptor = Object.getOwnPropertyDescriptor(document, 'fullscreenElement')
    const exitDescriptor = Object.getOwnPropertyDescriptor(document, 'exitFullscreen')
    let fullscreenElement: Element | null = gameRoot
    const exitFullscreen = vi.fn(async () => {
      fullscreenElement = null
      document.dispatchEvent(new Event('fullscreenchange'))
    })
    Object.defineProperty(document, 'fullscreenElement', {
      configurable: true,
      get: () => fullscreenElement,
    })
    Object.defineProperty(document, 'exitFullscreen', {
      configurable: true,
      value: exitFullscreen,
    })
    document.dispatchEvent(new Event('fullscreenchange'))
    await flushPromises()

    expect(wrapper.get('.fullscreen-button').text()).toContain('退出全屏')
    await wrapper.get('.fullscreen-button').trigger('click')
    await flushPromises()
    expect(exitFullscreen).toHaveBeenCalledOnce()

    wrapper.unmount()
    if (fullscreenDescriptor) {
      Object.defineProperty(document, 'fullscreenElement', fullscreenDescriptor)
    } else {
      Reflect.deleteProperty(document, 'fullscreenElement')
    }
    if (exitDescriptor) {
      Object.defineProperty(document, 'exitFullscreen', exitDescriptor)
    } else {
      Reflect.deleteProperty(document, 'exitFullscreen')
    }
  })

  it('shows the frozen completion time and starts a rematch', async () => {
    const wrapper = mount(GameView, {
      props: { snapshot: snapshot('finished', true) },
    })

    expect(wrapper.get('.pyramid-result').text()).toContain('金字塔已清空')
    expect(wrapper.get('.pyramid-result').text()).toContain('1:05.2')
    await wrapper.get('.pyramid-result .primary-button').trigger('click')
    await flushPromises()

    expect(pluginActions.restart).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
})
