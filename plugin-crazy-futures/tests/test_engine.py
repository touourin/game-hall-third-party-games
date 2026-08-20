from __future__ import annotations

import json
import random
from fractions import Fraction
from importlib import import_module
from pathlib import Path

import pytest

from backend.app.games.plugin_api import ArcadePlayer, ArcadeRoom, GameRuleError
from backend.app.games.plugins import discover_game_plugins


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
COMMUNITY_ROOT = PLUGIN_ROOT.parent


def load_engine():
    return next(
        plugin.engine
        for plugin in discover_game_plugins(COMMUNITY_ROOT)
        if plugin.engine.key == "plugin-crazy-futures"
    )


def make_room(player_count: int = 4):
    game = load_engine()
    game.rng = random.Random(1000 + player_count)
    players = [
        ArcadePlayer(
            f"p{index}",
            f"a{index}",
            f"玩家{index}",
            f"token-{index}",
            index - 1,
        )
        for index in range(1, player_count + 1)
    ]
    room = ArcadeRoom(
        "FTRS",
        game.key,
        players[0].id,
        players,
        game.initial_state(),
        options={"firstPlayer": "host"},
    )
    game.start(room)
    return game, room, players


def plugin_module(game, module_name: str):
    package = game.__class__.__module__.rsplit(".", 1)[0]
    return import_module(f"{package}.{module_name}")


def finish_loans(game, room) -> None:
    while room.state.stage == "loan":
        player = room.player(room.state.current_player_id)
        game.act(room, player, "borrow", {"amount": 0})


def test_catalog_has_exact_card_and_ladder_counts() -> None:
    catalog = json.loads((PLUGIN_ROOT / "data" / "cards.json").read_text("utf-8"))
    assert sum(card["copies"] for card in catalog["personal"]) == 160
    assert len(catalog["personal"]) == 67
    assert len(catalog["publicEvents"]) == 20
    assert len(catalog["priceLadder"]) == 51
    assert all(price % 2 == 0 for price in catalog["priceLadder"])
    assert catalog["personal"][44]["id"] == "PI-MACRO-05"
    assert catalog["personal"][44]["effect"]["zone"] == "low"


@pytest.mark.parametrize("player_count", [4, 5, 8])
def test_snake_order_gives_every_player_two_initiations(player_count: int) -> None:
    game, room, players = make_room(player_count)
    finish_loans(game, room)

    expected = [player.id for player in players]
    assert room.state.initiation_order == expected + list(reversed(expected))
    assert len(room.state.initiation_order) == player_count * 2
    assert all(room.state.initiation_order.count(player.id) == 2 for player in players)


def test_draw_and_public_events_are_automatic_after_loans() -> None:
    game, room, players = make_room()
    finish_loans(game, room)

    assert room.state.stage == "auction"
    assert all(len(room.state.ledgers[player.id].hand) == 2 for player in players)
    assert len(room.state.revealed_public) == 2
    assert len(room.state.public_deck) == 18


def test_auction_improves_quote_and_trades_with_exchange() -> None:
    game, room, players = make_room()
    finish_loans(game, room)

    game.act(
        room,
        players[0],
        "start_auction",
        {"commodity": "oil", "side": "buy", "quoteIndex": 25},
    )
    assert room.state.auction.cursor_player_id == players[1].id
    game.act(room, players[1], "bid", {"quoteIndex": 26})
    game.act(room, players[2], "pass_bid", {})
    game.act(room, players[3], "pass_bid", {})
    game.act(room, players[0], "pass_bid", {})

    ledger = room.state.ledgers[players[1].id]
    assert ledger.positions["oil"].quantity == 1
    assert ledger.positions["oil"].basis == 110
    assert ledger.positions["oil"].margin == 55
    assert ledger.cash == 45
    assert room.state.markets["oil"].current_index == 26
    assert room.state.markets["oil"].valid_trade_indices == [26]


