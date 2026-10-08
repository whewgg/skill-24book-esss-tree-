"""make_story_pool.py — 生成「讲史选签表」（内置进 SKILL.md）与候选池（tools/_story_pool.md）。

用途：给**无执行能力／零工具**的环境准备一份可直接取用的签表——模型只需读 SKILL.md
      即可按「用户消息字数 mod 条数」机械取号开讲，不必跑脚本、不必读 data/。

设计要点：
  1. 每部书各抽 1 条（27 部 → 27 条），覆盖全库、杜绝"总讲通鉴/史记"的偏食；
  2. 优先叙事类（篇名含 传/世家/纪），兜底也排除 志/表（历志、艺文志不是"故事"）；
  3. 剔除最著名桥段（完璧归赵、鸿门宴…）与译注行、目录行、承接词开头（故事不完整）；
  4. 固定种子 —— 同一语料重跑得到同一张表（可复现）；改 SEED 即换一批。

用法：python tools/make_story_pool.py           # 生成并写回 SKILL.md 与 _story_pool.md
      python tools/make_story_pool.py --dry      # 只打印，不改文件
      python tools/make_story_pool.py --check    # ★ 非破坏性核对：只"算"不"写"，
                                                 #   比生成器现在会写成的东西 vs 文档现文；
                                                 #   不一致就打印 diff 并 rc=1。
                                                 #   `tools/selftest.py` 的生成器回归调它。
"""
import os
import re
import random
import argparse

import _lib  # noqa: F401  (统一 stdout 编码)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, '04-故事精选索引.md')
SKILL = os.path.join(ROOT, 'SKILL.md')
POOL = os.path.join(ROOT, 'tools', '_story_pool.md')

SEED = 20260923
FAMOUS = ('蔺相如', '廉颇', '鸿门', '项羽', '三顾', '诸葛', '桃园', '荆轲', '完璧')
BADS = ('*[', '△', '（表略）', '《礼图》曰', '志第', '：指', '：即')
LEAD = re.compile(r'^(明年|三年|四年|是月|其年|既而|及孝|会元|初，|后，|于是|遂|乃|果'
                  r'|这时|此时|俄而|居|明日|后魏|冬季|夏季|春季|秋季)')
# 1-alt-2 段的定位串：**生成与核对共用，只此一份**
# ⚠ 定位串只锚「1-alt-2. 」这个编号 + 段末的固定句（标题文字改过，别再拿标题当锚）。
PAT = re.compile(r'\n1-alt-2\. .*?需要换一批时改脚本里的 `SEED` 重跑。\n', re.S)


def book_name(b):
    return re.sub(r'^\d\d', '', b).replace('文白', '')


def piece(rel):
    fn = os.path.basename(rel).rsplit('.', 1)[0]
    fn = re.sub(r'^\d+_', '', fn)
    fn = re.sub(r'^(文白对照_|《[^》]+》文白对照·)', '', fn)
    for suf in ('_文白对照', '_现代文翻译', '_现代汉语', '_白话文', '_白话',
                '_译文', '_翻译', '_en', '_文白'):
        fn = fn.replace(suf, '')
    if '_' in fn:
        head, tail = fn.split('_', 1)
        fn = head if len(tail) > 8 else head + '·' + tail      # 长尾巴＝人名列表，截去
    fn = fn.replace('_', '·')
    if fn == '资治通鉴':
        return ''
    return fn.strip('·')


def load_secs():
    lines = open(INDEX, encoding='utf-8').read().split('\n')
    secs = []
    for l in lines:
        m = re.match(r'^(\d\d[^｜]+)｜(\d+)–(\d+)｜(\d+) 窗$', l.strip())
        if m:
            secs.append((m.group(1), int(m.group(2)), int(m.group(3))))
    return lines, secs


def pick(lines, s, e, rng, mode='strict', tries=250):
    """mode: strict=篇名含 传/世家/纪；loose=篇名不含 志/表。"""
    for _ in range(tries):
        t = lines[rng.randint(s, e) - 1].strip()
        if '｜' not in t or t.startswith('#'):
            continue
        p = t.split('｜')
        if len(p) < 5:
            continue
        first = p[4]
        if any(k in first for k in FAMOUS) or any(k in first for k in BADS):
            continue
        if LEAD.match(first):
            continue
        nm = piece(p[0]) or os.path.basename(p[0]).rsplit('.', 1)[0]
        if mode == 'strict' and not re.search(r'传|世家|纪', nm):
            continue
        if mode == 'loose' and ('志' in nm or '表' in nm):
            continue
        return p[0], p[1], p[2], first, nm
    return None


