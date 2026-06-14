"""
Google Sheets / Drive 認証セットアップスクリプト
実行: python3 setup_google.py
"""
import os
import sys
from pathlib import Path

CREDENTIALS_DIR = Path(__file__).parent / "credentials"
CREDENTIALS_DIR.mkdir(exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

def main():
    client_secret_path = CREDENTIALS_DIR / "client_secret.json"
    token_path = CREDENTIALS_DIR / "token.json"

    if not client_secret_path.exists():
        print("=" * 60)
        print("client_secret.json が見つかりません")
        print("以下の手順でファイルを取得してください：")
        print()
        print("1. https://console.cloud.google.com/ を開く")
        print("2. 新しいプロジェクトを作成（例: receipt-organizer）")
        print("3. 左メニュー → APIとサービス → ライブラリ")
        print("   「Google Sheets API」を有効化")
        print("   「Google Drive API」を有効化")
        print("4. 左メニュー → APIとサービス → 認証情報")
        print("   「認証情報を作成」→「OAuthクライアントID」")
        print("   アプリの種類: デスクトップアプリ")
        print("   名前: receipt-organizer")
        print("5. 作成したら「JSONをダウンロード」")
        print(f"6. ダウンロードしたファイルを以下に保存:")
        print(f"   {client_secret_path}")
        print()
        print("7. このスクリプトを再実行: python3 setup_google.py")
        print("=" * 60)
        return

    print("client_secret.json を検出しました。認証を開始します...")

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = None
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(client_secret_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(token_path, "w") as f:
                f.write(creds.to_json())

        print()
        print("✅ 認証成功！token.json を保存しました。")
        print()
        print("次のステップ:")
        print("1. Google スプレッドシートを新規作成")
        print("   https://sheets.google.com/")
        print("2. スプレッドシートのURLからIDをコピー")
        print("   例: https://docs.google.com/spreadsheets/d/[ここ]/edit")
        print("3. アプリの ⚙️ 設定 → スプレッドシートID に貼り付け")

    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