def test_last_three_trade_median_becomes_close() -> None:
    game, room, _ = make_room()
    finish_loans(game, room)
    room.state.markets["oil"].valid_trade_indices = [25, 28, 26, 27]
    room.state.markets["gold"].valid_trade_indices = [28, 26]

    game._begin_settlement(room, room.state)

    assert room.state.markets["oil"].close_index == 27
    assert room.state.markets["gold"].close_index == 26
    assert room.state.markets["cotton"].close_index == 25


def test_low_zone_card_moves_only_a_low_price_commodity() -> None:
    game, room, players = make_room()
    finish_loans(game, room)
    game._begin_card_stage(room, room.state)
    ledger = room.state.ledgers[players[0].id]
    instance_id = "PI-MACRO-05#1"
    if instance_id in room.state.personal_deck:
        room.state.personal_deck.remove(instance_id)
    ledger.hand.append(instance_id)
    room.state.markets["oil"].spot_index = 0

    game.act(
        room,
        players[0],
        "play_card",
        {"instanceId": instance_id, "commodity": "oil"},
    )

    assert room.state.markets["oil"].spot_index == 1
    with pytest.raises(GameRuleError):
        ledger.hand.append("PI-MACRO-05#2")
        room.state.current_player_id = players[0].id
        game.act(
            room,
            players[0],
            "play_card",
            {"instanceId": "PI-MACRO-05#2", "commodity": "gold"},
        )


def test_discount_card_changes_only_current_round_loan() -> None:
    game, room, players = make_room()
    game.act(room, players[0], "borrow", {"amount": 100})
    while room.state.stage == "loan":
        current = room.player(room.state.current_player_id)
        game.act(room, current, "borrow", {"amount": 0})
    game._begin_card_stage(room, room.state)
    ledger = room.state.ledgers[players[0].id]
    instance_id = "PF-04#1"
    if instance_id in room.state.personal_deck:
        room.state.personal_deck.remove(instance_id)
    ledger.hand.append(instance_id)

    game.act(room, players[0], "play_card", {"instanceId": instance_id})

    assert ledger.loans[0].rate_percent == 5
    assert game._loan_interest(ledger) == 40


def test_seal_does_not_block_reduce_only_or_forced_liquidation() -> None:
    game, room, players = make_room()
    finish_loans(game, room)
    game._begin_card_stage(room, room.state)
    state = room.state
    ledger = state.ledgers[players[0].id]
    market = state.markets["oil"]
    position = ledger.positions["oil"]
    position.quantity = 1
    position.basis = Fraction(100)
    position.margin = Fraction(50)
    ledger.cash = Fraction(20)
    market.current_index = market.high_limit_index
    market.seal = "up"

    game.act(room, players[0], "reduce_only", {"commodity": "oil"})

    assert position.quantity == 0
    assert ledger.cash > 20

    position.quantity = 1
    position.basis = Fraction(100)
    position.margin = Fraction(50)
    ledger.cash = Fraction(-10)
    state.stage = "liquidation"
    state.current_player_id = players[0].id
    state.settlement_queue = [players[0].id]
    state_module = plugin_module(game, "state")
    state.pending_choice = state_module.PendingChoice("liquidation", players[0].id, 1)
    game.act(room, players[0], "choose_liquidation", {"commodity": "oil"})
    assert position.quantity == 0
    assert ledger.cash == 40
    assert not ledger.bankrupt


def test_hidden_hands_and_peek_never_leak_to_other_views_or_recording() -> None:
    game, room, players = make_room()
    finish_loans(game, room)
    first_view = game.view(room, players[0])
    second_view = game.view(room, players[1])

    first_ids = {card["instanceId"] for card in first_view["hand"]}
    second_ids = {card["instanceId"] for card in second_view["hand"]}
    assert first_ids
    assert second_ids
    assert first_ids.isdisjoint(second_ids)
    recorded = game.record_state(room)
    assert recorded["hand"] == []
    assert recorded["peekCards"] == []


def test_round_eight_convergence_uses_spot_and_tie_breakers() -> None:
    game, room, players = make_room()
    state = room.state
    state.round_number = 8
    first = state.ledgers[players[0].id]
    first.cash = Fraction(100)
    first.positions["gold"].quantity = 1
    first.positions["gold"].basis = Fraction(100)
    first.positions["gold"].margin = Fraction(50)
    state.markets["gold"].spot_index = 26

    game._finalize_game(room, state)

    assert room.phase == "finished"
    assert players[0].id in room.winner_player_ids
    assert first.final_score == 160


