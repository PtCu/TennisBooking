"""Jiushi official order verification and read-only payment tracking."""
import asyncio
import json
import logging
import hashlib
import re
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from phone_notify import optional_notifier, NotificationError
from storage import DATA_DIR, write_json

logger = logging.getLogger(__name__)
SHANGHAI = timezone(timedelta(hours=8))
PENDING = {"CREATED", "PAY_WAIT"}
FINAL = {"PAY_SUCCESS", "ORDER_CLOSED", "PAY_CANCEL", "REFUNDING", "ORDER_REFUND_PART", "ORDER_REFUND_ALL", "ORDER_REFUND_FAILED"}


class OrderReviewError(Exception):
    pass


def timestamp(value):
    return datetime.fromtimestamp(int(value) / 1000, SHANGHAI)


def verify_detail(value, record):
    if not isinstance(value, dict) or not isinstance(value.get("orderDetailModel"), dict):
        raise OrderReviewError("官方订单详情结构变化，请到官方核对；未发送付款提醒")
    model = value["orderDetailModel"]
    target = record.get("target")
    venue_id=target.get("venue_id","20") if isinstance(target,dict) else "20"
    if str(model.get("orderId")) != str(record["order_id"]) or str(model.get("venueId")) != str(venue_id):
        raise OrderReviewError("官方订单号或场馆与提交记录不符，已停止提醒")
    if not isinstance(target, dict) or not target.get("slots"):
        raise OrderReviewError("缺少原始场次核对记录，请到久事官方核对该订单")
    slots = value.get("venueBookGroundModelList")
    def detail_time(value):
        if isinstance(value,str) and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",value):
            return datetime.strptime(value,"%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
        return timestamp(value)
    def slot_matches(actual,wanted):
        expected_id=str(wanted["groundId"])
        actual_id=str(actual["groundId"])
        name=wanted.get("groundName")
        same_court=actual_id==expected_id
        if not same_court and name and actual_id==name and actual.get("groundName")==name:
            same_court=True
        if name and actual.get("groundName") and actual["groundName"]!=name:
            return False
        return same_court and all(detail_time(actual[k])==detail_time(wanted[k]) for k in ("startTime","endTime"))
    try:
        if not isinstance(slots,list) or len(slots)!=len(target["slots"]):
            raise ValueError()
        remaining=list(target["slots"])
        for item in slots:
            matches=[index for index,wanted in enumerate(remaining) if slot_matches(item,wanted)]
            if len(matches)!=1:
                raise ValueError()
            remaining.pop(matches[0])
        amount = Decimal(str(model["payAmount"])) / 100
        expected = Decimal(target["amount"])
        budget = Decimal(target["budget"])
        if not amount.is_finite() or not expected.is_finite() or not budget.is_finite() or amount != expected or amount > budget or amount < 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError, InvalidOperation):
        raise OrderReviewError("官方订单时段或金额与提交记录不符，已停止付款提醒，请人工核对") from None
    status = model.get("payStatus")
    if status not in PENDING | FINAL:
        raise OrderReviewError("官方订单状态尚未识别，请到官方核对；未发送付款提醒")
    remaining, deadline = None, None
    if status in PENDING and model.get("orderPayMaxTime") is not None:
        deadline = timestamp(model["orderPayMaxTime"])
        if model.get("currentTime") is None:
            raise OrderReviewError("订单服务器时间缺失，无法核对付款期限，请到官方查看")
        remaining = (deadline - timestamp(model["currentTime"])).total_seconds()
    return status, amount, remaining, deadline


def update_record(path, **changes):
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(changes, updated=datetime.now(SHANGHAI).isoformat())
    write_json(path, value)
    return value


@contextmanager
def order_lock(path):
    import msvcrt
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise OrderReviewError("另一个任务正在提交或核对订单，请先停止它") from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


async def order_snapshot(api):
    orders, total = {}, None
    for page in range(1, 6):
        value = await api.get_order_data("/order/v2-get/getOrderList", {"page": page, "pageSize": 20})
        items = value.get("orderDetailModelList") if isinstance(value, dict) else None
        count = value.get("totalNum") if isinstance(value, dict) else None
        if not isinstance(items, list) or type(count) is not int or not 0 <= count <= 100:
            raise OrderReviewError("无法完整读取官方订单列表，请到久事核对")
        if total is None:
            total = count
        if total != count:
            raise OrderReviewError("官方订单列表正在变化，请稍后重试")
        for item in items:
            oid = str(item.get("orderId", "")) if isinstance(item, dict) else ""
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", oid) or oid in orders:
                raise OrderReviewError("官方订单列表格式变化，请到久事核对")
            orders[oid] = item
        if len(orders) == total:
            return orders
        if len(orders) > total or not items:
            break
    raise OrderReviewError("官方订单列表未读完整，请到久事核对")


async def matching_orders(api, orders, record, candidates):
    matches = []
    for oid in candidates:
        if str(orders[oid].get("venueId")) != str(record["target"]["venue_id"]):
            continue
        detail = await api.get_order_data("/venue/v2-get/getVenueOrderDetail", {"orderId": oid})
        try:
            verify_detail(detail, dict(record, order_id=oid))
        except OrderReviewError:
            continue
        matches.append(oid)
    return matches


async def reconcile(api, path, *, attempts=6, interval=2):
    record = json.loads(path.read_text(encoding="utf-8"))
    if not record.get("target") or "baseline_orders" not in record:
        raise OrderReviewError("旧提交记录缺少场次信息，请直接到久事订单核对")
    for attempt in range(attempts):
        current = await order_snapshot(api)
        matches = await matching_orders(api, current, record, current.keys() - set(record["baseline_orders"]))
        if len(matches) > 1:
            raise OrderReviewError("发现多笔匹配订单，请直接到久事核对")
        if matches:
            update_record(path, state="payment-pending", order_id=matches[0])
            return matches[0]
        if attempt + 1 < attempts:
            await asyncio.sleep(interval)
    update_record(path, state="requires-official-check")
    raise OrderReviewError("尚未核对到匹配订单，请查看久事官方订单，稍后可继续核对")


