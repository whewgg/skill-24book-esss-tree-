# -*- coding: utf-8 -*-
"""猜谜题交付闸（硬条件）——答案是「一个人」还是「无法唯一确定」，只有两种合法形态。

判据（2026-09-24 用户定：必须全中才能说，有不符合就只能说无法确定）：
  ① **给出人名**  ⇒ 必须**显式声明「无不符」**（＝逐条全中，无一条未载/存疑/矛盾）
  ② **答「无法唯一确定」** ⇒ 必须**列出候选**（含各自出局条数）
  ✗ 出现「不符／未载／未查到／存疑／矛盾」等字样，**却仍给出具体人名** ⇒ 不合格
    （⚠ 「本库未载」也算不符——**无法证实即不得作答**，这正是孟昶题误答的形态）
  ✗ 出现「宜按…理解／题面或有误／需宽松解读」等**替题面打补丁**的措辞 ⇒ 不合格

用法：python tools\\check_guess.py <答案.txt>
退出码：0 合格；1 不合格；2 用法错。
"""
import re
import sys
from pathlib import Path

USAGE = r'用法：python tools\check_guess.py <答案.txt>'
ABSTAIN = ('无法唯一确定', '无法确定', '无法判定', '不能确定', '无法给出唯一')
PATCH = ('宜按', '或有误', '有偏差', '需宽松', '宽松解读', '可理解为', '题面或')
MISMATCH = ('不符', '对不上', '有出入', '未载', '未查到', '未找到', '未见', '未收',
            '存疑', '矛盾', '相反', '无法证实')
OKALL = ('无不符', '无不符合', '逐条符合', '全部符合', '六条均符', '均符合', '条条符合')
# ⚠ 否定式先行剥除：「无不符」含「不符」子串，不剥会**自己判自己不通过**（案例 B 实测踩到）。
NEG_RX = re.compile(r'无(?:不)?(?:符|出入|未载|未见|未收|存疑|矛盾|异常)')


def check(text):
    """返回 (是否合格, 结论行列表)。"""
    lines = []
    m = re.search(r'答案[：:＝=]\s*([^\s，。；、]+)', text)
    ans = m.group(1) if m else ''
    abstain = any(k in text for k in ABSTAIN)
    patch = [k for k in PATCH if k in text]
    body = NEG_RX.sub('', text)          # 剥掉"无不符"类否定式后再判不符
    mism = [k for k in MISMATCH if k in body]
    okall = [k for k in OKALL if k in text]
    bad = []
    if patch:
        bad.append('出现替题面打补丁的措辞：%s（不得用"宜按…理解／题面或有误"保住候选）' % '、'.join(patch))
    if abstain:
        # 形态②：弃权——合规，但须列候选
        if '候选' not in text:
            bad.append('答「无法唯一确定」但未列出候选与各自出局条数')
        else:
            lines.append('形态②（无法唯一确定）＋已列候选 —— 合规')
    else:
        # 形态①：给答案——必须全中且显式声明
        if not ans:
            bad.append('既未给出「答案：X」，也未答「无法唯一确定」——形态不明')
        else:
            lines.append('形态①（给出答案「%s」）' % ans)
            if not okall:
                bad.append('给了具体答案，却未显式声明「无不符」——按硬条件须逐条全中并写明「无不符」')
            if mism:
                bad.append('给了具体答案，却出现不符/未载等字样（%s）——'
                           '按硬条件：有不符合就只能改答「无法唯一确定」' % '、'.join(mism))
    return (not bad), lines + [('⛔ ' + b) for b in bad]


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith('-'):
        print(USAGE)
        return 2
    p = Path(sys.argv[1])
    if not p.exists():
        print('× 文件不存在：%s\n%s' % (p, USAGE))
        return 2
    ok, detail = check(p.read_text('utf-8', errors='replace'))
    for d in detail:
        print('  ' + d)
    if ok:
        print('# 结论：合格（形态②弃权：已列候选）'
              if any(k in p.read_text('utf-8', errors='replace') for k in ABSTAIN)
              else '# 结论：合格（形态①全中：已声明无不符）')
        return 0
    print('# 结论：不合格 —— 请改为「无法唯一确定」或补全并声明「无不符」')
    return 1


if __name__ == '__main__':
    sys.exit(main())
