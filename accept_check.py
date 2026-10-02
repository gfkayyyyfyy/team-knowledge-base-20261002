"""show 正文异常约定的验收测试。"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KB_SRC = ROOT  # 包所在目录


def run(kb_dir, *args):
    return subprocess.run(
        [sys.executable, "-m", "knowledge_base", "--root", str(kb_dir), *args],
        capture_output=True, cwd=ROOT,
    )


def make_kb(base):
    """建一个含两个修订版本的文档：v1 中文 Markdown+空行+末尾换行，v2 不同正文。"""
    kb = base / "kb"
    kb.mkdir(parents=True)
    v1 = "# 标题\n\n中文正文第一段。\n\n- 列表项\n"
    v2 = "第二版正文 different body\n"
    (base / "v1.md").write_text(v1, encoding="utf-8")
    (base / "v2.md").write_text(v2, encoding="utf-8")
    r = run(kb, "add", "--title", "验收文档", "--file", str(base / "v1.md"))
    assert r.returncode == 0, r.stderr
    doc_id = json.loads(r.stdout)["id"]
    r = run(kb, "update", str(doc_id), "--title", "验收文档v2", "--file", str(base / "v2.md"))
    assert r.returncode == 0, r.stderr
    return kb, doc_id, v1.encode("utf-8"), v2.encode("utf-8")


def snapshot(kb):
    return sorted(
        (str(p.relative_to(kb)), p.read_bytes() if p.is_file() else b"<dir>")
        for p in kb.rglob("*")
    )


def check_fail(result, doc_id, version, reason, label):
    assert result.returncode == 2, f"{label}: 退出码 {result.returncode}"
    assert result.stdout == b"", f"{label}: 标准输出应为空, got {result.stdout!r}"
    err = result.stderr.decode("utf-8")
    assert "Traceback" not in err, f"{label}: 不应有堆栈\n{err}"
    assert str(doc_id) in err and str(version) in err, f"{label}: 应含文档与版本号\n{err}"
    assert reason in err, f"{label}: 应说明原因 {reason}\n{err}"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="kb_accept_"))
    try:
        kb, doc_id, v1, v2 = make_kb(tmp / "healthy")

        # 健康基线：默认读最新、--version 读历史
        r = run(kb, "show", str(doc_id))
        assert r.returncode == 0 and r.stdout == v2, r
        r = run(kb, "show", str(doc_id), "--version", "1")
        assert r.returncode == 0 and r.stdout == v1, r
        # 既有错误类别不变
        r = run(kb, "show", "999")
        assert r.returncode == 2 and r.stdout == b"" and "文档不存在" in r.stderr.decode()
        r = run(kb, "show", str(doc_id), "--version", "99")
        assert r.returncode == 2 and r.stdout == b"" and "版本不存在" in r.stderr.decode()

        body_v1 = None  # 各副本中定位

        # 场景一：版本 1 正文缺失 / 变目录 / 非法 UTF-8
        for i, corrupt in enumerate(["missing", "dir", "badutf8"]):
            kb2, did, _, v2b = make_kb(tmp / f"v1_{corrupt}")
            assert did == doc_id
            body_v1 = kb2 / "bodies" / str(did) / "v1.md"
            if corrupt == "missing":
                body_v1.unlink()
                reason = "缺失或不是普通文件"
            elif corrupt == "dir":
                body_v1.unlink()
                body_v1.mkdir()
                reason = "缺失或不是普通文件"
            else:
                body_v1.write_bytes(b"\xff\xfe invalid \x80")
                reason = "UTF-8"
            before = snapshot(kb2)
            r = run(kb2, "show", str(did), "--version", "1")
            check_fail(r, did, 1, reason, f"v1 {corrupt}")
            # 最新版本不受影响
            r = run(kb2, "show", str(did))
            assert r.returncode == 0 and r.stdout == v2b, f"v1 {corrupt}: 最新版本应正常"
            # 失败读取不改变知识库内容
            after = snapshot(kb2)
            assert before == after, f"v1 {corrupt}: 失败读取改变了知识库"
            # history 仍正常
            r = run(kb2, "history", str(did))
            assert r.returncode == 0 and len(json.loads(r.stdout)) == 2

        # 场景二：最新版本（v2）正文异常，默认读取按约定失败，历史版本不受影响
        for corrupt in ["missing", "dir", "badutf8"]:
            kb3, did, v1b, _ = make_kb(tmp / f"v2_{corrupt}")
            body_v2 = kb3 / "bodies" / str(did) / "v2.md"
            if corrupt == "missing":
                body_v2.unlink()
                reason = "缺失或不是普通文件"
            elif corrupt == "dir":
                body_v2.unlink()
                body_v2.mkdir()
                reason = "缺失或不是普通文件"
            else:
                body_v2.write_bytes(b"\xc3\x28 bad")
                reason = "UTF-8"
            before = snapshot(kb3)
            r = run(kb3, "show", str(did))
            check_fail(r, did, 2, reason, f"v2 {corrupt}")
            # 显式 --version 2 同样失败，且不回退
            r = run(kb3, "show", str(did), "--version", "2")
            check_fail(r, did, 2, reason, f"v2 {corrupt} explicit")
            # 历史版本 1 不受影响
            r = run(kb3, "show", str(did), "--version", "1")
            assert r.returncode == 0 and r.stdout == v1b, f"v2 {corrupt}: v1 应正常"
            assert before == snapshot(kb3), f"v2 {corrupt}: 失败读取改变了知识库"

        # 场景三：合法正文原样输出（空、中文、CRLF、无末尾换行）
        cases = {
            "empty": b"",
            "crlf": "第一行\r\n第二行\r\n".encode("utf-8"),
            "no_trailing_nl": "没有末尾换行".encode("utf-8"),
        }
        for name, content in cases.items():
            kb4 = tmp / f"valid_{name}" / "kb"
            kb4.mkdir(parents=True)
            f = kb4.parent / "body.md"
            f.write_bytes(content)
            r = run(kb4, "add", "--title", "合法正文", "--file", str(f))
            assert r.returncode == 0, r.stderr
            did = json.loads(r.stdout)["id"]
            r = run(kb4, "show", str(did))
            assert r.returncode == 0 and r.stdout == content, f"{name}: {r.stdout!r}"

        print("全部验收场景通过")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
