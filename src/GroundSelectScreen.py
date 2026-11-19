import json
import logging
from datetime import datetime, timedelta

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, TabbedContent, TabPane

from JSApi import JSApi, TokenExpiredError
from widgets.Table import TabelCell, TableRow

logger = logging.getLogger(__name__)


class GroundButton(Button):
    def __init__(self, variant="default", ground_info=None, preview: bool = False, *args, **kwargs):
        self.ground_info = ground_info
        self.selected = False
        self.preview = preview

        if preview:
            p = ground_info.get("price", "")
            label = f"待放¥{p}" if str(p) not in ("", "-1", "-1.0") else "待放场"
            super().__init__(label, variant, *args, **kwargs)
            self.add_class("preview")
        else:
            avail = str(ground_info.get("status")) == "1" and float(ground_info.get("price", -1)) >= 0
            if not avail:
                label = "不可订"
                super().__init__(label, variant, disabled=True, *args, **kwargs)
                self.add_class("booked")
            else:
                label = f"¥{float(ground_info['price']):g}"
                super().__init__(label, variant, *args, **kwargs)

    @on(Button.Pressed)
    def ground_button_pressed(self):
        if self.disabled:
            return
        self.selected = not self.selected
        if self.selected:
            self.add_class("selected")
        else:
            self.remove_class("selected")


class GroundTable(ScrollableContainer):
    def __init__(self, timestamp, id=None):
        super().__init__(id=id)
        self.timestamp = timestamp