def test_loan_uses_monthly_simple_interest_and_emits_cash_flow() -> None:
    game, room, players = make_room()
    game.act(room, players[0], "borrow", {"amount": 60})

    ledger = room.state.ledgers[players[0].id]
    assert ledger.cash == 160
    assert game._loan_interest(ledger) == 48
    event = room.state.events[-1]
    assert event.type == "loan"
    assert event.data["cashDelta"] == 60


def test_price_limits_and_position_caps_are_checked_before_quoting() -> None:
    game, room, players = make_room()
    finish_loans(game, room)
    state = room.state
    ledger = state.ledgers[players[0].id]
    market = state.markets["oil"]

    options = game._legal_start_options(state, players[0].id)
    assert options
    assert all(
        market.low_limit_index <= index <= market.high_limit_index
        for option in options
        if option["commodity"] == "oil"
        for index in option["quoteIndices"]
    )

    ledger.cash = Fraction(1000)
    ledger.positions["oil"].quantity = 4
    ledger.positions["oil"].basis = Fraction(100)
    ledger.positions["oil"].margin = Fraction(200)
    assert not game._trade_would_be_legal(ledger, "oil", 1, 100)
    assert game._trade_would_be_legal(ledger, "oil", -1, 100)

    ledger.positions["gold"].quantity = 4
    ledger.positions["gold"].basis = Fraction(100)
    ledger.positions["gold"].margin = Fraction(200)
    assert not game._trade_would_be_legal(ledger, "copper", 1, 100)


def test_settlement_marks_to_market_and_recalculates_margin() -> None:
    game, room, players = make_room()
    finish_loans(game, room)
    state = room.state
    state.active_effects.clear()
    ledger = state.ledgers[players[0].id]
    position = ledger.positions["oil"]
    position.quantity = 2
    position.basis = Fraction(100)
    position.margin = Fraction(100)
    ledger.cash = Fraction(50)
    state.markets["oil"].valid_trade_indices = [26]

    game._begin_settlement(room, state)

    assert position.basis == 110
    assert position.margin == 110
    assert ledger.cash == 60


def test_margin_buffer_reduces_only_the_next_margin_call() -> None:
    game, room, players = make_room()
    finish_loans(game, room)
    state = room.state
    state.active_effects.clear()
    ledger = state.ledgers[players[0].id]
    position = ledger.positions["oil"]
    position.quantity = 1
    position.basis = Fraction(100)
    position.margin = Fraction(50)
    ledger.cash = Fraction(50)
    ledger.margin_buffer = 10
    state.markets["oil"].valid_trade_indices = [27]

    game._begin_settlement(room, state)

    assert position.basis == 122
    assert position.margin == 61
    assert ledger.cash == 71
    assert ledger.margin_buffer == 0


def test_persistent_personal_card_triggers_twice_then_expires() -> None:
    game, room, players = make_room()
    state = room.state
    state.active_effects.clear()
    state.personal_discard.clear()
    state.markets["oil"].spot_index = 25
    catalog = plugin_module(game, "catalog")
    card = catalog.card_for_instance("PI-DURATION-01#1")
    game._add_active_effect(
        state,
        "PI-DURATION-01#1",
        card,
        owner_id=players[0].id,
    )

    game._trigger_persistent_effects(state)
    assert state.markets["oil"].spot_index == 26
    assert state.active_effects[0].remaining_triggers == 1
    game._trigger_persistent_effects(state)

    assert state.markets["oil"].spot_index == 27
    assert state.active_effects == []
    assert "PI-DURATION-01#1" in state.personal_discard


