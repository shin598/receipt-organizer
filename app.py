import os
import json
import base64
import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from PIL import Image
import anthropic
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
import io

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

BASE_DIR = Path(__file__).parent

# ローカル開発用フォールバック
LOCAL_RECEIPTS_DIR = BASE_DIR / "receipts"
LOCAL_CARD_DIR = LOCAL_RECEIPTS_DIR / "カード"
LOCAL_CASH_DIR = LOCAL_RECEIPTS_DIR / "現金"

GOOGLE_ENABLED = False
try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
    import google.auth.transport.requests
    GOOGLE_ENABLED = True
except ImportError:
    pass


def get_google_creds():
    """環境変数またはファイルからGoogle認証情報を取得"""
    if not GOOGLE_ENABLED:
        return None

    # 環境変数から取得（Railway用）
    token_json = os.environ.get("GOOGLE_TOKEN_JSON")
    if token_json:
        token_data = json.loads(token_json)
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=token_data.get("client_id"),
            client_secret=token_data.get("client_secret"),
            scopes=token_data.get("scopes"),
        )
    else:
        # ローカルファイルから取得
        token_path = BASE_DIR / "credentials" / "token.json"
        if not token_path.exists():
            return None
        creds = Credentials.from_authorized_user_file(str(token_path))

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(google.auth.transport.requests.Request())
        except Exception:
            return None
    return creds


def get_config():
    """設定を環境変数またはconfig.jsonから取得"""
    config = {
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "spreadsheet_id": os.environ.get("SPREADSHEET_ID", ""),
        "drive_folder_card": os.environ.get("DRIVE_FOLDER_CARD", ""),
        "drive_folder_cash": os.environ.get("DRIVE_FOLDER_CASH", ""),
    }
    # ローカルのconfig.jsonで上書き
    config_path = BASE_DIR / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            local = json.load(f)
        for k, v in local.items():
            if v:
                config[k] = v
    return config


def save_config_local(data: dict):
    config_path = BASE_DIR / "config.json"
    existing = {}
    if config_path.exists():
        with open(config_path) as f:
            existing = json.load(f)
    existing.update(data)
    with open(config_path, 'w') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def analyze_receipt(image_bytes: bytes, mime_type: str, api_key: str) -> dict:
    """Claude APIで領収書を解析"""
    client = anthropic.Anthropic(api_key=api_key)
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime_type, "data": image_b64},
                },
                {
                    "type": "text",
                    "text": """この領収書の画像から以下の情報を抽出してJSON形式で返してください。
情報が読み取れない場合は null を返してください。

{
  "store_name": "店名",
  "date": "日付 (YYYY-MM-DD形式)",
  "amount": 金額(数字のみ、円単位),
  "payment_method": "カード" または "現金" (不明な場合は "現金"),
  "category": "カテゴリ (食費/交通費/消耗品/接待/その他 のどれか)",
  "memo": "その他メモ"
}

JSONのみ返してください。説明は不要です。""",
                },
            ],
        }],
    )

    text = message.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def image_to_pdf_bytes(image_bytes: bytes) -> bytes:
    """画像をPDFに変換してバイト列で返す"""
    img = Image.open(io.BytesIO(image_bytes))
    try:
        from PIL import ExifTags
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == 'Orientation':
                    if value == 3:
                        img = img.rotate(180, expand=True)
                    elif value == 6:
                        img = img.rotate(270, expand=True)
                    elif value == 8:
                        img = img.rotate(90, expand=True)
    except Exception:
        pass

    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')

    img_width, img_height = img.size
    page_width, page_height = A4
    ratio = min(page_width / img_width, page_height / img_height) * 0.9
    new_width = img_width * ratio
    new_height = img_height * ratio
    x = (page_width - new_width) / 2
    y = (page_height - new_height) / 2

    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='JPEG', quality=90)
    img_buffer.seek(0)
    c.drawImage(ImageReader(img_buffer), x, y, width=new_width, height=new_height)
    c.save()
    return pdf_buffer.getvalue()


def upload_to_drive(pdf_bytes: bytes, filename: str, folder_id: str, creds) -> str:
    """Google DriveにPDFをアップロードしてリンクを返す"""
    service = build('drive', 'v3', credentials=creds)
    file_metadata = {'name': filename, 'mimeType': 'application/pdf'}
    if folder_id:
        file_metadata['parents'] = [folder_id]

    media = MediaIoBaseUpload(io.BytesIO(pdf_bytes), mimetype='application/pdf')
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id,webViewLink'
    ).execute()
    return file.get('webViewLink', '')


def save_pdf_locally(pdf_bytes: bytes, filename: str, payment: str) -> str:
    """ローカルにPDFを保存してパスを返す"""
    folder = LOCAL_CARD_DIR if payment == 'カード' else LOCAL_CASH_DIR
    folder.mkdir(parents=True, exist_ok=True)
    pdf_path = folder / filename
    counter = 1
    while pdf_path.exists():
        name_part = filename.rsplit('.', 1)[0]
        pdf_path = folder / f"{name_part}_{counter}.pdf"
        counter += 1
    with open(pdf_path, 'wb') as f:
        f.write(pdf_bytes)
    return str(pdf_path.relative_to(BASE_DIR))


