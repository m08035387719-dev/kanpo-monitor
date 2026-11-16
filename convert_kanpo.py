import os
import tempfile
import datetime
import requests
import re
import traceback
import fitz  # PyMuPDF
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- 設定項目 ---
DRIVE_FOLDER_ID = "1Lle6cIKDuGGpABKMh9gy_iegRAlWX7pI"
KANPO_TOP_URL = "https://www.kanpo.go.jp/index.html"
USER_AGENT = "KanpoImageBot/1.0 (credit monitoring)"

def get_today_jst_str():
    now_utc = datetime.datetime.utcnow()
    now_jst = now_utc + datetime.timedelta(hours=9)
    return now_jst.strftime("%Y%m%d"), now_jst.strftime("%Y-%m-%d")

def fetch_kanpo_pdf_url(date_compact):
    headers = {"User-Agent": USER_AGENT}
    try:
        res = requests.get(KANPO_TOP_URL, headers=headers, timeout=15)
        if res.status_code != 200:
            return None
        
        html = res.text
        pattern = re.compile(rf'href="([^"]*?{date_compact}h\d+[^"]*?full[^"]*?\.html)"')
        matches = pattern.findall(html)
        
        full_html_url = None
        for path in matches:
            if path.startswith("http"):
                full_html_url = path
            elif path.startswith("/"):
                full_html_url = "https://www.kanpo.go.jp" + path
            else:
                full_html_url = "https://www.kanpo.go.jp/" + path.replace("./", "")
            break
            
        if not full_html_url:
            print("⚠️ フルコンテンツHTMLリンクが見つかりませんでした。")
            return None
            
        v_res = requests.get(full_html_url, headers=headers, timeout=15)
        if v_res.status_code != 200:
            return None
            
        pdf_match = re.search(r'["\']([^"\']+\.pdf)["\']', v_res.text, re.IGNORECASE)
        if pdf_match:
            raw_pdf = pdf_match.group(1)
            if raw_pdf.startswith("http"):
                return raw_pdf
            elif raw_pdf.startswith("/"):
                return "https://www.kanpo.go.jp" + raw_pdf
            else:
                base_dir = full_html_url[:full_html_url.rfind("/") + 1]
                return base_dir + raw_pdf.replace("./", "")
        else:
            return full_html_url.replace(".html", ".pdf")

    except Exception as e:
        print(f"❌ エラーが発生しました:")
        traceback.print_exc()
        return None

def upload_to_drive(service, file_path, file_name, folder_id):
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaFileUpload(file_path, mimetype='image/jpeg', resumable=True)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    print(f"📤 アップロード成功: {file_name} (ID: {file.get('id')})")

def main():
    date_compact, date_hyphen = get_today_jst_str()
    print(f"=== 官報画像化処理開始: {date_hyphen} ===")

    # OAuth 2.0 認証情報の復元（リフレッシュトークンを使用）
    client_id = os.environ.get("GCP_CLIENT_ID")
    client_secret = os.environ.get("GCP_CLIENT_SECRET")
    refresh_token = os.environ.get("GCP_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise ValueError("❌ 必要なOAuth環境変数が設定されていません。")

    credentials = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    drive_service = build('drive', 'v3', credentials=credentials)

    pdf_url = fetch_kanpo_pdf_url(date_compact)
    if not pdf_url:
        print("PDF URLの取得に失敗したため終了します。")
        return

    print(f"📥 官報PDFを取得中: {pdf_url}")
    headers = {"User-Agent": USER_AGENT}
    pdf_res = requests.get(pdf_url, headers=headers, timeout=30)
    if pdf_res.status_code != 200:
        print(f"❌ PDFのダウンロードに失敗しました: HTTP {pdf_res.status_code}")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, "kanpo.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_res.content)

        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        print(f"📄 総ページ数: {total_pages} ページ。JPEG変換を開始します...")

        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            
            img_name = f"kanpo_{date_compact}_p{page_num + 1:03d}.jpg"
            img_path = os.path.join(tmpdir, img_name)
            pix.save(img_path)

            upload_to_drive(drive_service, img_path, img_name, DRIVE_FOLDER_ID)

    print("✅ すべてのページのJPEG化およびドライブ保存が完了しました。")

if __name__ == "__main__":
    main()