class GroundSelectScreen(ModalScreen):
    class LoggedStatusChanged(Message):
        def __init__(self, sender: "GroundSelectScreen", logged_status: bool) -> None:
            self.sender = sender
            self.logged_status = logged_status
            super().__init__()

        @property
        def control(self) -> "GroundSelectScreen":
            return self.sender

    def __init__(self, *children, name=None, id=None, classes=None):
        super().__init__(*children, name=name, id=id, classes=classes)
        self.js_api: JSApi = self.app.js_api
        self._template_rows = None

    def timestamp_to_label(self, timestamp: int):
        dt = datetime.fromtimestamp(timestamp / 1000)
        date_str = f"{dt.month:02}-{dt.day:02}"
        if dt.date() == datetime.today().date():
            weekday_str = "今天"
        else:
            weekday_str = {
                1: "星期一",
                2: "星期二",
                3: "星期三",
                4: "星期四",
                5: "星期五",
                6: "星期六",
                7: "星期日",
            }[dt.isoweekday()]
        return f"{date_str}\n{weekday_str}"

    def compose(self) -> ComposeResult:
        with Vertical(id="ground-select-container"):
            with TabbedContent(id="ground-tabs"):
                timestamps = self.js_api.get_timestamps_from_now(days=8)
                for timestamp in timestamps:
                    tab_label = self.timestamp_to_label(timestamp)
                    with TabPane(tab_label, id=f"ground-tab-{timestamp}"):
                        yield Vertical(id="ground-time-container")
                        yield GroundTable(timestamp, id=f"ground-table-{timestamp}")

            with Horizontal(id="ground-buttons-container"):
                yield Button("确定", id="ground-confirm-button")
                yield Button("取消", id="ground-cancel-button")

    @on(Button.Pressed, "#ground-confirm-button")
    def return_grounds_selected(self) -> None:
        selected_grounds = []
        for button in self.query(GroundButton):
            if button.selected:
                info = button.ground_info
                selected_grounds.append(
                    {
                        "groundId": str(info["groundId"]),
                        "groundName": info["groundName"],
                        "startTime": str(info["start_time"]),
                        "endTime": str(info["end_time"]),
                        "sportsType": str(info.get("sportsType", "11")),
                    }
                )
        selected_grounds.sort(key=lambda x: int(x["startTime"]))
        if not selected_grounds:
            logger.warning("未选择任何场地")
        else:
            desc = ", ".join(
                f"{g['groundName']} {datetime.fromtimestamp(int(g['startTime'])/1000):%m-%d %H:%M}"
                for g in selected_grounds
            )
            logger.error(f"已选择场地: {desc}")
        self.dismiss(selected_grounds)

    @on(Button.Pressed, "#ground-cancel-button")
    def cancel(self) -> None:
        self.dismiss(None)

    @work
    @on(TabbedContent.TabActivated)
    async def show_grounds_info(self, event: TabbedContent.TabActivated) -> None:
        table: GroundTable = event.pane.query_one(GroundTable)
        table.remove_children()

        time_container: Vertical = event.pane.query_one("#ground-time-container")
        time_container.remove_children()

        venue_select: Select = self.app.query_one("#ground-venue-select")
        if venue_select.is_blank() or not isinstance(venue_select.value, str):
            table.mount(Label("请先在主界面选择场馆"))
            return
        venue_info = json.loads(venue_select.value)
        venue_id = venue_info["venue_id"]

        dt_starts, ground_dict, is_preview = await self.get_ground_dict(venue_id, table.timestamp)
        if not dt_starts or not ground_dict:
            table.mount(Label("当前日期暂无可显示时段"))
            return

        ground_name_labels = [
            Label(ground_name, classes="ground-name")
            for ground_name in ground_dict.keys()
        ]
        table.mount(TableRow(*ground_name_labels, id="ground-name-row"))

        button_containers: dict[datetime, dict[str, TabelCell]] = {}
        for dt_start in dt_starts:
            button_containers.setdefault(dt_start, {})
            time_container.mount(Label(dt_start.strftime("%H:%M"), classes="ground-time"))
            row = TableRow()
            table.mount(row)
            for ground_name in ground_dict.keys():
                button_container = TabelCell()
                button_containers[dt_start][ground_name] = button_container
                row.mount(button_container)

        for ground_name, grounds in ground_dict.items():
            for ground in grounds:
                dt_start = datetime.fromtimestamp(ground["start_time"] / 1000)
                cell = button_containers.get(dt_start, {}).get(ground_name)
                if cell is not None:
                    cell.mount(GroundButton(ground_info=ground, preview=is_preview))

    async def get_ground_dict(self, venue_id, timestamp):
        grounds_dict = {}
        dt_starts = []

        try:
            ground_infos = await self.js_api.get_ground(venue_id, timestamp)
        except TokenExpiredError:
            logger.error("Token 已过期，请重新登录")
            self.post_message(self.LoggedStatusChanged(self, False))
            return dt_starts, grounds_dict, False
        except Exception as e:
            logger.error("获取场地信息失败")
            logger.debug(e, exc_info=True)
            return dt_starts, grounds_dict, False

        if ground_infos:
            self._template_rows = ground_infos
            is_preview = False
        else:
            is_preview = True
            if not self._template_rows:
                tomorrow_ts = self.js_api.get_timestamps_from_now(days=8)[1]
                try:
                    self._template_rows = await self.js_api.get_ground(venue_id, tomorrow_ts)
                except Exception:
                    self._template_rows = []
            if self._template_rows:
                tpl_day = datetime.fromtimestamp(int(self._template_rows[0]["startTime"]) / 1000).date()
                target_day = datetime.fromtimestamp(timestamp / 1000).date()
                delta_ms = (target_day - tpl_day).days * 86400 * 1000
                ground_infos = []
                for row in self._template_rows:
                    copied = dict(row)
                    copied["startTime"] = str(int(row["startTime"]) + delta_ms)
                    copied["endTime"] = str(int(row["endTime"]) + delta_ms)
                    copied["blockModel"] = [dict(b) for b in row.get("blockModel", [])]
                    ground_infos.append(copied)

        now = datetime.now()
        for info in ground_infos or []:
            start_time = int(info["startTime"])
            end_time = int(info["endTime"])
            dt_start = datetime.fromtimestamp(start_time / 1000)
            if not is_preview and dt_start < now:
                continue
            dt_starts.append(dt_start)
            for ground in info["blockModel"]:
                g_copy = dict(ground)
                g_copy["start_time"] = start_time
                g_copy["end_time"] = end_time
                grounds_dict.setdefault(g_copy["groundName"], []).append(g_copy)
        return dt_starts, grounds_dict, is_preview
