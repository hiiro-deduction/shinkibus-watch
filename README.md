# shinkibus-watch

神姫バス公式サイトの「路線バス新着情報」を毎朝チェックし、**お盆ダイヤ・年末年始ダイヤ・運休・臨時便**などの告知が出たら Discord / メールに通知する。あわせて非公式 RSS フィード (`feed.xml`) を生成する。

- 監視対象: <https://www.shinkibus.co.jp/sys/frames/lists01>（路線バスのみ。旅行・PR系は別タブなので混ざらない）
- 実行基盤: GitHub Actions（無料枠内）
- 依存ライブラリ: **なし**（Python 標準ライブラリのみ）

## セットアップ

1. **public リポジトリ**を作り、このディレクトリの中身をそのまま置いて push する
   （RSS を raw URL や GitHub Pages で読むため。private でも通知だけなら動く）

   ```
   .
   ├── watch.py
   ├── test_parse.py
   ├── test_flow.py
   └── .github/workflows/watch.yml
   ```

2. 通知先を Settings → Secrets and variables → Actions に登録する（**どちらか片方だけでよい**）

   **Discord（推奨・設定が一番楽）**

   | Secret | 値 |
   |---|---|
   | `DISCORD_WEBHOOK_URL` | サーバー設定 → 連携サービス → ウェブフック で発行した URL |

   **メール（Gmail の例）**

   | Secret | 値 |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `465` |
   | `SMTP_USER` | Gmail アドレス |
   | `SMTP_PASS` | [アプリパスワード](https://myaccount.google.com/apppasswords)（通常のパスワードは不可） |
   | `MAIL_TO` | 受信したいアドレス |
   | `MAIL_FROM` | 省略可（既定は `SMTP_USER`） |

3. Actions タブから `shinkibus-watch` を **Run workflow** で1回手動実行する
   - 初回は「現在載っている記事を state に記録するだけ」で通知は飛ばない（過去記事で通知が爆発しないため）
   - 2回目以降、新着・タイトル更新があったときだけ通知される

4. （任意）RSS で読みたい場合
   - 手軽: `https://raw.githubusercontent.com/<user>/<repo>/main/feed.xml` をリーダーに登録
   - きれいにやる: Settings → Pages で `main` ブランチのルートを公開し、
     `https://<user>.github.io/<repo>/feed.xml` を購読。あわせてリポジトリ変数
     `FEED_SELF_URL` に同じ URL を入れておくと `atom:link rel="self"` が入る

## 設定できる変数（Variables、任意）

| 変数 | 既定 | 説明 |
|---|---|---|
| `KEYWORDS` | ダイヤ,運休,運行,臨時,迂回,経路変更,減便,増便,時刻,休止,廃止,路線 | タイトルにこれらを含む記事だけ通知（カンマ区切り） |
| `NOTIFY_ALL` | 空 | `1` にするとキーワードで絞らず全件通知 |
| `FEED_SELF_URL` | 空 | 公開する feed.xml の URL |

実行時刻はワークフローの cron（既定 `30 21 * * *` = 06:30 JST）で変更する。

## 動作の要点

- **記事の同一性は URL の ID**（`/sys/frames/view/2539` の `2539`）で判定する。日付やタイトルが書き換わっても取りこぼさない
- **タイトルの書き換えも検知**する。神姫バスは「【8月5日内容更新】…」のように既存記事を更新することがあるため
- **記事が0件しか取れなかったら異常終了する**（終了コード 2）。サイトの HTML 構造が変わったときに「新着なし」と静かに誤認するのが一番危険なので、あえて失敗させる。GitHub は既定でワークフロー失敗をメール通知するので、これが最後の砦になる
- **CSS セレクタに依存しない**パーサにしてある。リンク周辺から日付・カテゴリを推定するので、多少のマークアップ変更では壊れない
- 通知対象の記事については、本文ページから **PDF の直リンク**も拾って一緒に通知する
- ページから消えた古い記事も `state.json` に残り、RSS の履歴として保持される（最大50件）

## 既知の注意点

- **60日ルール**: GitHub は60日間コミットのないリポジトリのスケジュール実行を自動停止する。本スクリプトは毎回 `state.json` の `updated_at` を更新して毎日コミットするため、これに引っかからない。日次コミットのノイズが嫌なら、代わりに月1回何かをコミットする運用にして `updated_at` の更新をやめてもよい
- cron は数分〜十数分遅れることがある。出発時刻ぎりぎりに設定しない
- スクレイピングなので、サイトのリニューアルで壊れる可能性はある。上記のとおり壊れたら失敗するようにしてあるので、失敗メールが来たらパーサを直す

## ローカルでのテスト

```bash
python3 test_parse.py   # HTML パーサ（2種類のマークアップ）
python3 test_flow.py    # 初回シード → 新着検知 → タイトル更新検知 → 異常系
```

実際に取得して挙動を見る場合:

```bash
STATE_PATH=/tmp/state.json FEED_PATH=/tmp/feed.xml python3 watch.py   # 初回=シードのみ
STATE_PATH=/tmp/state.json FEED_PATH=/tmp/feed.xml NOTIFY_ALL=1 python3 watch.py
```

## この仕組みでカバーできないこと

告知の**掲載自体が遅い/ない**ケースは拾えない。当日の実際のダイヤは
[神姫バスNAVI](https://navi.shinkibus.jp/snk/) の運行日指定検索が最も正確なので、
通知が来た週は前夜に NAVI で確認するのが確実。
