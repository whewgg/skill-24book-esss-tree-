# -*- coding: utf-8 -*-
"""make_indexes.py — 一键重建全部索引并自动验收。

原先要按 `references/maintenance.md` §1 的顺序手工跑各条 build 脚本 ＋ selftest，
顺序错了（尤其 03 依赖 02、05，06 依赖 05）会产出错索引还不自知。这里把依赖顺序
固化成一条命令，任何一步失败即非零退出、不再往下跑。

用法：
  python tools\\make_indexes.py        # 全量重建 + selftest 验收
"""
import subprocess
import sys

from _lib import ROOT  # noqa: E402

PY = sys.executable
# 每步超时上限。实测各步 1–20 s，1800 s 是"绝不可能正常耗时"的量级：
# **必须有超时**，否则某个生成脚本一旦挂住（死循环/文件锁），本脚本会永远等下去——
# 这正是"绕进去出不来"的形态：没有输出、没有退出码、看不出卡在哪一步。
STEP_TIMEOUT = 1800
CHAIN = [
    ("build_name_index.py", "02 人物字号索引"),
    ("build_volume_index.py", "05 卷名总表 ＋ 01 卷目速查"),
    ("build_person_mentions.py", "03 人物出现索引（依赖 02、05）"),
    ("build_story_index.py", "04 故事精选索引"),
    ("check_tables.py", "06 表卷与原件索引（依赖 05、机读稿目录）"),
    ("build_emperor_index.py", "07 帝王本纪索引（依赖 05）"),
    ("build_case_index.py", "08 案件索引（依赖 05 的书名/卷次表）"),
]


def main():
    tools = ROOT / "tools"
    for i, (script, desc) in enumerate(CHAIN, 1):
        print("[%d/%d] %s → %s" % (i, len(CHAIN) + 1, script, desc))
        try:
            r = subprocess.run([PY, str(tools / script)], cwd=str(ROOT), timeout=STEP_TIMEOUT)
        except subprocess.TimeoutExpired:
            print("⛔ %s 超过 %d 秒未结束，已中止——脚本可能挂住（死循环/等锁），"
                  "请单独跑它排查。" % (script, STEP_TIMEOUT))
            return 1
        if r.returncode != 0:
            print("⛔ %s 退出码 %d，中止——后续索引会基于错的产物。" % (script, r.returncode))
            return r.returncode
    print("[%d/%d] selftest.py → 全量验收" % (len(CHAIN) + 1, len(CHAIN) + 1))
    try:
        r = subprocess.run([PY, str(tools / "selftest.py")], cwd=str(ROOT), timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("⛔ selftest.py 超过 %d 秒未结束，已中止。" % STEP_TIMEOUT)
        return 1
    if r.returncode == 0:
        print("✅ 全部索引重建并验收通过（报告 tools/_selftest_report.txt）")
    else:
        print("⛔ 验收未通过（退出码 %d）——按 tools/_selftest_report.txt 修复" % r.returncode)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
