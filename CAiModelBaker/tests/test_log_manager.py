"""quant_engine_log 크기 상한(회전·tail 복사) 가드."""
import os

from CAiModelBaker.core.log_manager import append_with_rotation, copy_tail


def test_rotation_caps_total_size(tmp_path):
    p = str(tmp_path / "logs" / "q.txt")
    for _ in range(50):
        append_with_rotation(p, "x" * 1000 + "\n", max_bytes=10_000, backup_count=3)
    files = [f for f in os.listdir(tmp_path / "logs")]
    # 회전 직후에는 활성 파일이 없을 수 있다 — 백업은 최대 3개, .4 이상은 존재하지 않아야 한다.
    assert set(files) <= {"q.txt", "q.txt.1", "q.txt.2", "q.txt.3"}
    assert "q.txt.3" in files
    total = sum(os.path.getsize(tmp_path / "logs" / f) for f in files)
    assert total <= 4 * (10_000 + 1001)  # 파일당 max_bytes + 1회 기록분


def test_rotation_keeps_newest_in_active_or_first_backup(tmp_path):
    p = str(tmp_path / "q.txt")
    for i in range(30):
        append_with_rotation(p, f"line{i:03d}\n" + "x" * 500 + "\n", max_bytes=2_000, backup_count=2)
    newest = open(p, encoding="utf-8").read() + open(p + ".1", encoding="utf-8").read()
    assert "line029" in newest
    assert not os.path.exists(p + ".3")


def test_copy_tail_limits_size_and_aligns_lines(tmp_path):
    src, dst = str(tmp_path / "s.log"), str(tmp_path / "d.log")
    with open(src, "w", encoding="utf-8") as f:
        for i in range(5000):
            f.write(f"row-{i:05d}\n")
    n = copy_tail(src, dst, max_bytes=1000)
    out = open(dst, encoding="utf-8").read().splitlines()
    assert n <= 1000
    assert out[0].startswith("[... 앞부분")
    assert out[-1] == "row-04999"
    assert all(l.startswith("row-") for l in out[1:])


def test_copy_tail_small_file_copied_whole(tmp_path):
    src, dst = str(tmp_path / "s.log"), str(tmp_path / "d.log")
    open(src, "w", encoding="utf-8").write("a\nb\n")
    copy_tail(src, dst, max_bytes=1000)
    assert open(dst, encoding="utf-8").read() == "a\nb\n"
