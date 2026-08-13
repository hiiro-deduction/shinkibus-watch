"""パーサの動作確認（HTML構造が変わっても壊れにくいかのスモークテスト）。"""

from watch import parse_entries

VARIANT_A = """
<ul class="newsList">
  <li>
    <p class="date">2026.08.05 <span class="cat cat01">路線バス</span></p>
    <p class="ttl"><a href="/sys/frames/view/2548">お盆期間[8月11日～16日]のポートループ・シティループの運行内容変更について</a></p>
  </li>
  <li>
    <p class="date">2026.07.29 <span class="cat cat01">路線バス</span></p>
    <p class="ttl"><a href="https://www.shinkibus.co.jp/sys/frames/view/2539">2026年 お盆ダイヤについて【一般路線バス・コミュニティバス】</a></p>
  </li>
  <li>
    <p class="date">2026.07.29 <span class="cat cat01">路線バス</span></p>
    <p class="ttl"><a href="/sys/frames/view/2542">漆芸家・江藤雄造氏とタイアップした電気バスを運行します！</a></p>
  </li>
</ul>
"""

# リンクが行全体を包み、日付がリンクの内側にあるパターン
VARIANT_B = """
<div class="news">
  <a href="/sys/frames/view/2548" class="row"><span class="date">2026.08.05</span>
  <span class="cat">路線バス</span><span class="ttl">お盆期間の運行内容変更について</span></a>
  <a href="/sys/frames/view/2539" class="row"><span class="date">2026.07.29</span>
  <span class="cat">路線バス</span><span class="ttl">2026年 お盆ダイヤについて</span></a>
</div>
"""


def show(name, html):
    entries = parse_entries(html)
    print(f"--- {name}: {len(entries)} 件 ---")
    for e in entries:
        print(f"  id={e.id} date={e.date!r} cat={e.category!r} url={e.url}")
        print(f"    {e.title}")
    return entries


a = show("VARIANT_A", VARIANT_A)
assert len(a) == 3
assert a[0].id == "2548"
assert a[0].date == "2026.08.05"
assert a[0].category == "路線バス"
assert a[1].url == "https://www.shinkibus.co.jp/sys/frames/view/2539"
assert a[2].url == "https://www.shinkibus.co.jp/sys/frames/view/2542"

b = show("VARIANT_B", VARIANT_B)
assert len(b) == 2
assert b[0].date == "2026.08.05"
assert b[1].date == "2026.07.29"
assert b[1].title == "2026年 お盆ダイヤについて"

# キーワード判定
from watch import DEFAULT_KEYWORDS, matches_keywords, build_feed

assert matches_keywords(a[1], DEFAULT_KEYWORDS) is True  # 「ダイヤ」を含む
assert matches_keywords(a[2], DEFAULT_KEYWORDS) is True  # 「運行」を含む

feed = build_feed(a)
assert "<rss" in feed and "お盆ダイヤ" in feed and "shinkibus-2539" in feed
print("--- feed.xml 先頭 ---")
print("\n".join(feed.splitlines()[:14]))
print("\nOK: すべてのアサーションを通過しました")
