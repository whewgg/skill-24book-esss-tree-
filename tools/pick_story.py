# -*- coding: utf-8 -*-
"""
pick_story.py — 讲史抽签器（机械抽签，替代人工选书）

为什么需要它：`SKILL.md` 要求"27 部等概率选书、连讲多个须轮换史书"，但靠模型
自觉挑书会稳定偏向《资治通鉴》（它在 04 索引里排第一、体量最大、最眼熟）。
把抽签交给脚本，模型只负责"按抽到的窗口 Read 并讲"，偏向就没有了。

口径：默认**分层等概率**——先 27 部书等概率抽一部，再在该书内等概率抽一窗。
（`--flat` 改为全库窗口等概率，此时《资治通鉴》占 1650/22100 ≈ 7.5%。）

用法（工作目录＝skill 根目录）：
    python tools\\pick_story.py                       # 抽 1 个（默认排除最近 4 次抽到/讲过的书）
    python tools\\pick_story.py --count 3             # 一次抽 3 个，书互不重复
    python tools\\pick_story.py --book 24明史文白     # 指定史书
    python tools\\pick_story.py --dynasty 刘宋        # 按朝代抽（朝名消歧见 references\\dynasty-disambiguation.md）
    python tools\\pick_story.py --topic 玄武门        # 按主题抽（匹配窗口「首句＋路径」）
    python tools\\pick_story.py --dynasty 宋          # 歧义朝名 → 报出候选，要求二选一
    python tools\\pick_story.py --exclude 资治通鉴,史记   # 本次不要这两本（只对这一次生效）
    python tools\\pick_story.py --no-repeat 0         # 不排除
    python tools\\pick_story.py --info               # 只统计分布，不抽
    python tools\\pick_story.py --commit             # 抽完记入 tools\\story_log.md（讲完再记）
    python tools\\pick_story.py --no-log             # 本次不写抽签记录（试抽/调试用）

**签号**：每次抽签都会打印一个 6 位签号（由 书名｜路径｜起止行 算出，确定性可复核）。
回答讲史时**首行必须原样带上该签号**——这是"到底走没走抽签"的唯一凭证：
没跑脚本就编不出签号，跑过就一定对得上。**没有签号的讲史视为自选书，不合格。**

**两本账（分工不同，别混）**：
  · `tools/_draw_log.md`——**抽到即记**，脚本自动写。`--no-repeat` 读它，所以轮换不再依赖
    人工登记（原先靠"讲完记得 --commit-entry"，实际没人登记 → 轮换网一直是空的）。
  · `tools/story_log.md`——**讲完再记**（人工 `--commit-entry`），供轮换与回顾。
    `--no-repeat` 会合并两本账取最近 N 次。
"""
import argparse
import datetime as _dt
import hashlib
import os
import random
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, '04-故事精选索引.md')
LOG = os.path.join(ROOT, 'tools', 'story_log.md')          # 讲完台账（人工登记）
DRAW_LOG = os.path.join(ROOT, 'tools', '_draw_log.md')     # 抽签记录（脚本自动写）
SEP = '\uff5c'          # 全角竖线 ｜

# ── 政权 → 本库史书（与 references/dynasty-disambiguation.md 第 1 节同源） ──
DYNASTY = {
    '先秦': ['01史记文白', '00资治通鉴'],
    '东周': ['01史记文白', '00资治通鉴'],
    '春秋': ['01史记文白', '00资治通鉴'],
    '战国': ['01史记文白', '00资治通鉴'],
    '秦':   ['01史记文白', '00资治通鉴'],
    '西汉': ['02汉书文白', '01史记文白', '00资治通鉴'],
    '东汉': ['03后汉书文白', '00资治通鉴'],
    '蜀汉': ['04三国志文白', '00资治通鉴'],
    '三国': ['04三国志文白', '00资治通鉴'],
    '曹魏': ['04三国志文白', '00资治通鉴'],
    '西晋': ['05晋书文白', '00资治通鉴'],
    '东晋': ['05晋书文白', '00资治通鉴'],
    '刘宋': ['06宋书文白', '14南史文白', '00资治通鉴'],
    '南朝': ['06宋书文白', '07南齐书文白', '08梁书文白', '09陈书文白', '14南史文白', '00资治通鉴'],
    '南齐': ['07南齐书文白', '14南史文白', '00资治通鉴'],
    '萧梁': ['08梁书文白', '14南史文白', '00资治通鉴'],
    '陈':   ['09陈书文白', '14南史文白', '00资治通鉴'],
    '北魏': ['10魏书文白', '15北史文白', '00资治通鉴'],
    '北朝': ['10魏书文白', '11北齐书文白', '12周书文白', '15北史文白', '13隋书文白', '00资治通鉴'],
    '北齐': ['11北齐书文白', '15北史文白', '13隋书文白', '00资治通鉴'],
    '北周': ['12周书文白', '15北史文白', '13隋书文白', '00资治通鉴'],
    '隋':   ['13隋书文白', '00资治通鉴'],
    '李唐': ['16旧唐书文白', '17新唐书文白', '00资治通鉴'],
    '五代': ['18旧五代史', '19新五代史', '00资治通鉴'],
    '后梁': ['18旧五代史', '19新五代史', '00资治通鉴'],
    '后唐': ['18旧五代史', '19新五代史', '00资治通鉴'],
    '后晋': ['18旧五代史', '19新五代史', '00资治通鉴'],
    '后汉': ['18旧五代史', '19新五代史', '00资治通鉴'],
    '后周': ['18旧五代史', '19新五代史', '00资治通鉴'],
    '赵宋': ['20宋史文白'],
    '北宋': ['20宋史文白'],
    '南宋': ['20宋史文白'],
    '辽':   ['21辽史'],
    '金':   ['22金史文白'],
    '元':   ['23元史', '25新元史文白'],
    '明':   ['24明史文白'],
    '清':   ['26清史稿文白'],
}

