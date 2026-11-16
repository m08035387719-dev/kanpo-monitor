import os
import json
import tempfile
import datetime
import requests
import re
import fitz  # PyMuPDF
from google.oauth2 import service_account
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
    """本日の官報PDFのURLを直接安全に取得する"""
    headers = {"User-Agent": USER_AGENT}
    try:
        res = requests.get(KANPO_TOP_URL, headers=headers, timeout=15)
        if res.status_code != 200:
            print(f"❌ 官報トップページの取得に失敗しました: HTTP {res.status_code}")
            return None
        
        html = res.text
        # 本日の日付が含まれるPDFファイルへのリンクを直接探す
        pdf_matches = re.findall(rf'href="([^"]*?{date_compact}[^"]*?\.pdf)"', html)
        if pdf_matches:
            path = pdf_matches[0]
            if path.startswith("http"):
                return path
            elif path.startswith("/"):
                return "https://www.kanpo.go.jp" + path
            else:
                return "https://www.kanpo.go.jp/" + path.replace("./", "")

        # 見つからない場合はビューアページ（.html）経由で探す
        html_matches = re.findall(rf'href="([^"]*?{date_compact}[^"]*?\.html)"', html)
        if html_matches:
            viewer_path = html_matches[0]
            if viewer_path.startswith("http"):
                viewer_url = viewer_path
            elif viewer_path.startswith("/"):
                viewer_url = "https://www.kanpo.go.jp" + viewer_path
            else:
                viewer_url = "https://www.kanpo.go.jp/" + viewer_path.replace("./", "")
            
            print(f"🔍 ビューアURLを確認中: {viewer_url}")
            v_res = requests.get(viewer_url, headers=headers, timeout=15)
            pdf_match = re.search(r'["\']([^"\']+\.pdf)["\']', v_res.text)
            if pdf_match:
                raw_pdf = pdf_match.group(1)
                if raw_pdf.startswith("http"):
                    return raw_pdf
                elif raw_pdf.startswith("/"):
                    return "https://www.kanpo.go.jp" + raw_pdf
                else:
                    base_dir = viewer_url[:viewer_url.rfind("/") + 1]
                    return base_dir + raw_pdf.replace("./", "")
                    
        print(f"⚠️ 本日 ({date_compact}) のPDFリンクが見つかりませんでした。")
        return None
    except Exception as e:
        print(f"❌ PDF URLの解決中にエラーが発生しました: {e}")
        return None

def upload_to_drive(service, file_path, file_name, folder_id):
    """Googleドライブの指定フォルダにファイルをアップロードする"""
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

    # 1. Google Drive API の認証設定
    sa_key_json = os.environ.get("GCP_SA_KEY")
    if not sa_key_json:
        raise ValueError("❌ 環境変数 GCP_SA_KEY が設定されていません。")
    
    sa_info = json.loads(sa_key_json)
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=['https://www.googleapis.com/auth/drive']
    )
    drive_service = build('drive', 'v3', credentials=credentials)

    # 2. 本日の官報PDF URLの取得
    pdf_url = fetch_kanpo_pdf_url(date_compact)
    if not pdf_url:
        print("本日の官報は発行されていないか、取得できませんでした。処理を正常終了します。")
        return

    print(f"📥 官報PDFを取得中: {pdf_url}")
    headers = {"User-Agent": USER_AGENT}
    pdf_res = requests.get(pdf_url, headers=headers, timeout=30)
    if pdf_res.status_code != 200:
        print(f"❌ PDFのダウンロードに失敗しました: HTTP {pdf_res.status_code}")
        return

    # 3. 一時ディレクトリでPDFを読み込み、全ページをJPEG化してドライブへアップロード
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
