from tools.read_file_demo import read_file


def test_read_existing_file():
    result = read_file("example.txt")

    assert result == {
        "ok": True,
        "content": "这是练习文件\n",
    }

def test_missing_file():
    result = read_file("missing.txt")

    assert result == {
        "ok": False,
        "error": "文件不存在",
    }
def test_outside_workspace():
    result = read_file("../tasks.json")

    assert result == {
        "ok": False,
        "error": "路径越界，拒绝访问",
    }