# 该政权在《资治通鉴》里对应哪个（些）纪子目录；None＝不限定（该政权通鉴不载）
# 关键：通鉴「宋纪」=刘宋、「齐纪」=南齐、「梁纪」=萧梁、「唐纪」=李唐，
#       而北魏/北齐/北周散在南北朝各纪，通鉴不载赵宋。
#
# ⚠ 名字特意与 rag.py 的 `TZJ_JI` **不同**：那张是「纪名 → 政权」（给引文加政权限定语），
#   这张是反查「朝名 → 纪名」（给 --dynasty 过滤）。早先两处都叫 TZJ_JI、方向相反，
#   是极易踩的坑，故此处改名以示区别。
TZJ_JI_OF_DYNASTY = {
    '先秦': ('周纪',), '东周': ('周纪',), '春秋': ('周纪',), '战国': ('周纪',),
    '秦': ('秦纪',),
    '西汉': ('汉纪',), '东汉': ('汉纪',), '蜀汉': ('魏纪',), '三国': ('魏纪',), '曹魏': ('魏纪',),
    '西晋': ('晋纪',), '东晋': ('晋纪',),
    '刘宋': ('宋纪',), '南齐': ('齐纪',), '萧梁': ('梁纪',), '陈': ('陈纪',),
    '南朝': ('宋纪', '齐纪', '梁纪', '陈纪'),
    '北魏': ('宋纪', '齐纪', '梁纪', '陈纪'),
    '北朝': ('宋纪', '齐纪', '梁纪', '陈纪'),
    '北齐': ('宋纪', '齐纪', '梁纪', '陈纪'),
    '北周': ('宋纪', '齐纪', '梁纪', '陈纪'),
    '隋': ('隋纪',),
    '李唐': ('唐纪',),
    '五代': ('后梁纪', '后唐纪', '后晋纪', '后汉纪', '后周纪'),
    '后梁': ('后梁纪',), '后唐': ('后唐纪',), '后晋': ('后晋纪',),
    '后汉': ('后汉纪',), '后周': ('后周纪',),
    '赵宋': None, '北宋': None, '南宋': None,
    '辽': None, '金': None, '元': None, '明': None, '清': None,
}

# 歧义朝名 → 候选（必须让用户/调用方选定，不许默默当成一个）
AMBIGUOUS = {    '宋': (['刘宋', '赵宋'], '刘宋＝南朝宋（06宋书／14南史，420–479）；赵宋＝北宋南宋（20宋史，960–1279）。通鉴只含刘宋。'),
    '汉': (['西汉', '东汉', '蜀汉', '后汉'], '西汉＝02汉书；东汉＝03后汉书；蜀汉＝04三国志；后汉＝五代（18/19五代史）。'),
    '唐': (['李唐', '后唐', '五代'], '李唐＝16/17唐书；后唐＝五代（18/19五代史）。'),
    '周': (['东周', '北周', '后周'], '东周＝01史记；北周＝12周书；后周＝五代（18/19五代史）。'),
    '魏': (['曹魏', '北魏'], '曹魏＝04三国志；北魏＝10魏书。'),
    '齐': (['南齐', '北齐'], '南齐＝07南齐书；北齐＝11北齐书。'),
    '梁': (['萧梁', '后梁'], '萧梁＝08梁书（南朝）；后梁＝五代（18/19五代史）。'),
    '晋': (['西晋', '东晋', '后晋'], '西晋／东晋＝05晋书；后晋＝五代（18/19五代史）。'),
    '陈': (['陈'], '南朝陈＝09陈书。'),
}

# 易混史书的政权标签（输出时附注，防止引用时写错书名）
BOOK_NOTE = {
    '06宋书文白': '刘宋（南朝宋 420–479）',
    '20宋史文白': '赵宋（北宋／南宋 960–1279）',
    '17新唐书文白': '李唐（618–907）',
    '14南史文白': '南朝（宋齐梁陈）',
    '15北史文白': '北朝＋隋',
    '18旧五代史': '五代（后梁–后周）',
    '19新五代史': '五代（后梁–后周）',
    '07南齐书文白': '南齐（479–502）',
    '11北齐书文白': '北齐（550–577）',
    '12周书文白': '北周（557–581）',
    '08梁书文白': '萧梁（502–557）',
    '00资治通鉴': '编年 前403–959（宋纪＝刘宋）',
}


def load_index():
    """返回 [(book, [window, ...]), ...]，window = dict(path,s,e,score,first)。"""
    with open(INDEX, encoding='utf-8') as fh:
        lines = fh.read().splitlines()
    books, cur = [], None
    win_re = re.compile(r'^(.+?)' + SEP + r'(\d+)' + SEP + r'(\d+)' + SEP + r'(\d+)' + SEP + r'(.*)$')
    for ln in lines:
        if ln.startswith('## '):
            cur = (ln[3:].strip(), [])
            books.append(cur)
            continue
        if cur is None:
            continue
        m = win_re.match(ln)
        if m:
            cur[1].append({'path': m.group(1), 's': int(m.group(2)), 'e': int(m.group(3)),
                           'score': int(m.group(4)), 'first': m.group(5)})
    return [(b, w) for b, w in books if w]


def read_ledger(path):
    """读一本账，返回 [(date, book), ...]（新在后）。两本账同格式，共用本函数。"""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding='utf-8') as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith('#') or ln.startswith('>'):
                continue
            parts = ln.split(SEP)
            if len(parts) >= 2:
                out.append((parts[0].strip(), parts[1].strip()))
    return out


def read_log():
    """讲完台账（人工登记）。"""
    return read_ledger(LOG)


def read_draw_log():
    """抽签记录（脚本自动写）——--no-repeat 的主要依据。"""
    return read_ledger(DRAW_LOG)


