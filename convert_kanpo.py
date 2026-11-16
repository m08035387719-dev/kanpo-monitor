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
# Googleドライブの共有先フォルダIDを直接指定
DRIVE_FOLDER_ID = "1Lle6cIKDuGGpABKMh9gy_iegRAlWX7pI"
KANPO_TOP_URL = "https://www.kanpo.go.jp/index.html"
KANPO_BASE_URL = "https://www.kanpo.go.jp/"
USER_AGENT = "KanpoImageBot/1.0 (credit monitoring)"

def get_today_jst_str():
    # 日本時間の今日の日付を yyyy-mm-dd と yyyymmdd で取得
    now_utc = datetime.datetime.utcnow()
    now_jst = now_utc + datetime.timedelta(hours=9)
    return now_jst.strftime("%Y%m%d"), now_jst.strftime("%Y-%m-%d")

def fetch_kanpo_pdf_url(date_compact):
    """本日の官報（本紙）のPDF URLを新サイトの構造に合わせて柔軟に探索して取得する"""
    headers = {"User-Agent": USER_AGENT}
    try:
        res = requests.get(KANPO_TOP_URL, headers=headers, timeout=15)
        if res.status_code != 200:
            print(f"❌ 官報トップページの取得に失敗しました: HTTP {res.status_code}")
            return None
        
        html = res.text
        # 本日の日付（例: 20261116）が含まれるすべてのリンクを抽出
        links = re.findall(rf'href="([^"]*?{date_compact}[^"]*?)"', html)
        
        target_url = None
        for link in links:
            # 相対パスや絶対パスを適切に結合
            if link.startswith("./"):
                full_url = KANPO_BASE_URL + link.replace("./", "")
            elif link.startswith("/"):
                full_url = "https://www.kanpo.go.jp" + link
            elif link.startswith("http"):
                full_url = link
            else:
                full_url = KANPO_BASE_URL + link
            
            # 本紙（'h' を含むか、または直接PDFのもの）を優先的にキャッチ
            if "h" in full_url or full_url.endswith(".pdf"):
                target_url = full_url
                break
        
        if not target_url:
            print(f"⚠️ 本日 ({date_compact}) の本紙リンクが見つかりませんでした。")
            return None
        
        print(f"🔍 候補URLを発見: {target_url}")
        
        # 取得したURLがHTML（ビューア等）の場合、その中から実際のPDFファイル名を探す
        if target_url.endswith(".html") or not target_url.endswith(".pdf"):
            viewer_res = requests.get(target_url, headers=headers, timeout=15)
            pdf_path_match = re.search(r'["\']([^"\']+\.pdf)["\']', viewer_res.text)
            if pdf_path_match:
                raw_path = pdf_path_match.group(1)
                if raw_path.startswith("http"):
                    return raw_path
                elif raw_path.startswith("/"):
                    return "https://www.kanpo.go.jp" + raw_path
                else:
                    base_dir = target_url[:target_url.rfind("/") + 1]
                    return base_dir + raw_path.replace("./", "")
            else:
                return target_url.replace(".html", ".pdf")
        
        return target_url
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

    # 1. Google Drive API の認証設定（GitHub Secretsからキーを復元）
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

            # Googleドライブへアップロード
            upload_to_drive(drive_service, img_path, img_name, DRIVE_FOLDER_ID)

    print("✅ すべてのページのJPEG化およびドライブ保存が完了しました。")

if __name__ == "__main__":
    main()
