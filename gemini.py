######## -*- coding: utf-8 -*- ###################################################
## Gemini API access for SysML v2 Glossary in Japanese
## Copyright (c) 2026 Mgnite Inc.
## Contributors:
##  Hisashi Miyashita, Mgnite Inc.
##################################################################################

from google import genai
import time
import os
import csv

def load_instructions(file_path="instructions.md"):
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        print(f"警告: {file_path} が見つかりません。デフォルト設定を使用します。")
        return "あなたはSysML v2の専門家です。"

def save_as_csv(raw_text, section_no, output_dir="output"):
    """
    , や 改行 が含まれるフィールドのみ引用符で囲む (QUOTE_MINIMAL)
    Excelでの文字化けを防ぎつつ、Git Diff を最もクリーンに保つ設定。
    """
    if not raw_text:
        return None

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 1. クリーニング（Markdown装飾の除去）
    clean_text = raw_text.replace("```tsv", "").replace("```", "").strip()
    
    # 2. |~| 区切りのテキストを行ごとのリストに変換
    # AIの出力に合わせ、確実に分割するために split を使用します
    lines = clean_text.splitlines()
    data_rows = []
    for line in lines:
        if "|~|" in line:
            # |~| で分割し、各セルの前後の空白を削除
            row = [cell.strip() for cell in line.split("|~|")]
            data_rows.append(row)

    file_path = os.path.join(output_dir, f"SysML2-Section-{section_no}.csv")

    # 3. 保存 (BOM付きUTF-8)
    with open(file_path, "w", encoding="utf-8-sig", newline="") as f_output:
        # quoting=csv.QUOTE_MINIMAL : 必要な時だけ引用符を付ける
        writer = csv.writer(f_output, 
                            quoting=csv.QUOTE_MINIMAL, 
                            lineterminator='\n')
        
        writer.writerows(data_rows)
    
    print(f"--- [Minimal Quoting CSV] {file_path} ---")
    return file_path