DRAW_LOG_HEAD = ('# 抽签记录（append-only，由 tools/pick_story.py 自动写入，不要手改）\n'
                 '> 抽到即记。`--no-repeat` 读本文件，所以轮换不依赖"讲完记得登记"。\n'
                 '> 行格式：`时间｜书名｜路径｜起行｜止行｜情节分`\n')


def commit(rows, path=None):
    """追加记录。path 省略＝写讲完台账 story_log.md；传 DRAW_LOG＝写抽签记录。"""
    path = path or LOG
    new = not os.path.exists(path)
    with open(path, 'a', encoding='utf-8') as fh:
        if new:
            if path == DRAW_LOG:
                fh.write(DRAW_LOG_HEAD)
            else:
                fh.write('# 讲史台账（append-only，供 --no-repeat 轮换判定）\n')
                fh.write('> 本文件由 `tools/pick_story.py --commit-entry "书名|路径|起行|止行|情节分"` 追加，'
                         '不要手改格式。\n')
                fh.write('> 空台账（只有本头部）＝ 没有讲史记录；`--no-repeat` 会自动失效，抽签正常进行。\n'
                         '> 注：`--no-repeat` 还会并读 `tools/_draw_log.md`（抽到即记），故轮换不完全依赖本文件。\n'
                         '> 行格式：`时间｜书名｜路径｜起行｜止行｜情节分`\n')
        for r in rows:
            fh.write(SEP.join([r['stamp'], r['book'], r['win']['path'], str(r['win']['s']),
                               str(r['win']['e']), str(r['win']['score'])]) + '\n')


def ticket(book, win):
    """签号：由「书名｜路径｜起止行」算出的 6 位确定性短码——没跑抽签就编不出来。"""
    raw = '%s|%s|%d|%d' % (book, win['path'], win['s'], win['e'])
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:6].upper()


def eligible_books(books, args, recent):
    sel = books
    if args.book:
        keys = [k.strip() for k in args.book.split(',') if k.strip()]
        sel = [(b, w) for b, w in sel if any(k in b for k in keys)]
        if not sel:
            sys.exit('× 找不到匹配的史书：%s（可用 --info 看书目）' % args.book)
    # 分隔符要宽容：中文用户极可能打全角逗号/顿号/全角竖线，或直接空格分隔
    # （实测这些写法原先**静默无效**——排了个寂寞还不出声）。书文件夹名不含空格，可安全按空白切。
    if getattr(args, 'topic', None):
        # 用户指定主题时也得走抽签——否则模型只能自己 Grep 04 挑窗，偏食就回来了。
        # 主题只匹配窗口的「首句＋路径」（首句仅 24 字），覆盖有限，故无匹配时给出下一步。
        k = args.topic.strip()
        tsel = []
        for b, wins in sel:
            kept = [w for w in wins if k in w['path'] or k in w['first']]
            if kept:
                tsel.append((b, kept))
        if not tsel:
            sys.exit('× --topic「%s」在窗口的「首句＋路径」里没有匹配。主题词覆盖有限，'
                     '请先用 `python tools\\rag.py %s` 查它出现在哪些卷，再用 --book 定向抽'
                     % (k, k))
        sel = tsel
    ex_keys = [k.strip() for k in re.split(r'[,，、｜|；;/ \t]+', getattr(args, 'exclude', '') or '')
               if k.strip()]
    if ex_keys:
        # 「换一本」的正确姿势：重抽并排除刚讲过/不想要的那本，
        # 而不是让模型自己另挑一本（那正是偏向《资治通鉴》《史记》的入口）。
        all_names = [b for b, _ in books]
        miss = [k for k in ex_keys if not any(k in b for b in all_names)]
        if miss:
            # 拼错/写空了的排除项不能让整条命令"看起来生效了"——必须出声
            print('（注意：排除项 %s 没有匹配到任何史书，已忽略；可用 --info 看全部书名）'
                  % '、'.join(miss))
        keep = [(b, w) for b, w in sel if not any(k in b for k in ex_keys)]
        if not keep:
            sys.exit('× 排除名单把候选书全排掉了：排除了 %s，而当前候选只有 %s——'
                     '请减少排除项，或去掉 --book／--dynasty 限定'
                     % ('、'.join(ex_keys), '、'.join(b for b, _ in sel)))
        sel = keep
    if args.dynasty:
        d = args.dynasty.strip()
        if d in AMBIGUOUS and d not in DYNASTY:
            cands, note = AMBIGUOUS[d]
            print('！「%s」是歧义朝名，不能直接抽签。' % d)
            print('  %s' % note)
            for c in cands:
                if c in DYNASTY:
                    print('  · %s → %s' % (c, '、'.join(DYNASTY[c])))
            sys.exit('  请改用 --dynasty %s' % ' 或 --dynasty '.join(cands))
        if d not in DYNASTY:
            sys.exit('× 未知朝名「%s」。可用：%s' % (d, '、'.join(sorted(DYNASTY))))
        allowed = DYNASTY[d]
        sel = [(b, w) for b, w in sel if b in allowed]
        if not sel:
            blocked = [b for b in allowed if any(k in b for k in ex_keys)]
            if blocked:
                # 别报"该朝无书节"——那是误导：书节在，只是被本次排除名单挡掉了
                sys.exit('× 「%s」的书节是 %s，但其中 %s 被本次 --exclude 挡掉了。'
                         '要么去掉排除项，要么换个 --dynasty'
                         % (d, '、'.join(allowed), '、'.join(blocked)))
            sys.exit('× %s 在本索引中无可用书节：%s' % (d, '、'.join(allowed)))
        # 通鉴是编年通史，须按纪子目录再收一刀，否则会抽到别的朝代（如"刘宋"抽到汉纪）
        ji = TZJ_JI_OF_DYNASTY.get(d)
        if ji:
            pos = '\\%s\\' % ji[0] if len(ji) == 1 else None

            def wkeep(w):
                if pos:
                    return pos in w['path']
                return any('\\%s\\' % j in w['path'] for j in ji)

            newsel = []
            for b, wins in sel:
                if b != '00资治通鉴':
                    newsel.append((b, wins))
                    continue
                kept = [w for w in wins if wkeep(w)]
                if kept:
                    newsel.append((b, kept))
            sel = newsel
            if not sel:
                sys.exit('× %s 在通鉴中无对应纪节，请去掉 --dynasty 或改用 --book' % d)
    if recent:
        keep = [(b, w) for b, w in sel if b not in recent]
        if keep:
            sel = keep
        else:
            print('（提示：排除最近 %d 次后无书可选，已取消排除）' % len(recent))
    return sel