def draw_all():
    lines, secs = load_secs()
    rng = random.Random(SEED)
    rows = []
    for b, s, e in secs:
        got = pick(lines, s, e, rng, 'strict') or pick(lines, s, e, rng, 'loose')
        if not got:
            print('⚠ 未抽到：%s' % b)
            continue
        rel, a, z, first, nm = got
        if not piece(rel):                       # 资治通鉴：篇名取文件名卷号
            nm = re.sub(r'^资治通鉴_', '', os.path.basename(rel).rsplit('.', 1)[0])
        rows.append((book_name(b), nm, first[:30]))
    return rows


def build_table(rows):
    out = ['| # | 史书 | 篇目 | 该篇原文首句（照录；讲时须复述以自证） |', '|---|---|---|---|']
    for i, (bk, nm, fr) in enumerate(rows, 1):
        out.append('| %d | %s | %s | %s |' % (i, bk, nm, fr))
    return '\n'.join(out)


def build_section(rows):
    """1-alt-2 段（写回 SKILL.md）。

    ⚠ 2026-09-29 修复：本函数原先只复现前三句，而 SKILL.md 现文还带着「若连这张表也读不到…」
    与「只照念首句＝讲不全」两段安全文案——**跑一次 make_story_pool.py 就会把它们删掉**，
    其中「读不到选签表」是 selftest BODY_RULES 的必需串，删了直接 FAIL（实测被删 3 处：
    读不到选签表／不许随口编／只照念首句＝讲不全）。现改为逐句复现现文。
    同时整段按句分行（B1：单行过长会被读取工具静默截断，故每行控制在 600 字节内）。

    ⚠ 2026-10-01 再修（AstrBot 只报选签不讲故事的根因）：原首个短语写作「**零工具环境的选签表**」，
    读起来像"面向零工具环境的一条平级选项"，模型在"命令试错两次"之后就顺手抓了它。
    现改名为「**末位兜底：连 `04` 都读不到时的选签表**」，并在段首加两条前置条件＋
    「只给首句、照念＝不合格」的警告——**这一段必须与 SKILL.md「环境能力分流」的分支说明对齐**。
    """
    n = len(rows)
    return ('\n1-alt-2. **末位兜底：连 `04` 都读不到时的选签表（按用户消息字数机械取号，不必读任何文件）**：\n'
            '  ⛔ **先确认你真的没有别的路**——本条的适用条件是"**手上没有执行工具，且连 '
            '`04-故事精选索引.md` 也读不到**"。只要你**能读 `04`**，就回到 **1-alt**；'
            '只要**有执行工具**，就回到**第 1 步**。**这条不是"省事的捷径"**。\n'
            '  ⚠ **本条只给"篇目＋首句"，不含故事正文。** 取到号之后**必须去 Read 该篇原文并按第 2 步补全**'
            '——**只把首句照念一遍就当讲完了＝不合格**（读者看到的会是一句没头没尾的文言，等于没讲）。'
            '所以只要**环境能读到 `data\\` 里的源文件**，本条的产出就只是"**选区**"，正文仍要 Read。\n'
            '  下面的表共 %d 条，对应 27 部书各一条；取**用户这句话的字数 L**，'
            '选第 `(L mod %d)+1` 条，讲该条——**选到哪个讲哪个，不得因「这条更有名／更精彩」而换**。\n'
            '  回答首行写「选签 第 k 条（L＝N 字）」，并把该条的**原文首句照录一次**自证。\n'
            '  **若连这张表也读不到**（所在环境拿不到本文件正文）：**必须如实说明「我读不到选签表，'
            '不能凭记忆编故事充当正史」**，改请用户给篇目、或让用户执行 `python tools\\pick_story.py`'
            '——**绝不许随口编一个"听起来像史"的故事充数**（形如合法、实为瞎编，是本 skill 最重的失效模式）。\n\n%s\n\n'
            '> 表由 `tools/make_story_pool.py` 读 `04` 按固定种子抽取（已剔除完璧归赵等超著名桥段、'
            '译注行、目录行与志表类）；需要换一批时改脚本里的 `SEED` 重跑。\n'
            % (n, n, build_table(rows)))


