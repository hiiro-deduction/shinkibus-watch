"""fetch をスタブして、初回シード → 新着検知 → タイトル更新検知 の流れを確認する。"""

import os
import tempfile

tmp = tempfile.mkdtemp()
os.environ["STATE_PATH"] = os.path.join(tmp, "state.json")
os.environ["FEED_PATH"] = os.path.join(tmp, "feed.xml")

import watch  # noqa: E402

PAGE = """
<ul>
  <li><p class="date">2026.07.29 <span>路線バス</span></p>
  <a href="/sys/frames/view/2539">2026年 お盆ダイヤについて【一般路線バス】</a></li>
  <li><p class="date">2026.07.06 <span>路線バス</span></p>
  <a href="/sys/frames/view/2526">姫路セントラルパークへの臨時直行便を運行いたします！</a></li>
</ul>
"""

NEW_ITEM = """
  <li><p class="date">2026.12.01 <span>路線バス</span></p>
  <a href="/sys/frames/view/2600">2026年 年末年始ダイヤについて</a></li>
"""

RENAMED = PAGE.replace(
    "2026年 お盆ダイヤについて【一般路線バス】",
    "【8月5日内容更新】2026年 お盆ダイヤについて【一般路線バス】",
)

sent = []
watch.notify_discord = lambda s, e: sent.append(("discord", s, [x.id for x in e]))
watch.notify_email = lambda s, e: sent.append(("email", s, [x.id for x in e]))
watch.find_pdf_url = lambda e: f"https://www.shinkibus.co.jp/sys/whatsnew/download/{e.id}"

pages = [PAGE, PAGE.replace("</ul>", NEW_ITEM + "</ul>"), RENAMED]
labels = ["初回", "新着1件", "タイトル更新"]

for label, page in zip(labels, pages):
    watch.fetch = lambda url, timeout=30, _p=page: _p
    print(f"\n=== {label} ===")
    rc = watch.main()
    assert rc == 0, rc
    print("通知:", sent[-1] if sent else "なし")

assert sent[0][2] == ["2600"] and sent[1][2] == ["2600"], sent
assert sent[2][2] == ["2539"] and sent[3][2] == ["2539"], sent
assert len(sent) == 4  # discord+email が2回ずつ

# 消えた記事も履歴として残る
import json  # noqa: E402

state = json.load(open(os.environ["STATE_PATH"], encoding="utf-8"))
assert set(state["entries"]) == {"2539", "2526", "2600"}, state["entries"].keys()
feed = open(os.environ["FEED_PATH"], encoding="utf-8").read()
assert "年末年始ダイヤ" in feed and "【8月5日内容更新】" in feed

# 空ページなら異常終了する（静かに「新着なし」と誤認しない）
watch.fetch = lambda url, timeout=30: "<html><body>メンテナンス中</body></html>"
assert watch.main() == 2

print("\nOK: フロー全体のアサーションを通過しました")