def draw(books, args, rng):
    # `--min-score` 无窗可选时会退化为"该书全部窗"。这是有意的兜底（否则某些书直接抽不出来），
    # 但**必须出声**——否则用户设了 9999 却抽到情节分 42，会以为参数没生效。
    warned = [False]

    def pick_from(wins, book):
        q = [w for w in wins if w['score'] >= args.min_score]
        if not q:
            if not warned[0]:
                print('（注意：--min-score %d 下《%s》无窗可选，已退化为该书全部窗；'
                      '这不是错误，想提高下限就调小这个值）' % (args.min_score, book))
                warned[0] = True
            return wins
        return q

    if args.flat:
        pool = [(book, win) for book, wins in books for win in wins
                if win['score'] >= args.min_score]
        if not pool:
            print('（注意：--min-score %d 下全库无窗可选，已退化为全库全部窗）' % args.min_score)
            pool = [(book, win) for book, wins in books for win in wins]
        picks = []
        for _ in range(args.count):
            if not pool:
                break
            pick = rng.choice(pool)
            picks.append(pick)
            pool = [(book, win) for book, win in pool if book != pick[0]]
        return picks

    picks, used = [], set()
    for _ in range(args.count):
        cand = [(b, w) for b, w in books if b not in used]
        if not cand:
            break
        book, wins = rng.choice(cand)
        used.add(book)
        picks.append((book, rng.choice(pick_from(wins, book))))
    return picks


def show(books, args, picks):
    total = sum(len(w) for _, w in books)
    mode = '窗口等概率（每轮从剩余书的合格窗口抽取）' if args.flat else '分层等概率（先等概率选书，再书内等概率选窗）'
    print('抽签——%s；候选书 %d 部 / %d 窗' % (mode, len(books), total))
    print('（每书窗数／全库占比见 --info；引用纪律见 references\\dynasty-disambiguation.md）\n')
    if getattr(args, 'exclude', None):
        print('本次临时排除：%s —— **仅这一次有效**，之后抽签会自动重新纳入\n' % args.exclude)
    rows = []
    for book, win in picks:
        note = BOOK_NOTE.get(book)
        print('■ %s%s' % (book, '　← %s' % note if note else ''))
        print('  签号      #%s   ← 讲史回答的**首行**请原样带上此签号'
              '（未带＝没走抽签，不合格）' % ticket(book, win))
        print('  路径      %s' % win['path'])
        print('  行号      %d–%d（共 %d 行）' % (win['s'], win['e'], win['e'] - win['s'] + 1))
        print('  情节分    %d' % win['score'])
        print('  首句      %s' % win['first'])
        # 首句若以承接词起头，说明这窗是**上文的下半段**（约 1.8% 的窗）——行号区间是准的，
        # 但故事起点在窗外的上一段；讲完整需往前回溯几行（如子贡故事：窗是"子贡去而之鲁…"
        # 的收尾，游说齐吴越晋的全过程在其前 40 行）。只提示，不代挑。
        m = re.match(r'^(于是|遂|果|乃|竟|既而|已而|既|因|会|及|俄而|少顷|居|明日|后'
                     r'|这时|此时|是时|当是时|初|先是|先|顷之|由是|其后|于是乎)', win['first'])
        if m:
            print('  ⚠ 承接段  首句以「%s」起头，本窗或是故事的**收尾**——往前回溯几行即可补全开头'
                  % m.group(1))
        # 窗口只是「情节核心段」：全库窗口中位数仅 8 行（72.6% ≤10 行），经常从人物生平
        # 中段起、到中段止，甚至跨两个人（实测：08梁书卷46 抽到「承圣二年，**又**与王僧辩
        # 讨陆纳…」——那是杜龛传记的后半段，读者根本不知道他是谁）。故除窗口外再给「讲全」
        # 的扩读范围：往前找人物出场、往后找结局。范围是机械给的起点，完整性由 Read 后判断。
        _sp, _ep = win['s'], win['e']
        _lo, _hi = max(1, _sp - 30), _ep + 30
        print('  读取      Read  %s  offset=%d  limit=%d   ← 本窗＝情节核心段，'
              '常从人物生平中段起、到中段止' % (os.path.join(ROOT, 'data', win['path']), _sp, _ep - _sp + 1))
        print('  讲全      Read  %s  offset=%d  limit=%d   ← 往前读到人物出场'
              '（"某某，字某某，某地人也"／"某，某之子也"）、往后读到结局（卒／赠谥／换人）；'
              '读者能答出"这是谁、经历了什么、结局如何"才算讲全'
              % (os.path.join(ROOT, 'data', win['path']), _lo, _hi - _lo + 1))
        print('  注        讲完请注明《书名·卷次·篇名》并写全政权限定（"刘宋"不写"宋"）；'
              '史实照录，虚构须标注')
        # 2026-09-23：微信机器人端出现第三种"讲两遍"——讲完自问"想听哪个朝代"、自答"好啊"、
        # 预告"我给你讲个项羽的故事"（往往还是刚讲完的那个）。抽签输出是模型开讲前**必看**
        # 的最后一屏，把禁则写在这里比只写进 SKILL.md 更能到达现场（同"承接段"提醒的思路）。
        print('  ⚠ 一轮一签  本条回答讲完这一个**即止**：不要自问自答续讲第二个故事，'
              '也不要末尾预告"再讲一个"；续讲须等用户下一轮发起并重新抽签（自动避开刚讲过的书）')
        print('  ⚠ 产出契约  正文＝照本窗 Read 复述（白话叙述＋原文金句），模型不得用自身语言能力'
              '另行创作故事；若用户本就没要历史故事，回到入口裁决、别开讲')
        print('  登记      python tools\\pick_story.py --commit-entry "%s|%s|%d|%d|%d"'
              % (book, win['path'], win['s'], win['e'], win['score']))
        print()
        rows.append({'stamp': _dt.datetime.now().strftime('%Y-%m-%d %H:%M'),
                     'book': book, 'win': win})
    return rows


