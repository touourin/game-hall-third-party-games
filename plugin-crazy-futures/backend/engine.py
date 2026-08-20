from __future__ import annotations

import random
from fractions import Fraction
from statistics import median
from typing import Any, Iterable

from backend.app.games.plugin_api import ArcadePlayer, ArcadeRoom, GameRuleError

from .catalog import (
    COMMODITIES,
    COMMODITY_COLORS,
    COMMODITY_LABELS,
    PRICE_LADDER,
    PRICE_ZONES,
    CardDefinition,
    card_for_instance,
    new_personal_deck,
    new_public_deck,
)
from .state import (
    ActiveEffect,
    AuctionState,
    CrazyFuturesState,
    GameEvent,
    LoanRecord,
    PendingChoice,
    PlayerLedger,
)


MAX_ROUNDS = 8
MAX_LOAN = 100
LOAN_STEP = 10
HAND_LIMIT = 6
MAX_COMMODITY_POSITION = 4
MAX_TOTAL_POSITION = 8
PRICE_LIMIT_STEPS = 3
EVENT_HISTORY_LIMIT = 80


def _number(value: Fraction | int) -> int | float:
    fraction = Fraction(value)
    if fraction.denominator == 1:
        return fraction.numerator
    return round(float(fraction), 2)


def _direction(quantity: int) -> int:
    return 1 if quantity > 0 else -1


