import os
import json
import tempfile
import datetime
import requests
import re
import traceback
import fitz  # PyMuPDF
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- 設定項目 ---
DRIVE_FOLDER_ID = "1Lle6cIKDuGGpABKMh9gy_iegRAlWX7pI"
KANPO_TOP_URL = "https://www.kanpo.go.jp/index.html"
KANPO_BASE_URL = "https://www.kanpo.go.jp/"
USER_AGENT = "KanpoImageBot/1.0 (credit monitoring)"

def get_today_jst_str():
    now_utc = datetime.datetime.utcnow()
    now_jst = now_utc + datetime.timedelta(hours=9)
    return now_jst.strftime("%Y%m%d"), now_jst.strftime("%Y-%m-%d")

def fetch_kanpo_pdf_url(date_compact):
    headers = {"User-Agent": USER_AGENT}
    try:
        res = requests.get(KANPO_TOP_URL, headers=headers, timeout=15)
        print(f"トップページ取得 HTTPステータス: {res.status_code}")
        if res.status_code != 200:
            return None
        
        html = res.text
        
        # 本紙のフルコンテンツHTMLリンクを探索
        pattern = re.compile(rf'href="([^"]*?{date_compact}h\d+[^"]*?full[^"]*?\.html)"')
        matches = pattern.findall(html)
        print(f"見つまったフルコンテンツHTML候補数: {len(matches)}")
        
        full_html_url = None
        for path in matches:
            if path.startswith("http"):
                full_html_url = path
            elif path.startswith("/"):
                full_html_url = "https://www.kanpo.go.jp" + path
            else:
                full_html_url = KANPO_BASE_URL + path.replace("./", "")
            break
            
        if not full_html_url:
            print("⚠️ フルコンテンツHTMLリンクが見つかりませんでした。")
            return None
            
        print(f"🔍 ターゲットHTML: {full_html_url}")
        
        v_res = requests.get(full_html_url, headers=headers, timeout=15)
        print(f"ターゲットHTML取得 HTTPステータス: {v_res.status_code}")
        if v_res.status_code != 200:
            return None
            
        # HTML内からPDFリンクを抽出
        pdf_match = re.search(r'["\']([^"\']+\.pdf)["\']', v_res.text, re.IGNORECASE)
        if pdf_match:
            raw_pdf = pdf_match.group(1)
            print(f"📄 抽出されたPDFパス: {raw_pdf}")
            if raw_pdf.startswith("http"):
                return raw_pdf
            elif raw_pdf.startswith("/"):
                return "https://www.kanpo.go.jp" + raw_pdf
            else:
                base_dir = full_html_url[:full_html_url.rfind("/") + 1]
                return base_dir + raw_pdf.replace("./", "")
        else:
            fallback_url = full_html_url.replace(".html", ".pdf")
            print(f"⚠️ PDF直接リンクが見つからないためフォールバック: {fallback_url}")
            return fallback_url

    except Exception as e:
        print(f"❌ 予期せぬエラーが発生しました:")
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

    sa_key_json = os.environ.get("GCP_SA_KEY")
    if not sa_key_json:
        raise ValueError("❌ 環境変数 GCP_SA_KEY が設定されていません。")
    
    sa_info = json.loads(sa_key_json)
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=['https://www.googleapis.com/auth/drive']
    )
    drive_service = build('drive', 'v3', credentials=credentials)

    pdf_url = fetch_kanpo_pdf_url(date_compact)
    if not pdf_url:
        print("PDF URLの取得に失敗したため終了します。")
        return

    print(f"📥 官報PDFを取得中: {pdf_url}")
    headers = {"User-Agent": USER_AGENT}
    pdf_res = requests.get(pdf_url, headers=headers, timeout=30)
    print(f"PDFダウンロード HTTPステータス: {pdf_res.status_code}")
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