TICKET_RE = re.compile(r'(?<![\w#])#([0-9A-Fa-f]{6})(?![\w])')


def check_answer(src):
    """后置校验一段讲史回答：只讲一遍（签号唯一）＋虚构必标注。
    退出码：0 合格；1 有 FAIL；2 用法错/空输入。"""
    try:
        if src == '-':
            text = sys.stdin.read()
        else:
            with open(src, encoding='utf-8', errors='replace') as fh:
                text = fh.read()
    except OSError:
        print('--check 无法读取文件 %s（或传 - 从 stdin 读）' % src)
        return 2
    if not text.strip():
        print('--check 输入为空')
        return 2

    tickets = [m.group(1).upper() for m in TICKET_RE.finditer(text)]
    first_line = text.splitlines()[0].strip()
    first_ticket = TICKET_RE.search(first_line)

    # 签号对账：从两本账（抽签记录＋讲完台账）的 书名|路径|起行|止行 重算签号
    known = set()
    for path in (DRAW_LOG, LOG):
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8', errors='replace') as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith('#') or ln.startswith('>'):
                    continue
                p = [x.strip() for x in ln.split(SEP)]
                if len(p) >= 5:
                    raw = '%s|%s|%s|%s' % (p[1], p[2], p[3], p[4])
                    known.add(hashlib.sha1(raw.encode('utf-8')).hexdigest()[:6].upper())

    # 虚构标注看**开头**（SKILL.md 要求标在开头）。只扫开头是为了不让正文里顺带提到的
    # 「小说/传说」二字（如"此事后世小说附会"）替一段无签号的讲史蒙混过关。
    head = '\n'.join(text.splitlines()[:3])
    has_fiction = bool(re.search(r'非真实历史|不是真实历史|并非史实|非正史|纯属虚构|'
                                 r'(?:^|[\s：:（(【\[])\s*(?:虚构|演义|小说|民间故事|戏说|改编)(?:[\s：:）)】\]，。]|$)',
                                 head))
    fails, warns = [], []

    # 篇幅：正文过长＝把窗口事件顺了一遍（用户反馈「故事讲得太长」）。只 WARN——长度无硬标准。
    _body = chr(10).join(text.splitlines()[1:])
    _n = len([c for c in _body if not c.isspace()])
    if _n > 600:
        warns.append('正文约 %d 字，偏长（建议 ≤400 字）——讲全≠讲长，挑最紧的一段讲' % _n)

    # 第三种合法形态：**应点讲**——用户点名要某个故事／成语／事件（"讲完璧归赵""破釜沉舟"），
    # 走 rag.py 定位原文，**没有签号**，出处就是凭证。判据＝首行含「应点」或含《书名·卷次》
    # （书名号内有「·」分隔，如《资治通鉴·卷8》）。
    responding = bool(re.search(r'应点', first_line)
                      or re.search(r'《[^》]{2,40}·[^》]{1,20}》', first_line))

    # 第四种合法形态：**1-alt 降级选自**（2026-10-01 补）。无执行工具的环境按 SKILL.md「1-alt」
    #   从 `04` 手算取号，凭证是首行「引用 04 第 N 行（L＝N 字）」。
    #   ⚠ 此前 `--check` 只认签号／应点讲两种 —— 于是**降级路径产出的合格回答会被判 FAIL**
    #   （实测：一份合规的 1-alt 回答报「无签号、也非应点讲」，rc=1）。
    #   这是"闸门不认文档明确认可的路径"＝静默失联，必须让它认。判据＝首行含「引用 04 第」
    #   且带「第 N 行」，避免把正文里偶然提到 04 的句子当凭证。
    alt_04 = bool(re.search(r'引用\s*`?04`?\s*第\s*\d+\s*行', first_line))

    # 第五道闸：**窗口原文必现**（2026-10-05 补）。曾见「抽了真签却全篇自编、末尾还自称
    #   『原文照录，无虚构成分』」——魏书卷28 奚牧：编出『太祖崩，遗诏令奚牧辅政，遂与魏主
    #   共治天下』（北魏太祖死于宫廷政变，奚牧是臣），还把同卷庾业延的清廉事安到他头上。
    #   签号真、内容假，比『没抽签』更隐蔽。判据：首行签号 → 回 04 反查窗口 → 读源文件该区间
    #   → **去标点后滑窗取连续 16 字** → 回答须含其中一个；否则＝没照录。
    #   ⚠ 不按标点切段（文言标点密，切出的片段太短，会误伤真照录的回答——实测）。
    if first_ticket:
        _code = first_ticket.group(1).upper()
        _win = None
        for _b, _ws in load_index():
            for _w in _ws:
                if ticket(_b, _w) == _code:
                    _win = _w
                    break
            if _win:
                break
        if _win:
            _fp = os.path.join(ROOT, 'data', _win['path'])
            if os.path.exists(_fp):
                with open(_fp, encoding='utf-8', errors='replace') as _fh:
                    _ls = _fh.read().splitlines()
                _PUNC = '，。；：！？、（）【】“”‘’《》〈〉「」『』' + chr(34) + chr(39)

                def _bare(x):
                    for _c in _PUNC:
                        x = x.replace(_c, '')
                    return ''.join(x.split())

                _src = ''.join(_bare(x.lstrip('>').strip()) for x in _ls[_win['s'] - 1:_win['e']])
                _ans = _bare(text)
                _MIN = 16
                _runs = [_src[k:k + _MIN] for k in range(0, max(0, len(_src) - _MIN + 1), 4)]
                if _runs and not any(r in _ans for r in _runs):
                    fails.append('窗口原文一句都没出现（≥%d 字连续片段全无）——疑似「抽了真签却自己编」；真照录至少要含一段原句' % _MIN)

    # 第六道闸：**自称照录却无引号引文**（2026-10-05）。verify 认「」/西文引号包裹的引文；
    #   本案（魏书卷28 奚牧）把伪原文写成**裸行**、不加引号 → verify 抽出「引文 0 条」→ 直接
    #   放行。故：凡出现「原文照录／无虚构成分／原文如下」等声明，回答里**必须有引号引文**，
    #   否则＝逃避核验（照录一律用「」标明，这样 verify 才抽得到、才核得动）。
    _Q = chr(34)
    if re.search('原文照录|原文如下|照录原文|无虚构成分|原文即是|原文为', text):
        _quoted = re.search('「[^」]{8,}」', text) or re.search(_Q + '[^' + _Q + ']{8,}' + _Q, text)
        if not _quoted:
            fails.append('自称「原文照录／无虚构成分」却没有任何引号引文——verify 抽不到引文就核不了；照录请一律用「」标明')
    if len(tickets) > 1:
        fails.append('出现 %d 个签号（%s）＝讲了两遍／多条路——只允许一个'
                     % (len(tickets), '、'.join('#%s' % t for t in tickets)))
    if len(tickets) == 0:
        if has_fiction:
            pass  # 虚构故事且已标注，合格
        elif responding:
            print('OK    应点讲（首行有出处、无签号）：用户点名的故事按 rag.py 定位原文讲')
        elif alt_04:
            print('OK    1-alt 降级选自（首行「引用 04 第 N 行」）：无执行工具的环境按 04 手算取号')
        else:
            fails.append('无签号、也无「虚构/非真实历史」标注、也非应点讲、也非 1-alt 降级选自'
                         '（首行「引用 04 第 N 行」）：既没走抽签、又当真实历史讲')
    else:
        if has_fiction:
            warns.append('带了签号却又标「虚构」——正史故事不必标虚构，疑似与小说混叙')
        if not first_ticket:
            fails.append('签号不在首行（讲史回答首行应原样带签号）')
        for code in dict.fromkeys(tickets):
            if code not in known:
                warns.append('签号 #%s 未在两本账里对上（可能 --no-log 试抽，或讲完未登记）' % code)

    for f in fails:
        print('FAIL %s' % f)
    for w in warns:
        print('WARN %s' % w)
    print('# 结论：%s' % ('存在 %d 处硬错，必须改' % len(fails) if fails
                        else '合格（%d 处待人工确认）' % len(warns)))
    return 1 if fails else 0


