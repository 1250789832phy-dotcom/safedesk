from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app


class PageContent(HTMLParser):
    def __init__(self):
        super().__init__()
        self.language = None
        self.attributes = {}
        self.text = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.language = attributes.get("lang")
        if "id" in attributes:
            self.attributes[attributes["id"]] = attributes

    def handle_data(self, data):
        self.text.append(data)


@pytest.fixture
def page():
    response = TestClient(create_app()).get("/")
    assert response.status_code == 200
    parsed = PageContent()
    parsed.feed(response.text)
    return parsed


def test_workbench_declares_simplified_chinese(page):
    assert page.language == "zh-CN"


@pytest.mark.parametrize(
    "label",
    [
        "本地智能体工作台",
        "新建任务",
        "任务内容",
        "语音输入",
        "停止",
        "取消",
        "清空",
        "输出方式",
        "文字",
        "语音",
        "文字 + 语音",
        "运行任务",
        "最近任务",
        "刷新",
        "执行情况",
        "状态：",
        "空闲",
        "尚未选择任务",
        "执行计划",
        "暂无执行计划。",
        "执行记录",
        "暂无工具调用。",
        "任务报告",
        "暂无任务报告。",
    ],
)
def test_workbench_uses_chinese_labels(page, label):
    assert label in page.text


def test_workbench_keeps_existing_control_contract(page):
    assert page.attributes["task"]["placeholder"] == "读取 example.txt"
    assert page.attributes["clear"]["title"] == "清空任务内容"
    assert page.attributes["task"]["maxlength"] == "4000"
    for control in (
        "record", "stop-record", "cancel-record", "submit", "refresh",
        "output", "taskList", "status", "taskId", "plan", "trace", "report",
    ):
        assert control in page.attributes
