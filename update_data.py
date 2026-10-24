import os
import json
import traceback
from google import genai
from pathlib import Path

def main():
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY が設定されていません。")
            return

        print("Gemini クライアントを初期化中...")
        client = genai.Client(api_key=api_key)

        # 新しいスキーマの raw_race_posts.json からデータを読み込む
        input_path = Path("raw_race_posts.json")
        if not input_path.exists():
            print("Error: raw_race_posts.json が見つかりません。")
            return

        with input_path.open(encoding="utf-8") as f:
            raw_data = json.load(f)

        race_info = raw_data.get("race", {})
        posts = raw_data.get("posts", [])
        race_id = raw_data.get("target_race_id", "unknown")

        # Geminiへ渡すプロンプトデータの構築
        prompt_data = {
            "race_id": race_id,
            "race": race_info,
            "posts": posts,
        }

        prompt = f"""
あなたは競馬のデータ分析AIです。
提供された以下の競馬データ（出走馬情報および掲示板投稿など）を分析し、
指定されたJSON構造に完全に一致するデータのみを出力してください。
Markdownのコードブロック（```json ... ```）や、前後の挨拶・解説文は一切含めず、純粋なJSON文字列だけを出力してください。

【制約事項】
- 投稿者の意見を確定した事実として扱わないこと
- 欠損値を勝手に推測で補完しないこと
- 推測と客観的データを明確に区別すること

【入力データ】
{json.dumps(prompt_data, ensure_ascii=False, indent=2)}

【出力すべきJSON構造】
{{
  "summaries": [
    {{
      "race_id": "{race_id}",
      "updated_at": "開催当日 最新更新",
      "post_count": {len(posts)},
      "dark_horses": [
        {{"num": 10, "comment": "追い切りの気配や投稿での評価に基づくコメント", "heat": 4}}
      ],
      "going": {{
        "trend": "outside",
        "reports": [
          {{"time": "直近", "text": "馬場や展開に関するレポート"}}
        ]
      }},
      "views": [
        {{"tag": "展開", "text": "ファンの見解や展開予想の要約", "likes": 100}}
      ]
    }}
  ]
}}
"""

        print(f"Gemini APIにリクエストを送信中 (race_id: {race_id})...")
        response = client.models.generate_content(
            model='gemini-3.8-flash',
            contents=prompt,
        )
        
        text = response.text.strip()
        
        # クリーニング保険
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        new_data = json.loads(text)
        
        with open("bulletin_summary.json", "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
            
        print("bulletin_summary.json の更新が完了しました！")

    except Exception as e:
        print("=== エラーが発生しました ===")
        traceback.print_exc()
        raise e

if __name__ == "__main__":
    main()