def show_next(spec):
    """续讲取点：给**上一签的路径**（可附已读到的止行）→ 输出"接下来读哪里"。
    ① 同一文件的后续行（本传/本卷还没讲完）；② 同书紧邻的下一卷（本传/本卷已读完）。
    为什么要有它：用户说"继续／后来呢"时，模型得先知道"后续在哪"——靠猜就成了自选书；
    本工具把取点机械掉，续讲才有据可查（与"换一本必须 --exclude"同一条纪律）。
    """
    bits = [b.strip() for b in spec.replace('|', SEP).split(SEP) if b.strip()]
    if not bits:
        sys.exit('× --next 需要上一签的路径（可附止行）：--next "18旧五代史\\现代文\\101_….md" 58')
    rel = bits[0]
    end = int(bits[-1]) if len(bits) > 1 and bits[-1].isdigit() else 0
    p = rel if os.path.isabs(rel) else os.path.join(ROOT, 'data', rel)
    if not os.path.exists(p):
        sys.exit('× --next 找不到文件：%s' % rel)
    with open(p, encoding='utf-8', errors='replace') as fh:
        lines = fh.read().splitlines()
    n = len(lines)
    print('# 续讲取点：%s（共 %d 行｜上次读到 %s）' % (rel, n, end or '未指明'))
    if end and end < n:
        print('  ① 同一文件后续  Read  %s  offset=%d  limit=%d'
              % (p, end + 1, min(40, n - end)))
    else:
        print('  ① 同一文件后续  ——（%s）' % ('已到文件末尾' if end else '未给止行就不给①'))
    folder = os.path.dirname(p)
    try:
        sibs = sorted(f for f in os.listdir(folder) if f.endswith('.md'))
    except OSError:
        sibs = []
    nxt = None
    for i, f in enumerate(sibs):
        if os.path.abspath(os.path.join(folder, f)) == os.path.abspath(p) and i + 1 < len(sibs):
            nxt = sibs[i + 1]
    if nxt:
        npth = os.path.join(folder, nxt)
        with open(npth, encoding='utf-8', errors='replace') as fh:
            nl = len(fh.read().splitlines())
        print('  ② 紧邻卷次      Read  %s  offset=1  limit=%d   （%s）'
              % (npth, min(40, nl), nxt))
    else:
        print('  ② 紧邻卷次      ——（已是本目录最后一份，此人/此事到此为止）')
    print('  怎么选  ①若仍在讲**同一个人／同一件事** → 续讲①；①已换传主、或上次读到的就是本卷末 → 用②。')
    print('  凭证    续讲首行写「续 #原签号　→《书名·卷次》」（沿用上一签的签号，标明续到哪一卷）。')
    print('  ⚠ 续讲**不按字数取号**（按字数取号是"再讲一个"的机制）；也不得借"续讲"之名换到无关的书。')
    return 0