class CrazyFuturesEngine:
    key = "plugin-crazy-futures"
    name = "疯狂期货"
    min_players = 4
    max_players = 8
    action_phases = {"playing"}

    def __init__(self, rng: random.Random | random.SystemRandom | None = None) -> None:
        self.rng = rng or random.SystemRandom()

    def initial_state(self) -> CrazyFuturesState:
        return CrazyFuturesState()

    def start(self, room: ArcadeRoom) -> None:
        players = [player for player in room.players if not player.left_room]
        if not self.min_players <= len(players) <= self.max_players:
            raise GameRuleError("疯狂期货需要 4–8 位玩家")

        personal_deck = new_personal_deck()
        public_deck = new_public_deck()
        self.rng.shuffle(personal_deck)
        self.rng.shuffle(public_deck)
        state = CrazyFuturesState(
            round_number=1,
            turn_order=[player.id for player in players],
            starter_index=0,
            ledgers={player.id: PlayerLedger() for player in players},
            personal_deck=personal_deck,
            public_deck=public_deck,
        )
        room.state = state
        room.phase = "playing"
        self._emit(
            state,
            "game_start",
            f"{len(players)} 位交易员进入北辰商品交易所",
            {"playerCount": len(players)},
        )
        self._begin_round(room, state)

    def act(
        self,
        room: ArcadeRoom,
        player: ArcadePlayer,
        action: str,
        payload: dict[str, Any],
    ) -> None:
        if room.phase != "playing":
            raise GameRuleError("当前对局不能继续操作")
        state: CrazyFuturesState = room.state
        if action == "resign":
            self.manual_forfeit(room, player)
            return
        ledger = state.ledgers.get(player.id)
        if ledger is None or ledger.bankrupt or ledger.forfeited:
            raise GameRuleError("你已不在本局的行动序列中")

        if action == "borrow":
            self._borrow(room, state, player, payload)
        elif action == "start_auction":
            self._start_auction(room, state, player, payload)
        elif action == "bid":
            self._bid(room, state, player, payload)
        elif action == "pass_bid":
            self._pass_bid(room, state, player)
        elif action == "play_card":
            self._play_card(room, state, player, payload)
        elif action == "reduce_only":
            self._reduce_only(room, state, player, payload)
        elif action == "pass_card":
            self._pass_card(room, state, player)
        elif action == "discard_cards":
            self._discard_cards(room, state, player, payload)
        elif action == "choose_liquidation":
            self._choose_liquidation(room, state, player, payload)
        else:
            raise GameRuleError("不支持这个疯狂期货操作")

    def _begin_round(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        active = self._ordered_active_ids(state)
        if not active:
            self._finish_all_bankrupt(room, state)
            return
        for market in state.markets.values():
            if state.round_number > 1:
                market.open_index = market.close_index
                market.current_index = market.close_index
            market.low_limit_index = max(0, market.open_index - PRICE_LIMIT_STEPS)
            market.high_limit_index = min(
                len(PRICE_LADDER) - 1,
                market.open_index + PRICE_LIMIT_STEPS,
            )
            market.valid_trade_indices.clear()
            market.seal = None
        state.stage = "loan"
        state.phase_order = active
        state.phase_cursor = 0
        state.current_player_id = active[0]
        state.pending_choice = None
        state.auction = None
        for ledger in state.ledgers.values():
            ledger.peek_cards.clear()
        self._emit(
            state,
            "round_start",
            f"第 {state.round_number} 个月开始",
            {"round": state.round_number, "starterId": active[0]},
        )

    def _borrow(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
        payload: dict[str, Any],
    ) -> None:
        self._require_turn(state, player.id, "loan", "还没有轮到你决定借贷")
        amount = payload.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise GameRuleError("借贷金额必须是整数")
        ledger = state.ledgers[player.id]
        remaining = MAX_LOAN - sum(record.principal for record in ledger.loans)
        if amount < 0 or amount > remaining or amount % LOAN_STEP:
            raise GameRuleError("借贷必须是 0–100 万范围内的 10 万整数倍")
        if amount:
            ledger.loans.append(LoanRecord(amount, state.round_number))
            ledger.cash += amount
        self._emit(
            state,
            "loan",
            f"{player.name}{'借入 ' + str(amount) + ' 万金币' if amount else '本月不新增贷款'}",
            {"playerId": player.id, "amount": amount, "cashDelta": amount},
        )
        state.phase_cursor += 1
        self._advance_loan_phase(room, state)

    def _advance_loan_phase(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        while state.phase_cursor < len(state.phase_order):
            candidate = state.phase_order[state.phase_cursor]
            if self._is_active(state, candidate):
                state.current_player_id = candidate
                return
            state.phase_cursor += 1
        self._draw_and_reveal(room, state)

    def _draw_and_reveal(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        active = self._ordered_active_ids(state)
        if not active:
            self._finish_all_bankrupt(room, state)
            return
        state.stage = "draw"
        state.current_player_id = None
        for player_id in active:
            drawn = self._draw_personal_cards(state, player_id, 2)
            player = room.player(player_id)
            self._emit(
                state,
                "deal",
                f"{player.name} 抽取 {len(drawn)} 张个人牌",
                {"playerId": player_id, "count": len(drawn)},
            )

        state.stage = "public_event"
        for _ in range(2):
            if not state.public_deck:
                break
            instance_id = state.public_deck.pop()
            self._resolve_public_event(room, state, instance_id)

        state.stage = "auction"
        active = self._ordered_active_ids(state)
        state.initiation_order = active + list(reversed(active))
        state.initiation_cursor = 0
        state.auction = None
        self._advance_auction_start(room, state)

    def _draw_personal_cards(
        self,
        state: CrazyFuturesState,
        player_id: str,
        count: int,
    ) -> list[str]:
        drawn: list[str] = []
        for _ in range(count):
            if not state.personal_deck and state.personal_discard:
                state.personal_deck = list(state.personal_discard)
                state.personal_discard.clear()
                self.rng.shuffle(state.personal_deck)
            if not state.personal_deck:
                break
            instance_id = state.personal_deck.pop()
            state.ledgers[player_id].hand.append(instance_id)
            drawn.append(instance_id)
        return drawn

    def _resolve_public_event(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        instance_id: str,
    ) -> None:
        card = card_for_instance(instance_id)
        state.revealed_public.append(instance_id)
        effect = card.effect
        effect_type = effect["type"]
        if effect_type == "spot_move":
            self._apply_spot_moves(state, effect["moves"], card.name)
            state.public_discard.append(instance_id)
        elif effect_type == "persistent_spot":
            self._add_active_effect(state, instance_id, card, owner_id=None)
        elif effect_type == "remove_persistent":
            self._resolve_remove_effect(state, effect, target_effect_id=None)
            state.public_discard.append(instance_id)
        else:
            raise GameRuleError(f"公共事件牌效果不受支持：{card.name}")
        self._emit(
            state,
            "public_event",
            f"公共事件：{card.name}",
            {"card": card.public_dict(instance_id)},
        )

    def _legal_start_options(
        self,
        state: CrazyFuturesState,
        player_id: str,
    ) -> list[dict[str, Any]]:
        ledger = state.ledgers[player_id]
        entries: list[tuple[dict[str, Any], bool]] = []
        for commodity in COMMODITIES:
            market = state.markets[commodity]
            for side, direction, indices in (
                (
                    "buy",
                    1,
                    range(market.current_index, market.high_limit_index + 1),
                ),
                (
                    "sell",
                    -1,
                    range(market.low_limit_index, market.current_index + 1),
                ),
            ):
                legal_indices = [
                    index
                    for index in indices
                    if self._trade_would_be_legal(
                        ledger,
                        commodity,
                        direction,
                        PRICE_LADDER[index],
                    )
                ]
                if not legal_indices:
                    continue
                position = ledger.positions[commodity]
                risk_increasing = position.quantity * direction >= 0
                entries.append(
                    (
                        {
                            "commodity": commodity,
                            "side": side,
                            "quoteIndices": legal_indices,
                        },
                        risk_increasing,
                    )
                )
        if any(risk_increasing for _, risk_increasing in entries):
            return [entry for entry, _ in entries]
        return [entry for entry, _ in entries]

    def _start_auction(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
        payload: dict[str, Any],
    ) -> None:
        self._require_turn(state, player.id, "auction", "还没有轮到你发起竞价")
        if state.auction is not None:
            raise GameRuleError("当前竞价已经开始")
        commodity = payload.get("commodity")
        side = payload.get("side")
        quote_index = payload.get("quoteIndex")
        if commodity not in COMMODITIES or side not in {"buy", "sell"}:
            raise GameRuleError("请选择有效的商品和买卖方向")
        if isinstance(quote_index, bool) or not isinstance(quote_index, int):
            raise GameRuleError("报价必须来自价格阶梯")
        option = next(
            (
                item
                for item in self._legal_start_options(state, player.id)
                if item["commodity"] == commodity and item["side"] == side
            ),
            None,
        )
        if option is None or quote_index not in option["quoteIndices"]:
            raise GameRuleError("该报价不满足涨跌停、保证金或头寸限制")
        participants = self._ordered_active_ids(state)
        auction = AuctionState(
            initiator_id=player.id,
            commodity=commodity,
            side=side,
            quote_index=quote_index,
            leader_id=player.id,
            participants=participants,
        )
        state.auction = auction
        auction.cursor_player_id = self._next_auction_actor(state, player.id)
        state.current_player_id = auction.cursor_player_id
        self._emit(
            state,
            "auction_open",
            f"{player.name} 发起{COMMODITY_LABELS[commodity]}{('买入' if side == 'buy' else '卖出')}竞价",
            {
                "playerId": player.id,
                "commodity": commodity,
                "side": side,
                "quoteIndex": quote_index,
                "price": PRICE_LADDER[quote_index],
            },
        )
        if auction.cursor_player_id is None:
            self._settle_auction(room, state)

    def _bid(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
        payload: dict[str, Any],
    ) -> None:
        auction = state.auction
        if (
            state.stage != "auction"
            or auction is None
            or auction.cursor_player_id != player.id
        ):
            raise GameRuleError("当前不能参与报价")
        quote_index = payload.get("quoteIndex")
        if isinstance(quote_index, bool) or not isinstance(quote_index, int):
            raise GameRuleError("报价必须来自价格阶梯")
        market = state.markets[auction.commodity]
        direction = 1 if auction.side == "buy" else -1
        improves = (
            quote_index > auction.quote_index
            if auction.side == "buy"
            else quote_index < auction.quote_index
        )
        if not improves or not market.low_limit_index <= quote_index <= market.high_limit_index:
            raise GameRuleError("新报价必须至少改善一格且不能越过涨跌停")
        if not self._trade_would_be_legal(
            state.ledgers[player.id],
            auction.commodity,
            direction,
            PRICE_LADDER[quote_index],
        ):
            raise GameRuleError("你的现金、保证金或头寸不足以执行该报价")
        auction.quote_index = quote_index
        auction.leader_id = player.id
        self._emit(
            state,
            "bid",
            f"{player.name} 将报价改善到 {PRICE_LADDER[quote_index]} 万",
            {
                "playerId": player.id,
                "commodity": auction.commodity,
                "side": auction.side,
                "quoteIndex": quote_index,
                "price": PRICE_LADDER[quote_index],
            },
        )
        auction.cursor_player_id = self._next_auction_actor(state, player.id)
        state.current_player_id = auction.cursor_player_id
        if auction.cursor_player_id is None:
            self._settle_auction(room, state)

    def _pass_bid(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
    ) -> None:
        if state.stage != "auction":
            raise GameRuleError("当前不在竞价阶段")
        auction = state.auction
        if auction is None:
            if state.current_player_id != player.id:
                raise GameRuleError("还没有轮到你发起竞价")
            if self._legal_start_options(state, player.id):
                raise GameRuleError("你仍有合法订单，必须发起一次竞价")
            self._emit(
                state,
                "auction_skip",
                f"{player.name} 没有任何合法订单，本次发起跳过",
                {"playerId": player.id},
            )
            state.initiation_cursor += 1
            self._advance_auction_start(room, state)
            return
        if auction.cursor_player_id != player.id:
            raise GameRuleError("当前不需要你表态")
        if player.id not in auction.passed_ids:
            auction.passed_ids.append(player.id)
        self._emit(
            state,
            "bid_pass",
            f"{player.name} 放弃本次竞价",
            {"playerId": player.id},
        )
        auction.cursor_player_id = self._next_auction_actor(state, player.id)
        state.current_player_id = auction.cursor_player_id
        if auction.cursor_player_id is None:
            self._settle_auction(room, state)

    def _next_auction_actor(
        self,
        state: CrazyFuturesState,
        after_player_id: str,
    ) -> str | None:
        auction = state.auction
        if auction is None:
            return None
        available = [
            player_id
            for player_id in auction.participants
            if player_id != auction.leader_id
            and player_id not in auction.passed_ids
            and self._is_active(state, player_id)
        ]
        if not available:
            return None
        start = state.turn_order.index(after_player_id)
        for offset in range(1, len(state.turn_order) + 1):
            candidate = state.turn_order[(start + offset) % len(state.turn_order)]
            if candidate in available:
                return candidate
        return None

    def _settle_auction(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        auction = state.auction
        if auction is None:
            return
        direction = 1 if auction.side == "buy" else -1
        price = PRICE_LADDER[auction.quote_index]
        ledger = state.ledgers[auction.leader_id]
        if not self._trade_would_be_legal(
            ledger,
            auction.commodity,
            direction,
            price,
        ):
            raise GameRuleError("领先报价在成交前已失去履约能力")
        cash_before = ledger.cash
        self._apply_trade(ledger, auction.commodity, direction, price)
        market = state.markets[auction.commodity]
        previous_index = market.current_index
        market.current_index = auction.quote_index
        market.valid_trade_indices.append(auction.quote_index)
        leader = room.player(auction.leader_id)
        self._emit(
            state,
            "auction_trade",
            f"{leader.name} 以 {price} 万成交 1 份{COMMODITY_LABELS[auction.commodity]}",
            {
                "playerId": leader.id,
                "commodity": auction.commodity,
                "side": auction.side,
                "fromIndex": previous_index,
                "toIndex": auction.quote_index,
                "price": price,
                "cashDelta": _number(ledger.cash - cash_before),
            },
        )
        state.auction = None
        state.initiation_cursor += 1
        self._advance_auction_start(room, state)

    def _advance_auction_start(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        while state.initiation_cursor < len(state.initiation_order):
            candidate = state.initiation_order[state.initiation_cursor]
            if self._is_active(state, candidate):
                state.current_player_id = candidate
                state.auction = None
                return
            state.initiation_cursor += 1
        self._begin_card_stage(room, state)

    def _begin_card_stage(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        active = self._ordered_active_ids(state)
        if not active:
            self._finish_all_bankrupt(room, state)
            return
        state.stage = "card"
        state.card_pass_count = 0
        state.pending_choice = None
        state.current_player_id = active[0]
        self._emit(
            state,
            "card_phase",
            "竞价结束，进入顺时针出牌阶段",
            {"starterId": active[0]},
        )

    def _play_card(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
        payload: dict[str, Any],
    ) -> None:
        self._require_card_turn(state, player.id)
        instance_id = payload.get("instanceId")
        if not isinstance(instance_id, str):
            raise GameRuleError("请选择一张手牌")
        ledger = state.ledgers[player.id]
        if instance_id not in ledger.hand:
            raise GameRuleError("选择的牌不在你的手中")
        option = self._card_play_option(state, player.id, instance_id)
        if option is None:
            raise GameRuleError("这张牌当前没有合法目标")
        card = card_for_instance(instance_id)
        effect = card.effect
        effect_type = effect["type"]
        commodity = payload.get("commodity")
        effect_id = payload.get("effectId")
        ledger.hand.remove(instance_id)

        if effect_type == "spot_move":
            moves = effect.get("moves")
            if effect.get("choose"):
                targets = option.get("commodities", [])
                if commodity not in targets:
                    ledger.hand.append(instance_id)
                    raise GameRuleError("请选择符合卡面条件的商品")
                moves = [{"commodity": commodity, "delta": effect["delta"]}]
            self._apply_spot_moves(state, moves, card.name)
            state.personal_discard.append(instance_id)
        elif effect_type == "persistent_spot":
            self._add_active_effect(state, instance_id, card, player.id)
        elif effect_type == "seal":
            if commodity not in option.get("commodities", []):
                ledger.hand.append(instance_id)
                raise GameRuleError("请选择已经到达对应涨跌停的商品")
            state.markets[commodity].seal = effect["side"]
            state.personal_discard.append(instance_id)
        elif effect_type == "peek_public":
            ledger.peek_cards = list(reversed(state.public_deck[-effect["count"] :]))
            state.personal_discard.append(instance_id)
        elif effect_type == "loan_discount":
            loan = self._current_round_loan(state, player.id)
            if loan is None or loan.rate_percent != 10:
                ledger.hand.append(instance_id)
                raise GameRuleError("本月没有可改为优惠利率的新贷款")
            loan.rate_percent = effect["ratePercent"]
            state.personal_discard.append(instance_id)
        elif effect_type == "margin_buffer":
            ledger.margin_buffer += effect["amount"]
            state.personal_discard.append(instance_id)
        elif effect_type == "remove_persistent":
            targets = option.get("effectIds", [])
            if effect_id not in targets:
                ledger.hand.append(instance_id)
                raise GameRuleError("请选择一张仍在场的持续个人牌")
            self._resolve_remove_effect(state, effect, effect_id)
            state.personal_discard.append(instance_id)
        elif effect_type == "information_swap":
            drawn = self._draw_personal_cards(state, player.id, effect["draw"])
            state.pending_choice = PendingChoice(
                "discard",
                player.id,
                count=effect["discard"],
                reason="information_swap",
                resolving_card_id=instance_id,
            )
            self._emit(
                state,
                "draw_bonus",
                f"{player.name} 额外抽取 {len(drawn)} 张牌，需弃置 {effect['discard']} 张",
                {"playerId": player.id, "count": len(drawn)},
            )
        else:
            ledger.hand.append(instance_id)
            raise GameRuleError("该牌效果尚未实现")

        self._emit(
            state,
            "card_play",
            f"{player.name} 打出《{card.name}》",
            {
                "playerId": player.id,
                "card": card.public_dict(instance_id),
                "commodity": commodity,
            },
        )
        state.card_pass_count = 0
        if state.pending_choice is None:
            self._advance_card_turn(room, state, player.id)

    def _reduce_only(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
        payload: dict[str, Any],
    ) -> None:
        self._require_card_turn(state, player.id)
        commodity = payload.get("commodity")
        if commodity not in COMMODITIES:
            raise GameRuleError("请选择有效商品")
        market = state.markets[commodity]
        position = state.ledgers[player.id].positions[commodity]
        if market.seal not in {"up", "down"} or position.quantity == 0:
            raise GameRuleError("该商品当前不能提交只减仓订单")
        direction = -_direction(position.quantity)
        quote_index = (
            market.high_limit_index if market.seal == "up" else market.low_limit_index
        )
        price = PRICE_LADDER[quote_index]
        ledger = state.ledgers[player.id]
        cash_before = ledger.cash
        self._apply_trade(ledger, commodity, direction, price)
        self._emit(
            state,
            "reduce_only",
            f"{player.name} 以封板价只减仓 1 份{COMMODITY_LABELS[commodity]}",
            {
                "playerId": player.id,
                "commodity": commodity,
                "quoteIndex": quote_index,
                "price": price,
                "cashDelta": _number(ledger.cash - cash_before),
            },
        )
        if ledger.cash < 0 and not self._has_positions(ledger):
            self._mark_bankrupt(room, state, player.id)
        state.card_pass_count = 0
        self._advance_card_turn(room, state, player.id)

    def _pass_card(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
    ) -> None:
        self._require_card_turn(state, player.id)
        state.card_pass_count += 1
        self._emit(
            state,
            "card_pass",
            f"{player.name} 本次不行动",
            {"playerId": player.id},
        )
        active_count = len(self._ordered_active_ids(state))
        if active_count == 0:
            self._finish_all_bankrupt(room, state)
        elif state.card_pass_count >= active_count:
            self._begin_settlement(room, state)
        else:
            self._advance_card_turn(room, state, player.id)

    def _advance_card_turn(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        after_player_id: str,
    ) -> None:
        next_player = self._next_active_id(state, after_player_id)
        if next_player is None:
            self._finish_all_bankrupt(room, state)
            return
        state.current_player_id = next_player

    def _discard_cards(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
        payload: dict[str, Any],
    ) -> None:
        pending = state.pending_choice
        if pending is None or pending.kind != "discard" or pending.player_id != player.id:
            raise GameRuleError("当前不需要你弃牌")
        instance_ids = payload.get("instanceIds")
        if (
            not isinstance(instance_ids, list)
            or len(instance_ids) != pending.count
            or not all(isinstance(instance_id, str) for instance_id in instance_ids)
            or len(set(instance_ids)) != len(instance_ids)
        ):
            raise GameRuleError(f"必须选择 {pending.count} 张不同的牌弃置")
        ledger = state.ledgers[player.id]
        if any(instance_id not in ledger.hand for instance_id in instance_ids):
            raise GameRuleError("弃置列表中包含不属于你的牌")
        selected = set(instance_ids)
        ledger.hand = [card for card in ledger.hand if card not in selected]
        state.personal_discard.extend(instance_ids)
        resolving_card = pending.resolving_card_id
        if resolving_card:
            state.personal_discard.append(resolving_card)
        reason = pending.reason
        state.pending_choice = None
        self._emit(
            state,
            "discard",
            f"{player.name} 弃置 {len(instance_ids)} 张牌",
            {"playerId": player.id, "count": len(instance_ids), "reason": reason},
        )
        if reason == "information_swap":
            self._advance_card_turn(room, state, player.id)
        else:
            self._advance_discard_queue(room, state)

    def _begin_settlement(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        state.stage = "settlement"
        state.current_player_id = None
        state.pending_choice = None
        self._trigger_persistent_effects(state)

        for market in state.markets.values():
            if len(market.valid_trade_indices) >= 3:
                market.close_index = int(median(market.valid_trade_indices[-3:]))
            elif market.valid_trade_indices:
                market.close_index = market.valid_trade_indices[-1]
            else:
                market.close_index = market.open_index
            self._emit(
                state,
                "market_close",
                f"{COMMODITY_LABELS[market.commodity]}收盘于 {PRICE_LADDER[market.close_index]} 万",
                {
                    "commodity": market.commodity,
                    "closeIndex": market.close_index,
                    "price": PRICE_LADDER[market.close_index],
                    "validTradeCount": len(market.valid_trade_indices),
                },
            )

        liquidation_queue: list[str] = []
        for player_id in self._ordered_active_ids(state):
            ledger = state.ledgers[player_id]
            cash_before = ledger.cash
            old_margin = sum(
                (position.margin for position in ledger.positions.values()),
                Fraction(0),
            )
            required_margin = Fraction(0)
            for commodity, position in ledger.positions.items():
                if position.quantity:
                    close_price = PRICE_LADDER[state.markets[commodity].close_index]
                    pnl = abs(position.quantity) * (
                        Fraction(close_price) - position.basis
                    ) * _direction(position.quantity)
                    ledger.cash += pnl
                    position.basis = Fraction(close_price)
                    position.margin = Fraction(abs(position.quantity) * close_price, 2)
                    required_margin += position.margin
                else:
                    position.basis = Fraction(0)
                    position.margin = Fraction(0)
            margin_change = required_margin - old_margin
            discount = Fraction(0)
            if margin_change > 0 and ledger.margin_buffer:
                discount = min(Fraction(ledger.margin_buffer), margin_change)
            ledger.cash -= margin_change - discount
            ledger.margin_buffer = 0
            self._emit(
                state,
                "player_settlement",
                f"{room.player(player_id).name} 完成本月盯市与保证金重算",
                {
                    "playerId": player_id,
                    "cashDelta": _number(ledger.cash - cash_before),
                    "margin": _number(required_margin),
                    "bufferUsed": _number(discount),
                },
            )
            if ledger.cash < 0:
                if self._has_positions(ledger):
                    liquidation_queue.append(player_id)
                else:
                    self._mark_bankrupt(room, state, player_id)

        state.settlement_queue = liquidation_queue
        if liquidation_queue:
            state.stage = "liquidation"
            player_id = liquidation_queue[0]
            state.current_player_id = player_id
            state.pending_choice = PendingChoice("liquidation", player_id, count=1)
        else:
            self._begin_discard_phase(room, state)

    def _trigger_persistent_effects(self, state: CrazyFuturesState) -> None:
        expired: list[ActiveEffect] = []
        for effect in sorted(state.active_effects, key=lambda item: item.sequence):
            self._apply_spot_moves(state, effect.moves, effect.card_name)
            effect.remaining_triggers -= 1
            self._emit(
                state,
                "persistent_trigger",
                f"持续效果《{effect.card_name}》触发",
                {
                    "effectId": effect.id,
                    "cardId": effect.card_id,
                    "remainingTriggers": effect.remaining_triggers,
                },
            )
            if effect.remaining_triggers <= 0:
                expired.append(effect)
        for effect in expired:
            self._remove_active_effect(state, effect, "持续时间结束")

    def _choose_liquidation(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player: ArcadePlayer,
        payload: dict[str, Any],
    ) -> None:
        pending = state.pending_choice
        if (
            state.stage != "liquidation"
            or pending is None
            or pending.kind != "liquidation"
            or pending.player_id != player.id
        ):
            raise GameRuleError("当前不需要你选择强制平仓")
        commodity = payload.get("commodity")
        if commodity not in COMMODITIES:
            raise GameRuleError("请选择有效商品")
        ledger = state.ledgers[player.id]
        position = ledger.positions[commodity]
        if position.quantity == 0:
            raise GameRuleError("该商品没有可以关闭的头寸")
        price = PRICE_LADDER[state.markets[commodity].close_index]
        cash_before = ledger.cash
        self._apply_trade(ledger, commodity, -_direction(position.quantity), price)
        ledger.forced_liquidations += 1
        self._emit(
            state,
            "forced_liquidation",
            f"交易所按收盘价强平 {player.name} 的 1 份{COMMODITY_LABELS[commodity]}",
            {
                "playerId": player.id,
                "commodity": commodity,
                "price": price,
                "cashDelta": _number(ledger.cash - cash_before),
            },
        )
        if ledger.cash < 0 and self._has_positions(ledger):
            return
        if ledger.cash < 0:
            self._mark_bankrupt(room, state, player.id)
        self._advance_liquidation_queue(room, state)

    def _advance_liquidation_queue(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
    ) -> None:
        if state.settlement_queue:
            state.settlement_queue.pop(0)
        while state.settlement_queue:
            player_id = state.settlement_queue[0]
            ledger = state.ledgers[player_id]
            if not self._is_active(state, player_id) or ledger.cash >= 0:
                state.settlement_queue.pop(0)
                continue
            if not self._has_positions(ledger):
                self._mark_bankrupt(room, state, player_id)
                state.settlement_queue.pop(0)
                continue
            state.current_player_id = player_id
            state.pending_choice = PendingChoice("liquidation", player_id, count=1)
            return
        state.pending_choice = None
        self._begin_discard_phase(room, state)

    def _begin_discard_phase(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        state.discard_queue = [
            player_id
            for player_id in self._ordered_active_ids(state)
            if len(state.ledgers[player_id].hand) > HAND_LIMIT
        ]
        if state.discard_queue:
            state.stage = "discard"
            player_id = state.discard_queue[0]
            count = len(state.ledgers[player_id].hand) - HAND_LIMIT
            state.current_player_id = player_id
            state.pending_choice = PendingChoice(
                "discard",
                player_id,
                count=count,
                reason="hand_limit",
            )
            return
        self._complete_round(room, state)

    def _advance_discard_queue(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        if state.discard_queue:
            state.discard_queue.pop(0)
        while state.discard_queue:
            player_id = state.discard_queue[0]
            if not self._is_active(state, player_id):
                state.discard_queue.pop(0)
                continue
            count = len(state.ledgers[player_id].hand) - HAND_LIMIT
            if count <= 0:
                state.discard_queue.pop(0)
                continue
            state.current_player_id = player_id
            state.pending_choice = PendingChoice(
                "discard",
                player_id,
                count=count,
                reason="hand_limit",
            )
            return
        state.pending_choice = None
        self._complete_round(room, state)

    def _complete_round(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        for market in state.markets.values():
            market.seal = None
        self._emit(
            state,
            "round_end",
            f"第 {state.round_number} 个月结算完成",
            {"round": state.round_number},
        )
        if not self._ordered_active_ids(state):
            self._finish_all_bankrupt(room, state)
            return
        if state.round_number >= MAX_ROUNDS:
            self._finalize_game(room, state)
            return
        state.round_number += 1
        state.starter_index = (state.starter_index + 1) % len(state.turn_order)
        self._begin_round(room, state)

    def _finalize_game(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        state.stage = "final"
        state.current_player_id = None
        for player_id in state.turn_order:
            ledger = state.ledgers[player_id]
            if not ledger.bankrupt:
                for commodity, position in ledger.positions.items():
                    if not position.quantity:
                        continue
                    spot_price = PRICE_LADDER[state.markets[commodity].spot_index]
                    pnl = abs(position.quantity) * (
                        Fraction(spot_price) - position.basis
                    ) * _direction(position.quantity)
                    ledger.cash += pnl + position.margin
                    position.quantity = 0
                    position.basis = Fraction(0)
                    position.margin = Fraction(0)
                loan_total = sum(
                    Fraction(record.principal)
                    + Fraction(
                        record.principal
                        * record.rate_percent
                        * (MAX_ROUNDS - record.borrowed_round + 1),
                        100,
                    )
                    for record in ledger.loans
                )
                ledger.cash -= loan_total
                if ledger.cash < 0:
                    ledger.exchange_debt += -ledger.cash
                    ledger.cash = Fraction(0)
                    ledger.bankrupt = True
            ledger.final_score = ledger.cash - ledger.exchange_debt

        state.rankings = sorted(
            state.turn_order,
            key=lambda player_id: (
                state.ledgers[player_id].bankrupt,
                -float(state.ledgers[player_id].final_score or 0),
                self._loan_principal(state.ledgers[player_id]),
                state.ledgers[player_id].forced_liquidations,
                state.turn_order.index(player_id),
            ),
        )
        eligible = [
            player_id
            for player_id in state.turn_order
            if not state.ledgers[player_id].bankrupt
            and not state.ledgers[player_id].forfeited
        ]
        if not eligible:
            self._emit(state, "game_end", "所有交易员均已破产", {})
            room.finish("bankrupt", [], "所有交易员均已破产，本局无人获胜")
            return
        best = max(
            eligible,
            key=lambda player_id: self._winner_key(state.ledgers[player_id]),
        )
        best_key = self._winner_key(state.ledgers[best])
        winners = [
            player_id
            for player_id in eligible
            if self._winner_key(state.ledgers[player_id]) == best_key
        ]
        winner_names = "、".join(room.player(player_id).name for player_id in winners)
        winning_score = _number(state.ledgers[winners[0]].final_score or 0)
        self._emit(
            state,
            "game_end",
            f"{winner_names} 以 {winning_score} 万金币净资产获胜",
            {"winnerIds": winners, "score": winning_score},
        )
        room.finish(
            "wealth",
            winners,
            f"{winner_names} 在第 8 个月终局回归后拥有最高净资产",
        )

    def manual_forfeit(self, room: ArcadeRoom, player: ArcadePlayer) -> bool:
        if room.phase != "playing":
            return False
        state: CrazyFuturesState = room.state
        ledger = state.ledgers.get(player.id)
        if ledger is None or ledger.bankrupt or ledger.forfeited:
            return False
        for commodity, position in ledger.positions.items():
            while position.quantity:
                market = state.markets[commodity]
                price = PRICE_LADDER[market.current_index]
                self._apply_trade(
                    ledger,
                    commodity,
                    -_direction(position.quantity),
                    price,
                )
        if ledger.cash < 0:
            ledger.exchange_debt += -ledger.cash
            ledger.cash = Fraction(0)
        state.personal_discard.extend(ledger.hand)
        ledger.hand.clear()
        ledger.bankrupt = True
        ledger.forfeited = True
        self._emit(
            state,
            "resign",
            f"{player.name} 认输退出本局",
            {"playerId": player.id},
        )

        pending = state.pending_choice
        if pending is not None and pending.player_id == player.id:
            if pending.resolving_card_id:
                state.personal_discard.append(pending.resolving_card_id)
            state.pending_choice = None
        if state.auction is not None and player.id in state.auction.participants:
            self._emit(
                state,
                "auction_cancel",
                "一名竞价参与者退出，当前竞价取消",
                {"playerId": player.id},
            )
            state.auction = None
            state.initiation_cursor += 1
            self._advance_auction_start(room, state)
        elif state.stage == "loan" and state.current_player_id == player.id:
            state.phase_cursor += 1
            self._advance_loan_phase(room, state)
        elif state.stage == "auction" and state.current_player_id == player.id:
            state.initiation_cursor += 1
            self._advance_auction_start(room, state)
        elif state.stage == "card" and state.current_player_id == player.id:
            state.card_pass_count = min(
                state.card_pass_count,
                max(0, len(self._ordered_active_ids(state)) - 1),
            )
            next_player = self._next_active_id(state, player.id)
            if next_player is None:
                self._finish_all_bankrupt(room, state)
            else:
                state.current_player_id = next_player
        elif state.stage == "liquidation" and state.current_player_id == player.id:
            self._advance_liquidation_queue(room, state)
        elif state.stage == "discard" and state.current_player_id == player.id:
            self._advance_discard_queue(room, state)
        if not self._ordered_active_ids(state) and room.phase == "playing":
            self._finish_all_bankrupt(room, state)
        return True

    def disconnect_timeout(self, room: ArcadeRoom, player: ArcadePlayer) -> bool:
        return self.manual_forfeit(room, player)

    def request_voter_ids(self, room: ArcadeRoom, kind: str) -> set[str]:
        if kind != "end_table":
            return {player.id for player in room.players if not player.left_room}
        state: CrazyFuturesState = room.state
        return set(self._ordered_active_ids(state))

    def view(self, room: ArcadeRoom, viewer: ArcadePlayer) -> dict[str, Any]:
        return self._view(room, viewer.id)

    def _view(self, room: ArcadeRoom, viewer_id: str | None) -> dict[str, Any]:
        state: CrazyFuturesState = room.state
        ledgers = []
        for player_id in state.turn_order:
            ledger = state.ledgers[player_id]
            total_margin = sum(
                (position.margin for position in ledger.positions.values()),
                Fraction(0),
            )
            unrealized = sum(
                (
                    abs(position.quantity)
                    * (
                        Fraction(
                            PRICE_LADDER[state.markets[commodity].current_index]
                        )
                        - position.basis
                    )
                    * _direction(position.quantity)
                    for commodity, position in ledger.positions.items()
                    if position.quantity
                ),
                Fraction(0),
            )
            ledgers.append(
                {
                    "playerId": player_id,
                    "cash": _number(ledger.cash),
                    "margin": _number(total_margin),
                    "loanPrincipal": self._loan_principal(ledger),
                    "loanInterest": _number(self._loan_interest(ledger)),
                    "exchangeDebt": _number(ledger.exchange_debt),
                    "estimatedEquity": _number(
                        ledger.cash + total_margin + unrealized - ledger.exchange_debt
                    ),
                    "positions": {
                        commodity: {
                            "quantity": position.quantity,
                            "basis": _number(position.basis),
                            "margin": _number(position.margin),
                        }
                        for commodity, position in ledger.positions.items()
                    },
                    "handCount": len(ledger.hand),
                    "bankrupt": ledger.bankrupt,
                    "forfeited": ledger.forfeited,
                    "forcedLiquidations": ledger.forced_liquidations,
                    "marginBuffer": ledger.margin_buffer,
                    "finalScore": (
                        _number(ledger.final_score)
                        if ledger.final_score is not None
                        else None
                    ),
                }
            )
        auction = state.auction
        pending = state.pending_choice
        events = [
            self._event_dict(event)
            for event in state.events
            if event.visible_to is None or event.visible_to == viewer_id
        ]
        hand = (
            [
                card_for_instance(instance_id).public_dict(instance_id)
                for instance_id in state.ledgers[viewer_id].hand
            ]
            if viewer_id in state.ledgers
            else []
        )
        peek = (
            [
                card_for_instance(instance_id).public_dict(instance_id)
                for instance_id in state.ledgers[viewer_id].peek_cards
            ]
            if viewer_id in state.ledgers
            else []
        )
        return {
            "version": "0.2-balanced-ladder",
            "round": state.round_number,
            "maxRounds": MAX_ROUNDS,
            "stage": state.stage,
            "stageLabel": self._stage_label(state.stage),
            "currentPlayerId": state.current_player_id,
            "starterPlayerId": (
                state.turn_order[state.starter_index]
                if state.turn_order
                else None
            ),
            "turnOrder": list(state.turn_order),
            "markets": [
                {
                    "commodity": commodity,
                    "name": COMMODITY_LABELS[commodity],
                    "color": COMMODITY_COLORS[commodity],
                    "spotIndex": market.spot_index,
                    "spotPrice": PRICE_LADDER[market.spot_index],
                    "openIndex": market.open_index,
                    "openPrice": PRICE_LADDER[market.open_index],
                    "currentIndex": market.current_index,
                    "currentPrice": PRICE_LADDER[market.current_index],
                    "closeIndex": market.close_index,
                    "closePrice": PRICE_LADDER[market.close_index],
                    "lowLimitIndex": market.low_limit_index,
                    "lowLimitPrice": PRICE_LADDER[market.low_limit_index],
                    "highLimitIndex": market.high_limit_index,
                    "highLimitPrice": PRICE_LADDER[market.high_limit_index],
                    "validTradeIndices": list(market.valid_trade_indices[-3:]),
                    "validTradePrices": [
                        PRICE_LADDER[index]
                        for index in market.valid_trade_indices[-3:]
                    ],
                    "seal": market.seal,
                }
                for commodity, market in state.markets.items()
            ],
            "priceLadder": list(PRICE_LADDER),
            "priceZones": {
                name: list(bounds) for name, bounds in PRICE_ZONES.items()
            },
            "ledgers": ledgers,
            "auction": (
                {
                    "initiatorId": auction.initiator_id,
                    "commodity": auction.commodity,
                    "side": auction.side,
                    "quoteIndex": auction.quote_index,
                    "price": PRICE_LADDER[auction.quote_index],
                    "leaderId": auction.leader_id,
                    "passedIds": list(auction.passed_ids),
                    "cursorPlayerId": auction.cursor_player_id,
                    "initiationNumber": state.initiation_cursor + 1,
                    "initiationTotal": len(state.initiation_order),
                }
                if auction is not None
                else None
            ),
            "initiationNumber": min(
                state.initiation_cursor + 1,
                len(state.initiation_order),
            ),
            "initiationTotal": len(state.initiation_order),
            "publicEvents": [
                card_for_instance(instance_id).public_dict(instance_id)
                for instance_id in state.revealed_public[-8:]
            ],
            "activeEffects": [self._active_effect_dict(effect) for effect in state.active_effects],
            "hand": hand,
            "peekCards": peek,
            "deckCounts": {
                "personal": len(state.personal_deck),
                "personalDiscard": len(state.personal_discard),
                "public": len(state.public_deck),
                "publicDiscard": len(state.public_discard),
            },
            "pendingChoice": (
                {
                    "kind": pending.kind,
                    "playerId": pending.player_id,
                    "count": pending.count if pending.player_id == viewer_id else None,
                    "reason": pending.reason if pending.player_id == viewer_id else None,
                    "isMine": pending.player_id == viewer_id,
                }
                if pending is not None
                else None
            ),
            "legalActions": (
                self._legal_actions(state, viewer_id)
                if viewer_id is not None
                else {}
            ),
            "events": events[-40:],
            "rankings": list(state.rankings),
        }

    def player_result(
        self,
        room: ArcadeRoom,
        player: ArcadePlayer,
    ) -> tuple[str, str, bool]:
        state: CrazyFuturesState = room.state
        ledger = state.ledgers.get(player.id)
        if ledger is None:
            return "未参赛", "observer", False
        if ledger.bankrupt:
            role = "破产交易员"
            team = "bankrupt"
        else:
            score = _number(ledger.final_score or ledger.cash)
            role = f"净资产 {score} 万"
            team = "trader"
        return role, team, player.id in room.winner_player_ids

    def record_state(self, room: ArcadeRoom) -> dict[str, Any]:
        return self._view(room, None)

    def _legal_actions(
        self,
        state: CrazyFuturesState,
        player_id: str,
    ) -> dict[str, Any]:
        ledger = state.ledgers.get(player_id)
        if ledger is None or ledger.bankrupt or ledger.forfeited:
            return {}
        result: dict[str, Any] = {"canResign": True}
        pending = state.pending_choice
        if pending is not None:
            if pending.player_id != player_id:
                return result
            if pending.kind == "discard":
                result["discardCount"] = pending.count
            elif pending.kind == "liquidation":
                result["liquidationCommodities"] = [
                    commodity
                    for commodity, position in ledger.positions.items()
                    if position.quantity
                ]
            return result
        if state.current_player_id != player_id:
            return result
        if state.stage == "loan":
            remaining = MAX_LOAN - self._loan_principal(ledger)
            result["borrowAmounts"] = list(range(0, remaining + 1, LOAN_STEP))
        elif state.stage == "auction":
            if state.auction is None:
                starts = self._legal_start_options(state, player_id)
                result["auctionStarts"] = starts
                result["canSkipAuction"] = not starts
            elif state.auction.cursor_player_id == player_id:
                result["bidQuoteIndices"] = self._legal_bid_indices(state, player_id)
                result["canPassBid"] = True
        elif state.stage == "card":
            result["playableCards"] = [
                option
                for instance_id in ledger.hand
                if (option := self._card_play_option(state, player_id, instance_id))
                is not None
            ]
            result["reduceOnlyCommodities"] = [
                commodity
                for commodity, market in state.markets.items()
                if market.seal in {"up", "down"}
                and ledger.positions[commodity].quantity
            ]
            result["canPassCard"] = True
        return result

    def _legal_bid_indices(
        self,
        state: CrazyFuturesState,
        player_id: str,
    ) -> list[int]:
        auction = state.auction
        if auction is None:
            return []
        market = state.markets[auction.commodity]
        direction = 1 if auction.side == "buy" else -1
        indices: Iterable[int]
        if auction.side == "buy":
            indices = range(auction.quote_index + 1, market.high_limit_index + 1)
        else:
            indices = range(market.low_limit_index, auction.quote_index)
        return [
            index
            for index in indices
            if self._trade_would_be_legal(
                state.ledgers[player_id],
                auction.commodity,
                direction,
                PRICE_LADDER[index],
            )
        ]

    def _card_play_option(
        self,
        state: CrazyFuturesState,
        player_id: str,
        instance_id: str,
    ) -> dict[str, Any] | None:
        card = card_for_instance(instance_id)
        effect = card.effect
        effect_type = effect["type"]
        option: dict[str, Any] = {
            "instanceId": instance_id,
            "cardId": card.id,
        }
        if effect_type == "spot_move":
            if effect.get("choose"):
                commodities = list(effect["targetOptions"])
                zone = effect.get("zone")
                if zone:
                    low, high = PRICE_ZONES[zone]
                    commodities = [
                        commodity
                        for commodity in commodities
                        if low <= state.markets[commodity].spot_index <= high
                    ]
                if not commodities:
                    return None
                option["commodities"] = commodities
            return option
        if effect_type == "persistent_spot":
            return option
        if effect_type == "seal":
            side = effect["side"]
            commodities = [
                commodity
                for commodity, market in state.markets.items()
                if market.seal is None
                and (
                    market.current_index == market.high_limit_index
                    if side == "up"
                    else market.current_index == market.low_limit_index
                )
            ]
            if not commodities:
                return None
            option["commodities"] = commodities
            return option
        if effect_type == "peek_public":
            return option if state.round_number < MAX_ROUNDS and state.public_deck else None
        if effect_type == "loan_discount":
            loan = self._current_round_loan(state, player_id)
            return option if loan is not None and loan.rate_percent == 10 else None
        if effect_type == "margin_buffer":
            return option
        if effect_type == "remove_persistent":
            effect_ids = [
                active.id
                for active in state.active_effects
                if active.scope == "personal"
            ]
            if not effect_ids:
                return None
            option["effectIds"] = effect_ids
            return option
        if effect_type == "information_swap":
            return option
        return None

    def _trade_would_be_legal(
        self,
        ledger: PlayerLedger,
        commodity: str,
        direction: int,
        price: int,
    ) -> bool:
        position = ledger.positions[commodity]
        new_quantity = position.quantity + direction
        if abs(new_quantity) > MAX_COMMODITY_POSITION:
            return False
        new_total = sum(
            abs(item.quantity)
            for key, item in ledger.positions.items()
            if key != commodity
        ) + abs(new_quantity)
        if new_total > MAX_TOTAL_POSITION:
            return False
        cash = ledger.cash
        if position.quantity == 0 or position.quantity * direction > 0:
            cash -= Fraction(price, 2)
        else:
            realized = (
                Fraction(price) - position.basis
            ) * _direction(position.quantity)
            release = position.margin / abs(position.quantity)
            cash += realized + release
        return cash >= 0

    def _apply_trade(
        self,
        ledger: PlayerLedger,
        commodity: str,
        direction: int,
        price: int,
    ) -> None:
        position = ledger.positions[commodity]
        old_quantity = position.quantity
        if old_quantity == 0 or old_quantity * direction > 0:
            old_abs = abs(old_quantity)
            new_quantity = old_quantity + direction
            position.basis = (
                position.basis * old_abs + Fraction(price)
            ) / abs(new_quantity)
            position.quantity = new_quantity
            margin = Fraction(price, 2)
            position.margin += margin
            ledger.cash -= margin
            return
        realized = (Fraction(price) - position.basis) * _direction(old_quantity)
        margin_release = position.margin / abs(old_quantity)
        ledger.cash += realized + margin_release
        position.margin -= margin_release
        position.quantity = old_quantity + direction
        if position.quantity == 0:
            position.basis = Fraction(0)
            position.margin = Fraction(0)

    def _apply_spot_moves(
        self,
        state: CrazyFuturesState,
        moves: Iterable[dict[str, Any]],
        cause: str,
    ) -> None:
        for move in moves:
            commodity = move["commodity"]
            delta = int(move["delta"])
            market = state.markets[commodity]
            previous = market.spot_index
            market.spot_index = max(
                0,
                min(len(PRICE_LADDER) - 1, market.spot_index + delta),
            )
            self._emit(
                state,
                "spot_move",
                f"{cause}：{COMMODITY_LABELS[commodity]}现货"
                f"{('上涨' if market.spot_index > previous else '下跌' if market.spot_index < previous else '触及价格表边界')}"
                f"至 {PRICE_LADDER[market.spot_index]} 万",
                {
                    "commodity": commodity,
                    "fromIndex": previous,
                    "toIndex": market.spot_index,
                    "price": PRICE_LADDER[market.spot_index],
                    "cause": cause,
                },
            )

    def _add_active_effect(
        self,
        state: CrazyFuturesState,
        instance_id: str,
        card: CardDefinition,
        owner_id: str | None,
    ) -> None:
        state.effect_sequence += 1
        moves = [dict(move) for move in card.effect["moves"]]
        total_delta = sum(move["delta"] for move in moves)
        state.active_effects.append(
            ActiveEffect(
                id=f"effect-{state.effect_sequence}",
                instance_id=instance_id,
                card_id=card.id,
                card_name=card.name,
                scope=card.effect["scope"],
                owner_id=owner_id,
                moves=moves,
                remaining_triggers=card.effect["triggers"],
                sequence=state.effect_sequence,
                direction="up" if total_delta > 0 else "down",
            )
        )

    def _resolve_remove_effect(
        self,
        state: CrazyFuturesState,
        effect: dict[str, Any],
        target_effect_id: str | None,
    ) -> None:
        candidates = [
            active
            for active in state.active_effects
            if active.scope == effect["scope"]
            and (
                effect.get("direction") is None
                or active.direction == effect["direction"]
            )
        ]
        selected: list[ActiveEffect] = []
        strategy = effect.get("strategy")
        if target_effect_id is not None:
            selected = [
                active for active in candidates if active.id == target_effect_id
            ]
        elif strategy == "all":
            selected = candidates
        elif strategy == "latest" and candidates:
            selected = [max(candidates, key=lambda active: active.sequence)]
        elif strategy == "most_remaining_oldest" and candidates:
            selected = [
                max(
                    candidates,
                    key=lambda active: (
                        active.remaining_triggers,
                        -active.sequence,
                    ),
                )
            ]
        for active in selected:
            self._remove_active_effect(state, active, "被修正效果移除")

    def _remove_active_effect(
        self,
        state: CrazyFuturesState,
        effect: ActiveEffect,
        reason: str,
    ) -> None:
        if effect not in state.active_effects:
            return
        state.active_effects.remove(effect)
        if effect.scope == "personal":
            state.personal_discard.append(effect.instance_id)
        else:
            state.public_discard.append(effect.instance_id)
        self._emit(
            state,
            "effect_removed",
            f"《{effect.card_name}》{reason}",
            {"effectId": effect.id, "cardId": effect.card_id},
        )

    def _mark_bankrupt(
        self,
        room: ArcadeRoom,
        state: CrazyFuturesState,
        player_id: str,
    ) -> None:
        ledger = state.ledgers[player_id]
        if ledger.bankrupt:
            return
        if ledger.cash < 0:
            ledger.exchange_debt += -ledger.cash
            ledger.cash = Fraction(0)
        state.personal_discard.extend(ledger.hand)
        ledger.hand.clear()
        ledger.bankrupt = True
        self._emit(
            state,
            "bankrupt",
            f"{room.player(player_id).name} 关闭全部头寸后仍无法偿债，宣告破产",
            {"playerId": player_id, "debt": _number(ledger.exchange_debt)},
        )

    def _finish_all_bankrupt(self, room: ArcadeRoom, state: CrazyFuturesState) -> None:
        state.stage = "final"
        state.current_player_id = None
        room.finish("bankrupt", [], "所有交易员均已破产，本局无人获胜")

    def _ordered_active_ids(self, state: CrazyFuturesState) -> list[str]:
        if not state.turn_order:
            return []
        ordered = state.turn_order[state.starter_index :] + state.turn_order[: state.starter_index]
        return [player_id for player_id in ordered if self._is_active(state, player_id)]

    def _next_active_id(
        self,
        state: CrazyFuturesState,
        after_player_id: str,
    ) -> str | None:
        if after_player_id not in state.turn_order:
            return None
        start = state.turn_order.index(after_player_id)
        for offset in range(1, len(state.turn_order) + 1):
            candidate = state.turn_order[(start + offset) % len(state.turn_order)]
            if self._is_active(state, candidate):
                return candidate
        return None

    @staticmethod
    def _is_active(state: CrazyFuturesState, player_id: str) -> bool:
        ledger = state.ledgers.get(player_id)
        return bool(ledger and not ledger.bankrupt and not ledger.forfeited)

    @staticmethod
    def _has_positions(ledger: PlayerLedger) -> bool:
        return any(position.quantity for position in ledger.positions.values())

    @staticmethod
    def _loan_principal(ledger: PlayerLedger) -> int:
        return sum(record.principal for record in ledger.loans)

    @staticmethod
    def _loan_interest(ledger: PlayerLedger) -> Fraction:
        return sum(
            (
                Fraction(
                    record.principal
                    * record.rate_percent
                    * (MAX_ROUNDS - record.borrowed_round + 1),
                    100,
                )
                for record in ledger.loans
            ),
            Fraction(0),
        )

    @staticmethod
    def _winner_key(ledger: PlayerLedger) -> tuple[Fraction, int, int]:
        return (
            ledger.final_score or Fraction(0),
            -CrazyFuturesEngine._loan_principal(ledger),
            -ledger.forced_liquidations,
        )

    @staticmethod
    def _current_round_loan(
        state: CrazyFuturesState,
        player_id: str,
    ) -> LoanRecord | None:
        return next(
            (
                record
                for record in reversed(state.ledgers[player_id].loans)
                if record.borrowed_round == state.round_number
            ),
            None,
        )

    @staticmethod
    def _require_turn(
        state: CrazyFuturesState,
        player_id: str,
        stage: str,
        message: str,
    ) -> None:
        if state.stage != stage or state.current_player_id != player_id:
            raise GameRuleError(message)
        if state.pending_choice is not None:
            raise GameRuleError("请先完成当前选择")

    @staticmethod
    def _require_card_turn(state: CrazyFuturesState, player_id: str) -> None:
        if state.stage != "card" or state.current_player_id != player_id:
            raise GameRuleError("还没有轮到你出牌")
        if state.pending_choice is not None:
            raise GameRuleError("请先完成当前选择")

    @staticmethod
    def _stage_label(stage: str) -> str:
        return {
            "loan": "借贷阶段",
            "draw": "抽牌阶段",
            "public_event": "公共事件阶段",
            "auction": "竞价阶段",
            "card": "出牌阶段",
            "settlement": "结算阶段",
            "liquidation": "强制平仓",
            "discard": "手牌整理",
            "final": "终局结算",
        }.get(stage, stage)

    @staticmethod
    def _active_effect_dict(effect: ActiveEffect) -> dict[str, Any]:
        return {
            "effectId": effect.id,
            "cardId": effect.card_id,
            "cardName": effect.card_name,
            "scope": effect.scope,
            "ownerId": effect.owner_id,
            "moves": [dict(move) for move in effect.moves],
            "remainingTriggers": effect.remaining_triggers,
            "direction": effect.direction,
        }

    @staticmethod
    def _event_dict(event: GameEvent) -> dict[str, Any]:
        return {
            "seq": event.seq,
            "type": event.type,
            "message": event.message,
            "data": dict(event.data),
        }

    @staticmethod
    def _emit(
        state: CrazyFuturesState,
        event_type: str,
        message: str,
        data: dict[str, Any],
        visible_to: str | None = None,
    ) -> None:
        state.event_sequence += 1
        state.events.append(
            GameEvent(
                seq=state.event_sequence,
                type=event_type,
                message=message,
                data=data,
                visible_to=visible_to,
            )
        )
        state.events = state.events[-EVENT_HISTORY_LIMIT:]