def test_peek_and_information_swap_remain_private() -> None:
    game, room, players = make_room()
    finish_loans(game, room)
    game._begin_card_stage(room, room.state)
    ledger = room.state.ledgers[players[0].id]
    for instance_id in ("PF-03#1", "PF-07#1"):
        if instance_id in room.state.personal_deck:
            room.state.personal_deck.remove(instance_id)
        ledger.hand.append(instance_id)

    game.act(room, players[0], "play_card", {"instanceId": "PF-03#1"})
    assert len(game.view(room, players[0])["peekCards"]) == 2
    assert game.view(room, players[1])["peekCards"] == []
    assert game.record_state(room)["peekCards"] == []

    room.state.current_player_id = players[0].id
    count_before_swap = len(ledger.hand)
    game.act(room, players[0], "play_card", {"instanceId": "PF-07#1"})
    assert room.state.pending_choice.player_id == players[0].id
    assert len(ledger.hand) == count_before_swap + 1
    discarded = ledger.hand[:2]
    game.act(room, players[0], "discard_cards", {"instanceIds": discarded})
    assert room.state.pending_choice is None
    assert len(ledger.hand) == count_before_swap - 1


def test_forced_liquidation_ignores_seal_and_preserves_exchange_debt() -> None:
    game, room, players = make_room()
    state = room.state
    ledger = state.ledgers[players[0].id]
    position = ledger.positions["oil"]
    position.quantity = 1
    position.basis = Fraction(100)
    position.margin = Fraction(50)
    ledger.cash = Fraction(-100)
    state.markets["oil"].close_index = 25
    state.markets["oil"].seal = "up"
    state.stage = "liquidation"
    state.current_player_id = players[0].id
    state.settlement_queue = [players[0].id]
    state.pending_choice = plugin_module(game, "state").PendingChoice(
        "liquidation",
        players[0].id,
        1,
    )

    game.act(room, players[0], "choose_liquidation", {"commodity": "oil"})

    assert position.quantity == 0
    assert ledger.bankrupt
    assert ledger.cash == 0
    assert ledger.exchange_debt == 50


def test_reduce_only_bankrupts_immediately_after_the_last_position_closes() -> None:
    game, room, players = make_room()
    state = room.state
    ledger = state.ledgers[players[0].id]
    position = ledger.positions["oil"]
    position.quantity = 1
    position.basis = Fraction(100)
    position.margin = Fraction(50)
    ledger.cash = Fraction(-40)
    state.markets["oil"].seal = "down"
    state.stage = "card"
    state.current_player_id = players[0].id

    game.act(room, players[0], "reduce_only", {"commodity": "oil"})

    assert position.quantity == 0
    assert ledger.bankrupt
    assert ledger.cash == 0
    assert ledger.exchange_debt == 14


def test_manual_forfeit_closes_positions_and_skips_future_actions() -> None:
    game, room, players = make_room()
    state = room.state
    ledger = state.ledgers[players[0].id]
    position = ledger.positions["gold"]
    position.quantity = -2
    position.basis = Fraction(100)
    position.margin = Fraction(100)
    ledger.cash = Fraction(20)
    ledger.hand.extend(["PF-01#1", "PF-02#1"])

    assert game.manual_forfeit(room, players[0])
    assert position.quantity == 0
    assert ledger.hand == []
    assert ledger.bankrupt and ledger.forfeited
    assert players[0].id not in game._ordered_active_ids(state)


def test_all_bankrupt_ends_without_a_winner() -> None:
    game, room, _ = make_room()
    for ledger in room.state.ledgers.values():
        ledger.bankrupt = True

    game._begin_round(room, room.state)

    assert room.phase == "finished"
    assert room.winner_player_ids == []


def test_final_tie_breakers_prefer_less_debt_then_fewer_liquidations() -> None:
    game, room, players = make_room()
    state = room.state
    state.round_number = 8
    state_module = plugin_module(game, "state")
    first, second, third, fourth = (state.ledgers[player.id] for player in players)
    first.cash = Fraction(110)
    first.loans = [state_module.LoanRecord(10, 8, 0)]
    second.cash = Fraction(100)
    second.forced_liquidations = 1
    third.cash = Fraction(100)
    third.forced_liquidations = 0
    fourth.cash = Fraction(80)

    game._finalize_game(room, state)

    assert room.winner_player_ids == [players[2].id]
    assert first.final_score == second.final_score == third.final_score == 100