def locate_biography(name):
    """定位本传路径。两段式：
    ① 读 `03-人物出现索引.md` 的 ★ 字段（机器格式、含「⚠同名异人」标记）——若该名是**同名异人**
       （03 会把几个人的本传路径用分号列进 ★ 字段），直接报错让用户用更具体的称呼（**讲错人比讲不到更糟**）；
    ② 03 没有该名 → 回退 `rag.py locate`（覆盖 03 未收的人、繁简、避讳、extra_names 手工登记），
       解析其「起读…路径」行。都不行返回 (None, 0)。"""
    p03 = os.path.join(ROOT, '03-人物出现索引.md')
    if os.path.exists(p03):
        for ln in open(p03, encoding='utf-8', errors='replace'):
            if name not in ln:
                continue
            if '同名异人' in ln:
                m = re.search(r'字：([^）]+)', ln)
                sys.exit('× --person「%s」是**同名异人**（03 标 ⚠同名异人，字：%s）——'
                         '请用更具体的称呼（带字号或朝代）再试，避免讲错人'
                         % (name, m.group(1) if m else '多个'))
            m = re.search(r'★([^｜；]+)', ln)
            if m:
                return m.group(1).strip(), 0
            break
    rp = os.path.join(ROOT, 'tools', 'rag.py')
    if os.path.exists(rp):
        import subprocess
        try:
            p = subprocess.run([sys.executable, rp, 'locate', name, '--first', '--source', 'index'],
                               capture_output=True, timeout=90)
            out = (p.stdout + p.stderr).decode('utf-8', 'replace')
        except Exception:
            out = ''
        m = re.search(r'起读[^\n]*?：\s*([^｜\n]+?\.md)', out)
        if m:
            return re.sub(r'^data[\\/]', '', m.group(1).strip().replace('/', '\\')), 0
    return None, 0


def person_bounds(rel, name):
    """读本传文件，找 name 的开传句行号与**下一个传主**的开传句行号 → (a, b)。
    用于在**合传**里圈出该人自己的窗（如《明史·卷213·徐阶等》混了徐阶/高拱/张居正/张同敞，
    直接取全卷会把别人的窗也抽进来）。兼容两种开传句：`**张居正**，字叔大`（译文版加粗）与
    `苏辙，字子由`。找不到 name 的开传句 → (None, None)（退化为取全卷）。"""
    p = os.path.join(ROOT, 'data', rel)
    if not os.path.exists(p):
        return None, None
    RX = re.compile(r'^\*{0,2}([^>#｜\s\*]{1,6})\*{0,2}，字[^，。；]{1,6}')
    spots = []
    for i, ln in enumerate(open(p, encoding='utf-8', errors='replace'), 1):
        s = ln.strip()
        if not s or s.startswith(('>', '#')):
            continue
        m = RX.match(s)
        if m:
            spots.append((m.group(1).strip('*'), i))
    mine = None
    for k, (nm, _ln0) in enumerate(spots):
        # 兼容单名：古文明文开传常用单名（「岸，字公衡」，省姓），而用户/索引给全名（「杜岸」）。
        if nm == name or (len(nm) == 1 and name.endswith(nm)):
            mine = k
            break
    if mine is None:
        return None, None
    a = spots[mine][1]
    b = spots[mine + 1][1] if mine + 1 < len(spots) else None
    return a, b


