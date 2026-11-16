"""传统表单订场流程的 Request、Plan 和库存选择逻辑。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable
from bisect import bisect_left


PLAN_TYPES = {"single_slot", "contiguous_bundle", "split_bundle"}


@dataclass(frozen=True)
class Plan:
    """一套完整的订场策略。方案按 Request 中的顺序尝试。"""

    kind: str
    count: int
    name: str = ""

    def validate(self) -> "Plan":
        if self.kind not in PLAN_TYPES:
            raise ValueError("方案类型无效")
        if type(self.count) is not int or not 1 <= self.count <= 4:
            raise ValueError("每个方案的场次数量须为1～4段")
        if self.kind == "single_slot" and self.count != 1:
            raise ValueError("单场方案只能选择1段")
        return self


@dataclass(frozen=True)
class CandidateSlot:
    ground_id: str
    ground_name: str
    start_time: str
    end_time: str
    sports_type: str
    price: Decimal
    available: bool

    @property
    def key(self) -> tuple[str, str, str]:
        return self.ground_id, self.start_time, self.end_time

    @property
    def start(self) -> datetime:
        return datetime.fromtimestamp(int(self.start_time) / 1000)

    @property
    def end(self) -> datetime:
        return datetime.fromtimestamp(int(self.end_time) / 1000)

    def to_payload(self) -> dict[str, str]:
        return {
            "groundId": self.ground_id,
            "groundName": self.ground_name,
            "startTime": self.start_time,
            "endTime": self.end_time,
            "sportsType": self.sports_type,
        }


@dataclass(frozen=True)
class BookingRequest:
    """一次订场意图，包含候选场次和有序方案。"""

    candidate_slots: tuple[tuple[str, str, str], ...]
    plans: tuple[Plan, ...]
    max_total: Decimal

    def validate(self) -> "BookingRequest":
        if not self.candidate_slots:
            raise ValueError("请先选择候选场次")
        dates = {
            datetime.fromtimestamp(int(key[1]) / 1000).date()
            for key in self.candidate_slots
        }
        if len(dates) != 1:
            raise ValueError("候选场次必须来自同一天")
        if not self.plans:
            raise ValueError("请至少配置一个订场方案")
        try:
            maximum = Decimal(str(self.max_total))
        except (InvalidOperation, ValueError, TypeError):
            raise ValueError("总预算格式无效") from None
        if not maximum.is_finite() or maximum <= 0:
            raise ValueError("总预算必须大于0")
        for plan in self.plans:
            plan.validate()
        return self


def _value(item: dict, *names: str, default=None):
    for name in names:
        if name in item:
            return item[name]
    return default


def candidate_keys(items: Iterable[dict]) -> tuple[tuple[str, str, str], ...]:
    keys = {
        (
            str(_value(item, "groundId", "ground_id")),
            str(_value(item, "startTime", "start_time")),
            str(_value(item, "endTime", "end_time")),
        )
        for item in items
    }
    return tuple(sorted(keys, key=lambda key: (int(key[1]), key[0], int(key[2]))))


def flatten_ground_infos(ground_infos: Iterable[dict]) -> tuple[CandidateSlot, ...]:
    """将官方 statusList 展平成可匹配的 CandidateSlot。"""

    result: list[CandidateSlot] = []
    for row in ground_infos:
        start_time = str(row["startTime"])
        end_time = str(row["endTime"])
        for ground in row.get("blockModel", []):
            try:
                price = Decimal(str(ground["price"]))
            except (KeyError, InvalidOperation, TypeError):
                continue
            result.append(
                CandidateSlot(
                    ground_id=str(ground["groundId"]),
                    ground_name=str(ground["groundName"]),
                    start_time=start_time,
                    end_time=end_time,
                    sports_type=str(ground["sportsType"]),
                    price=price,
                    available=price.is_finite() and str(ground.get("status")) == "1" and price >= 0,
                )
            )
    return tuple(result)


def _sort_key(slot: CandidateSlot) -> tuple[int, str, int]:
    return int(slot.start_time), slot.ground_name, int(slot.end_time)


def _within_budget(slots: Iterable[CandidateSlot], maximum: Decimal) -> bool:
    return sum((slot.price for slot in slots), Decimal(0)) <= Decimal(str(maximum))


def _select_contiguous(slots: list[CandidateSlot], count: int, maximum: Decimal) -> tuple[CandidateSlot, ...]:
    by_ground: dict[str, list[CandidateSlot]] = {}
    for slot in slots:
        by_ground.setdefault(slot.ground_id, []).append(slot)

    groups = sorted(by_ground.values(), key=lambda group: (_sort_key(group[0]), group[0].ground_name))
    for group in groups:
        group.sort(key=_sort_key)
        for start in range(0, len(group) - count + 1):
            window = group[start : start + count]
            if all(left.end == right.start for left, right in zip(window, window[1:])) and _within_budget(window, maximum):
                return tuple(window)
    return ()


def _select_split(slots: list[CandidateSlot], count: int, maximum: Decimal) -> tuple[CandidateSlot, ...]:
    """Find the earliest affordable non-overlapping selection in O(count * slots)."""
    starts = [int(slot.start_time) for slot in slots]
    following = [bisect_left(starts, int(slot.end_time), lo=index + 1) for index, slot in enumerate(slots)]
    infinity = Decimal("Infinity")
    minimum = [[Decimal(0)] * (len(slots) + 1)]
    for remaining in range(1, count + 1):
        costs = [infinity] * (len(slots) + 1)
        for index in range(len(slots) - 1, -1, -1):
            costs[index] = min(costs[index + 1], slots[index].price + minimum[remaining - 1][following[index]])
        minimum.append(costs)
    if minimum[count][0] > maximum:
        return ()
    selected = []
    index = 0
    for remaining in range(count, 0, -1):
        while index < len(slots):
            slot = slots[index]
            next_index = following[index]
            if slot.price + minimum[remaining - 1][next_index] <= maximum:
                selected.append(slot)
                maximum -= slot.price
                index = next_index
                break
            index += 1
    return tuple(selected)


def select_plan(
    request: BookingRequest,
    plan: Plan,
    available_slots: Iterable[CandidateSlot],
) -> tuple[CandidateSlot, ...]:
    """按一个 Plan 从实时库存中选出具体场次。"""

    request.validate()
    plan.validate()
    allowed = set(request.candidate_slots)
    slots = sorted(
        (
            slot
            for slot in available_slots
            if slot.available and slot.key in allowed
        ),
        key=_sort_key,
    )

    if plan.kind == "single_slot":
        selected = next(((slot,) for slot in slots if _within_budget((slot,), request.max_total)), ())
    elif plan.kind == "contiguous_bundle":
        selected = _select_contiguous(slots, plan.count, request.max_total)
    else:
        selected = _select_split(slots, plan.count, Decimal(str(request.max_total)))

    if len(selected) != plan.count or not _within_budget(selected, request.max_total):
        return ()
    return selected


def choose_plan(
    request: BookingRequest,
    available_slots: Iterable[CandidateSlot],
) -> tuple[Plan, tuple[CandidateSlot, ...]] | None:
    """按优先级返回第一个可执行方案。"""

    request.validate()
    available = tuple(available_slots)
    for plan in request.plans:
        selected = select_plan(request, plan, available)
        if selected:
            return plan, selected
    return None


def selection_summary(plan: Plan, slots: Iterable[CandidateSlot]) -> str:
    selected = tuple(slots)
    total = sum((slot.price for slot in selected), Decimal(0))
    details = "、".join(
        f"{slot.ground_name} {slot.start:%H:%M}-{slot.end:%H:%M}"
        for slot in selected
    )
    return f"{plan.name or plan.kind}：{details}，共{total:g}元"

