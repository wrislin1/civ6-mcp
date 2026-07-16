"""Exact gold-only Civ 6 trade builders for unofficial-channel settlement."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from civ_mcp.lua._helpers import SENTINEL, _int


def _require_int(
    value: Any,
    field: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return value


def _payment_inputs(other_player: int, gold: int) -> tuple[int, int]:
    return (
        _require_int(other_player, "player", minimum=0, maximum=63),
        _require_int(gold, "gold", minimum=1, maximum=10_000),
    )


@dataclass(frozen=True)
class ExactPaymentOffer:
    payer: int
    payee: int
    gold: int
    duration: int = 0
    item_count: int = 1

    def fingerprint(self) -> dict[str, int]:
        return {
            "payer": self.payer,
            "payee": self.payee,
            "gold": self.gold,
            "duration": self.duration,
            "item_count": self.item_count,
        }


class PaymentOfferStatus(StrEnum):
    """Authoritative state of one ordered-pair payment fingerprint."""

    ABSENT = "absent"
    EXACT = "exact"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class ChannelPaymentOfferState:
    status: PaymentOfferStatus
    offer: ExactPaymentOffer | None = None

    def __post_init__(self) -> None:
        if (self.status is PaymentOfferStatus.EXACT) != (self.offer is not None):
            raise ValueError("only an exact payment state may contain an offer")


def _payment_state_inputs(payer: int, payee: int, gold: int) -> tuple[int, int, int]:
    payer = _require_int(payer, "payer", minimum=0, maximum=63)
    payee = _require_int(payee, "payee", minimum=0, maximum=63)
    gold = _require_int(gold, "gold", minimum=1, maximum=10_000)
    if payer == payee:
        raise ValueError("payer and payee must be different players")
    return payer, payee, gold


def build_channel_payment_offer(payee: int, gold: int) -> str:
    """Propose one lump-sum gold item without accepting any AI response."""
    payee, gold = _payment_inputs(payee, gold)
    return f"""
local me = Game.GetLocalPlayer()
local target = {payee}
if me == target then
    print("ERR:CHANNEL_PAYMENT_SELF")
    print("{SENTINEL}")
    return
end
if not Players[target] or not Players[target]:IsAlive() then
    print("ERR:CHANNEL_PAYMENT_INVALID_PAYEE")
    print("{SENTINEL}")
    return
end
if DealManager.HasPendingDeal(me, target) then
    print("ERR:CHANNEL_PAYMENT_PENDING_DEAL")
    print("{SENTINEL}")
    return
end
DealManager.ClearWorkingDeal(DealDirection.OUTGOING, me, target)
local deal = DealManager.GetWorkingDeal(DealDirection.OUTGOING, me, target)
if not deal or deal:GetItemCount() ~= 0 then
    print("ERR:CHANNEL_PAYMENT_NO_CLEAN_DEAL")
    print("{SENTINEL}")
    return
end
local goldItem = deal:AddItemOfType(DealItemTypes.GOLD, me)
if not goldItem then
    print("ERR:CHANNEL_PAYMENT_ADD_GOLD_FAILED")
    print("{SENTINEL}")
    return
end
goldItem:SetAmount({gold})
goldItem:SetDuration(0)
if deal:GetItemCount() ~= 1 then
    print("ERR:CHANNEL_PAYMENT_NOT_EXACT")
    print("{SENTINEL}")
    return
end
DiplomacyManager.RequestSession(me, target, "MAKE_DEAL")
DealManager.SendWorkingDeal(DealProposalAction.PROPOSED, me, target)
print("OK:CHANNEL_PAYMENT_PROPOSED")
print("{SENTINEL}")
"""


def _build_exact_incoming_check(payer: int, gold: int, success_lua: str) -> str:
    return f"""
local me = Game.GetLocalPlayer()
local payer = {payer}
local target = payer
if me == payer or not DealManager.HasPendingDeal(payer, me) then
    print("ERR:NO_EXACT_CHANNEL_PAYMENT")
    print("{SENTINEL}")
    return
end
local deal = DealManager.GetWorkingDeal(DealDirection.INCOMING, me, payer)
if not deal or deal:GetItemCount() ~= 1 then
    print("ERR:NO_EXACT_CHANNEL_PAYMENT")
    print("{SENTINEL}")
    return
end
local item = nil
for candidate in deal:Items() do
    item = candidate
end
if not item
        or item:GetFromPlayerID() ~= payer
        or item:GetType() ~= DealItemTypes.GOLD
        or (item:GetAmount() or 0) ~= {gold}
        or (item:GetDuration() or 0) ~= 0 then
    print("ERR:NO_EXACT_CHANNEL_PAYMENT")
    print("{SENTINEL}")
    return