class GAClient:
    def __init__(self, apikey=None):
        if apikey is None:
            from mgpy.mg import iMg
            cfg = iMg.config()
            gConfig = cfg.get('gemini')
            if gConfig:
                apikey = gConfig['apikey']
            if apikey is None:
                raise Exception(f"API Key must be specified")
        self.client = genai.Client(api_key=apikey)
        self.instructions = load_instructions()
        self.set_model("gemini-2.5-flash")

    def set_model(self, name):
        self.model_name = name

    def set_light_model(self):
        self.set_model('gemini-2.0-flash')

    def list_models(self):
        models = self.client.models.list()
        for m in models:
            print(f"Model Name: {m.name}")

    def list_files(self):
        models = self.client.models.list()
        files = self.client.files.list()
        for f in files:
            print(f"Uploaded file: {f.display_name} - {f.state.name}")

    def cleanup_files(self):
        """古いファイルを一掃する"""
        files = self.client.files.list()
        for f in files:
            print(f"清理中: {f.display_name}")
            self.client.files.delete(name=f.name)

    def upload_sysml_spec(self, file_path, display_name=None):
        """
        指定されたパスのPDFをアップロードし、ACTIVE状態になるまで待機して File オブジェクトを返す
        """
        if not os.path.exists(file_path):
            print(f"--- [エラー] ファイルが見つかりません: {file_path} ---")
            return None

        print(f"--- [開始] アップロード: {file_path} ---")

        # 1. ファイルのアップロード
        with open(file_path, "rb") as f:
            uploaded_file = self.client.files.upload(
                file=f,
                config={
                    'mime_type': 'application/pdf',
                    'display_name': display_name or os.path.basename(file_path)
                }
            )

        # 2. ACTIVE 状態になるまでポーリング（待機）
        # client.files.get() を使って最新の状態を確認し続ける
        file_id = uploaded_file.name
        while True:
            current_file = self.client.files.get(name=file_id)
            if current_file.state.name == "ACTIVE":
                print(f"\n--- [完了] {current_file.display_name} が利用可能になりました (ID: {file_id}) ---")
                break
            elif current_file.state.name == "FAILED":
                print(f"\n--- [エラー] アップロードに失敗しました ---")
                return None
            else:
                print(".", end="", flush=True)
                time.sleep(2)

        return current_file

    # または、存在チェックをする
    def upload_if_not_exists(self, file_path, display_name):
        existing_files = self.client.files.list()
        for f in existing_files:
            if f.display_name == display_name:
                print(f"--- [発見] 既存のファイルを使用します: {display_name} ---")
                return f
        # なければアップロード
        return self.upload_sysml_spec(file_path, display_name)

    def upload_standards(self):
        print("SysML/KerML標準書をアップロード中...")
        self.sysml_spec = self.upload_if_not_exists("doc/STD/SysML2-formal-25-09-03.pdf", "SysML_v2_Formal")
        self.kerml_spec = self.upload_if_not_exists("doc/STD/KerML1-formal-25-09-01.pdf", "KerML_v1_Formal")


    def extract_sysml_section(self, file_objs, section_no, system_instruction):
        """
        section_no にリストを渡せるように拡張し、一括抽出を可能にする
        """
        if not isinstance(file_objs, list):
            file_objs = [file_objs]

        # section_no がリストの場合はカンマ区切りの文字列にする
        if isinstance(section_no, list):
            section_label = ", ".join(section_no)
            target_description = f"以下のセクションすべて: {section_label}"
        else:
            section_label = section_no
            target_description = f"Section {section_no}"

        print(f"--- [開始] {target_description} ---")

        # セクションごとの個別指示（一括抽出に対応）
        user_prompt = f"""
        添付のPDFから、SysML v2 Specにおける {target_description} の内容を抽出・翻訳してください。

        【重要指示】
        - 指定されたすべてのセクションから用語を抽出してください。
        - 出力はセクションごとに分けず、すべて一つのTSV形式（|~|区切り）にまとめて出力してください。
        - ヘッダー行は一度だけ出力してください。
        """

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=file_objs + [user_prompt],
                config={
                    "system_instruction": system_instruction,
                    "temperature": 0.0,
                }
            )

            result_text = response.text.strip()

            # 以前の指摘通り、エラーを外に投げるためにチェックを入れる
            if not result_text:
                raise ValueError(f"{section_label} の抽出結果が空です。")

            return result_text

        except Exception as e:
            # ここで握りつぶさず raise することで、retry 関数が正しく動くようになります
            print(f"--- [エラー発生] {section_label}: {e} ---")
            raise e
    

    def extract_and_save(self, section):
        # ここでエラーが起きると Exception が発生します
        raw_result = self.extract_sysml_section(
            [self.sysml_spec, self.kerml_spec], 
            section_no=section, 
            system_instruction=self.instructions
        )

        # もしAPIが空のレスポンスを返したり、何らかの理由でデータが取れなかった場合
        if not raw_result:
            # 明示的にエラーを投げることで、外側の retry ループを起動させます
            raise ValueError(f"Section {section} のデータ取得に失敗しました（空のレスポンス）。")

        # リスト対応のラベル作成
        file_label = "-".join(section) if isinstance(section, list) else section

        saved_path = save_as_csv(raw_result, file_label)
        return saved_path

    def extract_and_save_with_retry(self, section, max_retries=5):
        """
        指数関数的バックオフ (5分 -> 10分 -> 20分 -> 40分) を適用してリトライ
        """
        retries = 0
        # 初回の待機ベースを300秒（5分）に設定
        base_wait = 300 

        while retries < max_retries:
            try:
                # 実行前に念のため「ゾンビ・ファイル」を掃除するのもアリです
                # self.clean_up_files() 

                self.extract_and_save(section)
                print(f"✅ Section {section} の抽出・保存に成功しました。")
                return True 

            except Exception as e:
                error_msg = str(e).upper()
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                    retries += 1
                    # 💡 指数関数的バックオフ: 300 * (2 ^ (retries - 1))
                    # 1回目: 300s(5分), 2回目: 600s(10分), 3回目: 1200s(20分)...
                    wait_time = base_wait * (2 ** (retries - 1))
                    
                    print(f"⚠️ 制限(429)を検知。冷却期間として {wait_time // 60}分 待機します ({retries}/{max_retries})...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ リトライ不能なエラー: {e}")
                    return False 

        print(f"🛑 規定回数のリトライに失敗しました: Section {section}")
        return False