def narrow_to_person(books, name, args):
    """按**人物**抽：把候选池收窄到"该人本传的那些情节窗"，再交给同一套抽签/输出/登记。
    为什么要它：`04` 的窗口只记「路径｜起行｜止行｜情节分｜首句」，**路径里没有人名**
    （宋史按「卷三百三十九·列传第九十八」命名），所以 `--topic` 只能命中"首句恰好写了人名"
    的那一窗——而一个人的传里往往有**多个**情节窗（实测苏辙传 **6 窗**，`--topic 苏辙` 只给 1 个）。
    定位用 03/07 索引，圈定用 person_bounds（合传只取该人的窗，不串到同卷他人）。"""
    rel, _ = locate_biography(name)
    if not rel:
        sys.exit('× --person「%s」定位不到专传——先跑 `python tools\\rag.py %s --fast` 看检索结果，'
                 '或确认该人在本库是否有传' % (name, name))
    a, b = person_bounds(rel, name)
    keep, seen = [], 0
    for book, wins in books:
        for w in wins:
            if w['path'].replace('/', '\\') != rel:
                continue
            seen += 1
            if a and w['s'] < a:
                continue   # 窗在 name 开传之前（合传里是前一个人）
            if b and w['s'] >= b:
                continue   # 窗已在下一个传主之后
            keep.append((book, w))
    if not keep:
        sys.exit('× --person「%s」的本传（%s）在 04 里没有可用情节窗' % (name, rel))
    if args.min_score:
        hi = [bw for bw in keep if bw[1]['score'] >= args.min_score]
        if hi:
            keep = hi
    by_book = {}
    for book, w in keep:
        by_book.setdefault(book, []).append(w)
    print('# --person %s：本传 %s（开传行 %s）｜候选窗 %d 个（04 里同路径共 %d 窗）'
          % (name, rel, a if a else '未圈定', len(keep), seen))
    if not a:
        print('  ⚠ 未圈定：本传未标名（找不到「%s，字…」开传句），候选窗可能含同卷他人——'
              'Read 后须先确认是本人再讲' % name)
    print('  （该传有多个窗，本次抽到其中一个；换一个就重跑。首行仍带签号。）\n')
    return list(by_book.items())


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument('--book', help='指定史书（支持部分名，逗号分隔多个）')
    ap.add_argument('--dynasty', help='按朝代抽签（歧义朝名会要求二选一）')
    ap.add_argument('--topic', help='按主题抽（匹配窗口的「首句＋路径」，如 玄武门、漕运）；'
                                    '主题内容本身请先用 rag.py 查')
    ap.add_argument('--person', help='按**人物**抽（"讲苏辙的故事"）：先 rag.py locate 定本传，'
                                     '再在该传的多个情节窗里抽一个——一个人的故事不止一个')
    ap.add_argument('--no-repeat', type=int, default=4,
                    help='排除最近 N 次抽到/讲过的史书，默认 4；0＝不排除')
    ap.add_argument('--exclude', help='排除指定史书（支持部分名，逗号分隔）——「换一本」用')
    ap.add_argument('--no-log', action='store_true',
                    help='本次不写 tools\\_draw_log.md（试抽/调试用；会关掉自动轮换依据）')
    ap.add_argument('--count', type=int, default=1, help='一次抽几个，史书互不重复，默认 1')
    ap.add_argument('--min-score', type=int, default=25, help='情节分下限，默认 25（有对话/冲突的窗；中位 27）')
    ap.add_argument('--flat', action='store_true', help='全库窗口等概率（默认分层）')
    ap.add_argument('--seed', type=int, help='固定随机种子（复现用）')
    ap.add_argument('--info', action='store_true', help='只列各书窗数与占比')
    ap.add_argument('--commit', action='store_true', help='把本次抽签记入 tools\\story_log.md')
    ap.add_argument('--commit-entry',
                    help='按条登记已讲窗口，格式 "书名|路径|起行|止行|情节分"（竖线可写 | 或 ｜）')
    ap.add_argument('--next', nargs='+',
                    help='续讲取点：给上一签的路径（可附已读止行，如 --next "18旧五代史\\现代文\\101_….md" 58）'
                         '→ 输出后续读取范围（同传后续行／同书紧邻卷次）')
    ap.add_argument('--check',
                    help='后置校验一段讲史回答（文件路径或 - 从 stdin）：只讲一遍（签号唯一）／'
                         '虚构故事须带「非真实历史」标注／点名故事（应点讲）须带《书名·卷次》出处／'
                         '1-alt 降级选自须首行写「引用 04 第 N 行」；FAIL 退出码 1')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if args.next is not None:
        sys.exit(show_next(SEP.join(args.next)))
    if args.check is not None:
        sys.exit(check_answer(args.check))
    if args.commit_entry is not None and not args.commit_entry.strip():
        ap.error('--commit-entry 不能为空')
    if args.count < 1:
        # 原先 --count 0 / -1 会 rc=0 却什么都没抽，末行还打印得像成功了
        sys.exit('× --count 必须 ≥ 1（收到 %d）；一次抽多个写 --count 3' % args.count)

    books = load_index()
    if not books:
        sys.exit('× 未解析到窗口。请确认 %s 存在且格式未变。' % INDEX)

    if args.person:
        # 点名要人时不做轮换排除（否则可能因"最近讲过宋史"而抽不到他）
        args.no_repeat = 0
        books = narrow_to_person(books, args.person, args)

    if args.commit_entry:
        raw = args.commit_entry.replace('|', SEP)
        parts = [p.strip() for p in raw.split(SEP)]
        if len(parts) < 4:
            sys.exit('× --commit-entry 至少要有 书名|路径|起行|止行')
        parts += ['?'] * (5 - len(parts))
        stamp = _dt.datetime.now().strftime('%Y-%m-%d %H:%M')
        commit([{'stamp': stamp, 'book': parts[0],
                 'win': {'path': parts[1], 's': parts[2], 'e': parts[3], 'score': parts[4]}}])
        print('已登记：%s ｜ %s（%s–%s）→ %s' % (parts[0], parts[1], parts[2], parts[3], LOG))
        return

    if args.info:
        if args.exclude:
            print('（--info 是全局分布视图，不应用 --exclude：下面统计含被排除的书）\n')
        total = sum(len(w) for _, w in books)
        bmap = dict(books)
        tzj = len(bmap.get('00资治通鉴', []))
        print('索引书节 %d 部 ｜ 窗口合计 %d ｜ 资治通鉴 %d 窗（%.1f%%）'
              % (len(books), total, tzj, 100.0 * tzj / total if total else 0.0))
        print('分层抽签下每部书的概率都是 1/%d ≈ %.1f%%，通鉴不再有额外权重。\n'
              % (len(books), 100.0 / len(books)))
        for b, w in books:
            note = BOOK_NOTE.get(b)
            print('  %-18s %5d 窗  %5.1f%%%s' % (b, len(w), 100.0 * len(w) / total,
                                                '   ← %s' % note if note else ''))
        return

    # 两本账合并取最近 N 次：抽签记录（脚本自动写，可靠）＋ 讲完台账（人工登记）
    merged = read_draw_log() + read_log()
    merged.sort(key=lambda x: x[0])
    recent = []
    if args.no_repeat > 0:
        for _d, b in merged[-args.no_repeat:]:
            if b not in recent:
                recent.append(b)
    books = eligible_books(books, args, recent)
    rng = random.Random(args.seed)
    picks = draw(books, args, rng)
    rows = show(books, args, picks)
    if args.commit:
        commit(rows)
        print('已记入讲完台账 %s' % LOG)
    if args.no_log:
        print('（--no-log：本次未写抽签记录 %s）' % DRAW_LOG)
    else:
        commit(rows, DRAW_LOG)
        print('已记入抽签记录 %s（--no-repeat 会读它；试抽请加 --no-log）' % DRAW_LOG)


if __name__ == '__main__':
    main()