end
{success_lua}
print("{SENTINEL}")
"""


def build_channel_payment_query(payer: int, gold: int) -> str:
    """Query one exact incoming payer-to-local-player payment offer."""
    payer, gold = _payment_inputs(payer, gold)
    return _build_exact_incoming_check(
        payer,
        gold,
        f'print("PAYMENT|{payer}|" .. me .. "|{gold}|0|1")',
    )


def build_channel_payment_state_query(payer: int, payee: int, gold: int) -> str:
    """Classify the ordered pair's pending deal from either involved seat."""
    payer, payee, gold = _payment_state_inputs(payer, payee, gold)
    return f"""
local me = Game.GetLocalPlayer()
local payer = {payer}
local payee = {payee}
local direction = nil
local other = nil
if me == payer then
    direction = DealDirection.OUTGOING
    other = payee
elseif me == payee then
    direction = DealDirection.INCOMING
    other = payer
else
    print("ERR:CHANNEL_PAYMENT_WRONG_SEAT")
    print("{SENTINEL}")
    return
end
if not DealManager.HasPendingDeal(payer, payee) then
    print("PAYMENT_STATE|{payer}|{payee}|{gold}|absent")
    print("{SENTINEL}")
    return
end
local deal = DealManager.GetWorkingDeal(direction, me, other)
if not deal or deal:GetItemCount() ~= 1 then
    print("PAYMENT_STATE|{payer}|{payee}|{gold}|conflicting")
    print("{SENTINEL}")
    return
end
local item = nil
for candidate in deal:Items() do
    item = candidate
end
if item
        and item:GetFromPlayerID() == payer
        and item:GetType() == DealItemTypes.GOLD
        and (item:GetAmount() or 0) == {gold}
        and (item:GetDuration() or 0) == 0 then
    print("PAYMENT_STATE|{payer}|{payee}|{gold}|exact|0|1")
else
    print("PAYMENT_STATE|{payer}|{payee}|{gold}|conflicting")
end
print("{SENTINEL}")
"""


def parse_channel_payment_query(lines: list[str]) -> ExactPaymentOffer | None:
    """Return only a canonical single-item lump-sum payment fingerprint."""
    payment_lines = [line for line in lines if line.startswith("PAYMENT|")]
    if len(payment_lines) != 1:
        return None
    parts = payment_lines[0].split("|")
    if len(parts) != 6:
        return None
    try:
        payer, payee, gold, duration, item_count = map(_int, parts[1:])
    except (TypeError, ValueError):
        return None
    if (
        not 0 <= payer <= 63
        or not 0 <= payee <= 63
        or payer == payee
        or not 1 <= gold <= 10_000
        or duration != 0
        or item_count != 1
    ):
        return None
    return ExactPaymentOffer(
        payer=payer,
        payee=payee,
        gold=gold,
        duration=duration,
        item_count=item_count,
    )


def parse_channel_payment_state_query(
    lines: list[str],
    *,
    payer: int,
    payee: int,
    gold: int,
) -> ChannelPaymentOfferState | None:
    """Parse one seat-bound absent/exact/conflicting classification."""
    payer, payee, gold = _payment_state_inputs(payer, payee, gold)
    state_lines = [line for line in lines if line.startswith("PAYMENT_STATE|")]
    if len(state_lines) != 1:
        return None
    parts = state_lines[0].split("|")
    if len(parts) not in {5, 7}:
        return None
    try:
        actual_payer, actual_payee, actual_gold = map(_int, parts[1:4])
        status = PaymentOfferStatus(parts[4])
    except (TypeError, ValueError):
        return None
    if (actual_payer, actual_payee, actual_gold) != (payer, payee, gold):
        return None
    if status is PaymentOfferStatus.EXACT:
        if len(parts) != 7:
            return None
        try:
            duration, item_count = map(_int, parts[5:])
        except (TypeError, ValueError):
            return None
        if duration != 0 or item_count != 1:
            return None
        return ChannelPaymentOfferState(
            status,
            ExactPaymentOffer(payer, payee, gold, duration, item_count),
        )
    if len(parts) != 5:
        return None
    return ChannelPaymentOfferState(status)


def build_channel_payment_response(payer: int, gold: int, accept: bool) -> str:
    """Accept/reject only after revalidating the exact linked payment offer."""
    payer, gold = _payment_inputs(payer, gold)
    if not isinstance(accept, bool):
        raise TypeError("accept must be a boolean")
    action = (
        "DealProposalAction.ACCEPTED" if accept else "DealProposalAction.REJECTED"
    )
    verb = "ACCEPTED" if accept else "REJECTED"
    return _build_exact_incoming_check(
        payer,
        gold,
        f"""DealManager.SendWorkingDeal({action}, me, payer)
print("OK:CHANNEL_PAYMENT_{verb}")""",
    )


__all__ = [
    "ChannelPaymentOfferState",
    "ExactPaymentOffer",
    "PaymentOfferStatus",
    "build_channel_payment_offer",
    "build_channel_payment_query",
    "build_channel_payment_response",
    "build_channel_payment_state_query",
    "parse_channel_payment_query",
    "parse_channel_payment_state_query",
]