def build_pool(rows):
    head = ('# 讲史候选池（自动生成，供有读取能力的环境）\n\n'
            '> 由 `tools/make_story_pool.py` 从 `04-故事精选索引.md` 随机抽签生成（固定种子 %d），'
            '每部书若干条、已剔除最著名桥段与志表类。\n'
            '> 用法：按「用户消息字数 mod 条数」机械取号，讲该条；不要自己挑选。\n\n' % SEED)
    body = []
    for i, (bk, nm, fr) in enumerate(rows, 1):
        body.append('%d｜%s｜%s｜%s' % (i, bk, nm, fr))
    return head + '\n'.join(body) + '\n'


def check(rows=None):
    """非破坏性核对：**生成器现在会写成什么** vs **文档现文**，逐字节比。

    ⚠ **只算不写**——改不动任何文件。这是本脚本唯一能进回归的形态（跑真身是会改 SKILL.md 的）。
    返回 `(ok: bool, text: str)`。

    ⚠ **单一实现、两个调用方**：CLI 的 `--check`（人手工跑）与 `tools/selftest.py` 的**进程内调用**
    （它 `import` 本模块直接调，省掉 ~0.41 s 解释器启动——实测 `--check` 全程 0.45 s，其中 0.41 s
    只是"起一个 Python 进程"的固定开销，检查本身 0.04 s）。**判据只有这一份，别在 selftest 里再抄。**
    """
    import difflib
    rows = draw_all() if rows is None else rows
    sec = build_section(rows)
    out, bad = [], []
    m = PAT.search(open(SKILL, encoding='utf-8').read())
    if not m:
        bad.append('SKILL.md 里找不到 1-alt-2 段')
    elif m.group(0) != sec:
        d = list(difflib.unified_diff(m.group(0).splitlines(), sec.splitlines(),
                                      'SKILL.md 现文', '生成器现在会写的', lineterm='', n=0))
        out.append('\n'.join(d[:60]))
        bad.append('SKILL.md 的 1-alt-2 段与生成器算出的不一致（见上 diff，%d 行差异）' % len(d))
    if not os.path.exists(POOL):
        bad.append('tools/_story_pool.md 不存在')
    elif open(POOL, encoding='utf-8').read() != build_pool(rows):
        bad.append('tools/_story_pool.md 与生成器算出的不一致')
    if bad:
        out.append('⛔ 生成器已漂移：%s' % '；'.join(bad))
        out.append('   ⇒ 跑一次 make_story_pool.py 就会改写上面这些内容。'
                   '要么改本脚本让它复现现文，要么按它的输出重写文档——'
                   '两者选一，别让"跑一次生成器"变成一次静默改稿。')
        return False, '\n'.join(out)
    return True, 'OK 选签表段与 tools/_story_pool.md 均与生成器算出的一致（%d 条）' % len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只打印，不改文件')
    ap.add_argument('--check', action='store_true',
                    help='非破坏性核对：只算不写，比生成器算出的内容与文档现文是否逐字节一致；'
                         '不一致则打印 diff 并 rc=1（供 tools/selftest.py 的生成器回归调用）')
    args = ap.parse_args()
    rows = draw_all()
    sec = build_section(rows)

    # ── --check：只"算"不"写"。判据见 check()。
    #    2026-09-29 加——D1（build_section 只复现前三句 → 跑一次静默删掉三处安全文案）
    #    与 D2（`pat.sub(sec, s)` 转义崩溃）能潜伏两轮，就是因为这脚本此前不在任何回归里。
    if args.check:
        ok, msg = check(rows)
        print(msg)
        if not ok:
            raise SystemExit(1)
        return

    print(build_table(rows))
    if args.dry:
        return
    # 写 SKILL.md（替换既有 1-alt-2 段；无则追加到讲史流程后）
    s = open(SKILL, encoding='utf-8').read()
    if PAT.search(s):
        # ⚠ 2026-09-29：必须用函数式替换。`PAT.sub(sec, s)` 会把 sec 里的反斜杠当**替换模板转义**
        #   ——本段含 `python tools\pick_story.py` 与 `tools\make_story_pool.py`，直接传串会
        #   抛 `re.PatternError: bad escape \p`（实测跑一次就崩，但**崩在写盘之前**，故文档没被破坏）。
        s = PAT.sub(lambda _m: sec, s, count=1)
    else:
        raise SystemExit('SKILL.md 里没有 1-alt-2 段，无法定位替换——请先手工建好该段')
    open(SKILL, 'w', encoding='utf-8', newline='\n').write(s)
    open(POOL, 'w', encoding='utf-8', newline='\n').write(build_pool(rows))
    print('\n已更新 SKILL.md 选签表（%d 条）与 tools/_story_pool.md' % len(rows))


if __name__ == '__main__':
    main()