async def review_payment(api, path, *, notifier=None):
    record = json.loads(path.read_text(encoding="utf-8"))
    oid = record.get("order_id")
    if not isinstance(oid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", oid):
        raise OrderReviewError("记录中缺少有效订单号，请先核对订单")
    detail = await api.get_order_data("/venue/v2-get/getVenueOrderDetail", {"orderId": oid})
    status, amount, remaining, deadline = verify_detail(detail, record)
    update_record(path, official_status=status, payment_deadline=deadline.isoformat() if deadline else None)
    if status not in PENDING:
        state = "payment-success" if status == "PAY_SUCCESS" else "payment-ended"
        update_record(path, state=state)
        return False, "官方已确认付款成功" if state == "payment-success" else "官方订单已结束"
    if remaining is not None and remaining <= 0:
        return False, "官方付款期限已到，请打开久事查看最终订单状态"
    update_record(path, state="payment-pending")
    label = deadline.strftime("%m-%d %H:%M:%S") if deadline else "请在官方订单查看"
    message = f"订单已核对，待付款 {amount:g} 元，截止 {label}"
    notice = record.get("notification", {})
    attempts = notice.get("attempts", 0)
    if type(attempts) is not int or not 0 <= attempts <= 3:
        raise OrderReviewError("提醒记录格式异常，请在官方核对订单")
    now = datetime.now().timestamp()
    if notifier and not notice.get("accepted") and attempts < 3 and now - notice.get("last_attempt", 0) >= 30:
        notice = {"attempts": attempts + 1, "last_attempt": now, "accepted": False}
        update_record(path, notification=notice)
        try:
            await asyncio.to_thread(notifier.send, "久事预约成功 · 请付款", message + "\n使用下单账号打开久事体育 → 我的订单 → 待支付，核对后付款。")
        except NotificationError:
            logger.warning("微信提醒暂未发送成功，将稍后重试")
        else:
            notice["accepted"] = True
            update_record(path, notification=notice)
            logger.info("微信提醒服务已接受消息")
    return True, message


async def track_payment(api, path, *, interval=15, checks=120, on_update=None):
    try:
        notifier = optional_notifier(path.parent.parent)
    except (NotificationError, OSError):
        notifier = None
        logger.warning("微信提醒配置不可用，请在顶部重新设置")
    with order_lock(path.with_suffix(".tracking.lock")):
        for check in range(checks):
            pending, message = await review_payment(api, path, notifier=notifier)
            logger.info(message)
            if on_update:
                on_update(message)
            if not pending:
                return message
            if check + 1 < checks:
                await asyncio.sleep(interval)
    return "本轮跟踪已结束，可继续跟踪付款"


def trackable_records(directory=DATA_DIR):
    records = []
    for path in (directory / "orders").glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("state") in {"submitting", "submitted", "requires-official-check", "payment-pending"}:
            records.append((path, value))
    return sorted(records, key=lambda item: item[0].stat().st_mtime, reverse=True)


async def submit_selection(api, request, venue, contact_id, plan, slots, directory=DATA_DIR):
    from booking_flow import flatten_ground_infos, selection_summary

    directory.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps([str(venue["venue_id"]), str(contact_id), request.candidate_slots]).encode()).hexdigest()
    path = directory / "orders" / f"{fingerprint}.json"
    with order_lock(directory / "submission.lock"):
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("state") in {"payment-success", "payment-ended"}:
                raise OrderReviewError("此任务已有已核对订单，请在订单记录查看")
            if not record.get("order_id"):
                await reconcile(api, path)
            return path
        record = {"state": "submitting", "summary": selection_summary(plan, slots), "target": {
            "venue_id": str(venue["venue_id"]), "venue_name": venue["venue_name"],
            "slots": [slot.to_payload() for slot in slots],
            "amount": str(sum((slot.price for slot in slots), Decimal(0))), "budget": str(request.max_total),
        }}
        baseline = await order_snapshot(api)
        pending = {oid for oid, item in baseline.items() if item.get("payStatus") in PENDING}
        existing = await matching_orders(api, baseline, record, pending)
        if existing:
            raise OrderReviewError("官方已有相同场次待支付订单，请先在久事付款")
        day = slots[0].start.replace(hour=0, minute=0, second=0, microsecond=0)
        if day.date() not in await api.get_open_dates(venue["venue_id"]):
            raise OrderReviewError("目标日期尚未开放，请继续守候")
        fresh = flatten_ground_infos(await api.get_ground(venue["venue_id"], int(day.timestamp() * 1000)))
        available = {slot.key: slot for slot in fresh if slot.available}
        if any(slot.key not in available or available[slot.key].price != slot.price for slot in slots):
            raise OrderReviewError("提交前库存或价格已变化，请重新查询候选")
        if any(slot.start <= datetime.now() for slot in slots):
            raise OrderReviewError("目标场次已经开始，请重新选择")
        record["baseline_orders"] = sorted(baseline)
        write_json(path, record)
        try:
            await api.post_book(contact_id=contact_id, venue_id=venue["venue_id"], venue_name=venue["venue_name"],
                                agency_id=venue["agency_id"], agency_name=venue["agency_name"],
                                sports_name=venue["sports_name"], ground_list=record["target"]["slots"])
        except Exception:
            logger.warning("提交结果待核对，正在只读查询官方订单")
        await reconcile(api, path)
        return path