def append_to_spreadsheet(data: dict, spreadsheet_id: str, creds) -> bool:
    """Google スプレッドシートに行を追加"""
    service = build('sheets', 'v4', credentials=creds)

    # ヘッダー確認・初期化
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range='領収書!A1:H1'
        ).execute()
        if not result.get('values'):
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id, range='領収書!A1:H1',
                valueInputOption='RAW',
                body={'values': [['日付', '店名', '金額', '支払方法', 'カテゴリ', 'メモ', 'PDFリンク', 'ファイル名']]}
            ).execute()
    except Exception:
        pass

    row = [
        data.get('date', ''),
        data.get('store_name', ''),
        data.get('amount', ''),
        data.get('payment_method', ''),
        data.get('category', ''),
        data.get('memo', ''),
        data.get('drive_link', ''),
        data.get('pdf_filename', ''),
    ]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range='領収書!A:H',
        valueInputOption='USER_ENTERED',
        body={'values': [row]}
    ).execute()
    return True


def get_drive_files(creds, folder_card: str, folder_cash: str) -> list:
    """Google Driveからファイル一覧を取得"""
    service = build('drive', 'v3', credentials=creds)
    records = []
    for folder_name, folder_id in [('カード', folder_card), ('現金', folder_cash)]:
        if not folder_id:
            continue
        try:
            results = service.files().list(
                q=f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false",
                fields="files(id,name,modifiedTime,webViewLink)",
                orderBy="modifiedTime desc"
            ).execute()
            for f in results.get('files', []):
                records.append({
                    'name': f['name'],
                    'folder': folder_name,
                    'drive_link': f.get('webViewLink', ''),
                    'modified': f['modifiedTime'][:10],
                })
        except Exception:
            pass
    return records


@app.route('/')
def index():
    config = get_config()
    has_api_key = bool(config.get('anthropic_api_key'))
    has_spreadsheet = bool(config.get('spreadsheet_id'))
    creds = get_google_creds()
    return render_template('index.html',
                           has_api_key=has_api_key,
                           has_spreadsheet=has_spreadsheet,
                           google_enabled=bool(creds))


@app.route('/upload', methods=['POST'])
def upload():
    if 'file' not in request.files:
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'ファイルが選択されていません'}), 400

    config = get_config()
    api_key = config.get('anthropic_api_key')
    if not api_key:
        return jsonify({'error': 'Anthropic APIキーが設定されていません'}), 400

    image_bytes = file.read()
    mime_type = file.content_type or 'image/jpeg'
    if mime_type not in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
        mime_type = 'image/jpeg'

    # AI解析
    try:
        receipt_data = analyze_receipt(image_bytes, mime_type, api_key)
    except Exception as e:
        return jsonify({'error': f'AI解析エラー: {str(e)}'}), 500

    # PDF変換
    try:
        pdf_bytes = image_to_pdf_bytes(image_bytes)
    except Exception as e:
        return jsonify({'error': f'PDF変換エラー: {str(e)}'}), 500

    payment = receipt_data.get('payment_method', '現金')
    date_str = receipt_data.get('date') or datetime.date.today().isoformat()
    store = receipt_data.get('store_name') or 'unknown'
    safe_store = "".join(c for c in store if c.isalnum() or c in '-_' or '぀' <= c <= '鿿')
    filename = f"{date_str}_{safe_store}.pdf"
    receipt_data['pdf_filename'] = filename
    receipt_data['folder'] = payment

    # Google Drive保存
    creds = get_google_creds()
    drive_link = ''
    if creds:
        folder_id = config.get('drive_folder_card') if payment == 'カード' else config.get('drive_folder_cash')
        try:
            drive_link = upload_to_drive(pdf_bytes, filename, folder_id or '', creds)
        except Exception as e:
            receipt_data['drive_error'] = str(e)

    # ローカル保存（フォールバック）
    if not drive_link:
        local_path = save_pdf_locally(pdf_bytes, filename, payment)
        receipt_data['pdf_path'] = local_path

    receipt_data['drive_link'] = drive_link

    # スプレッドシート更新
    sheets_ok = False
    spreadsheet_id = config.get('spreadsheet_id')
    if spreadsheet_id and creds:
        try:
            sheets_ok = append_to_spreadsheet(receipt_data, spreadsheet_id, creds)
        except Exception as e:
            receipt_data['sheets_error'] = str(e)

    receipt_data['sheets_updated'] = sheets_ok
    return jsonify({'success': True, 'data': receipt_data})


@app.route('/receipts/<path:filename>')
def serve_pdf(filename):
    pdf_path = LOCAL_RECEIPTS_DIR / filename
    if pdf_path.exists():
        return send_file(str(pdf_path), mimetype='application/pdf')
    return jsonify({'error': 'Not found'}), 404


@app.route('/config', methods=['GET', 'POST'])
def config_page():
    if request.method == 'POST':
        data = request.json
        save_config_local({k: v for k, v in data.items() if v})
        return jsonify({'success': True})
    config = get_config()
    config['anthropic_api_key'] = '***' if config.get('anthropic_api_key') else ''
    return jsonify(config)


@app.route('/history')
def history():
    config = get_config()
    creds = get_google_creds()

    # Google Driveから取得
    if creds and (config.get('drive_folder_card') or config.get('drive_folder_cash')):
        records = get_drive_files(creds, config.get('drive_folder_card', ''), config.get('drive_folder_cash', ''))
        if records:
            return jsonify(records)

    # ローカルファイルから取得（フォールバック）
    records = []
    for folder_name, folder in [('カード', LOCAL_CARD_DIR), ('現金', LOCAL_CASH_DIR)]:
        if not folder.exists():
            continue
        for pdf in sorted(folder.glob('*.pdf'), reverse=True):
            records.append({
                'name': pdf.name,
                'folder': folder_name,
                'path': f"receipts/{folder_name}/{pdf.name}",
                'drive_link': '',
                'modified': datetime.datetime.fromtimestamp(pdf.stat().st_mtime).strftime('%Y-%m-%d'),
            })
    return jsonify(records)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(debug=False, host='0.0.0.0', port=port)
