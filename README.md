# TwiFFArchiver

X（Twitter）の **フォロー／フォロワー一覧** を取得して JSON に保存する CLI ツールです。

指定した `@アカウント` について、各ユーザーの次の3項目をメモします。

| フィールド | 意味 |
|---|---|
| `display_name` | ユーザー名（表示名） |
| `account_name` | アカウント名（`@` なし） |
| `user_id` | ユーザーID（文字列） |

将来の「この一覧を自動フォローする」ツール向けに、`user_id` を必ず含めた JSON を出します。  
フォロー操作自体はこのツールの対象外です。鍵垢は想定していません。

## 必要環境

- Python 3.11+
- X にログイン済みブラウザから取れる Cookie（`auth_token` / `ct0`）

> 非公式の内部 API を使います。仕様変更で壊れる可能性があり、大量取得はレート制限やアカウント制限のリスクがあります。自己責任で使ってください。

## インストール

```bash
cd TwiFFArchiver
python -m pip install -e .
```

ブラウザから Cookie を自動取得する場合:

```bash
python -m pip install -e ".[browser]"
```

## Cookie の設定

1. [x.com](https://x.com) にログイン
2. 開発者ツール → Application → Cookies → `x.com`
3. `auth_token` と `ct0` をコピー

```bash
python -m twiffarchiver auth set --auth-token YOUR_AUTH_TOKEN --ct0 YOUR_CT0
```

または対話入力:

```bash
python -m twiffarchiver auth set
```

Chrome などから直接読む（任意）:

```bash
python -m twiffarchiver auth set --from-browser chrome
```

確認:

```bash
python -m twiffarchiver auth status
```

環境変数でも渡せます。

- `TWIFFARCHIVER_AUTH_TOKEN` / `TWIFFARCHIVER_CT0`
- または `AUTH_TOKEN` / `CT0`

認証情報は `~/.config/twiffarchiver/credentials.json` に保存されます（リポジトリには入れないでください）。

## 使い方

```bash
python -m twiffarchiver fetch @hogehoge
python -m twiffarchiver fetch hogehoge --out ./out
python -m twiffarchiver fetch @hogehoge --following-only
python -m twiffarchiver fetch @hogehoge --followers-only
```

### 出力例

```text
out/
  hogehoge_20260721T220000Z/
    meta.json
    following.json
    followers.json
```

`following.json` / `followers.json` の各要素:

```json
{
  "user_id": "1234567890",
  "account_name": "example",
  "display_name": "Example User"
}
```

`meta.json` には対象アカウント、取得時刻、件数、ツールバージョンが入るので、自動フォロー側はここ＋一覧 JSON を読めば足ります。

## GraphQL query ID

X は内部 GraphQL の query ID を定期的に回します。起動時に JS バンドルから取得を試み、失敗時は内蔵のフォールバック ID → REST API に降ります。

強制更新:

```bash
python -m twiffarchiver query-ids --refresh
```

## 将来の自動フォローとの関係

- このツールは **保存のみ**
- 出力の `user_id` を順に読む想定
- following / followers はファイル分離済み

## ライセンス

MIT
