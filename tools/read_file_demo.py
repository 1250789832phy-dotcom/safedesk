from pathlib import Path


def read_file(relative_path):
    root = Path("workspace").resolve()
    target = (root / relative_path).resolve()

    if root not in target.parents:
        return {
            "ok": False,
            "error": "路径越界，拒绝访问",
        }

    if not target.exists():
        return {
            "ok": False,
            "error": "文件不存在",
        }

    return {
        "ok": True,
        "content": target.read_text(encoding="utf-8-sig"),
    }


if __name__ == "__main__":
    print(read_file("example.txt"))