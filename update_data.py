import os
import json
import traceback
from google import genai

def main():
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY が設定されていません。")
            return

        print("Gemini クライアントを初期化中 (AQキー対応)...")
        # client = genai.Client(api_key=api_key) の代わりに環境変数を直接読ませる形でも確実に初期化
        client = genai.Client(api_key=api_key)

        prompt = """
あなたは競馬のデータ分析AIです。
以下のJSON構造に完全に一致するデータのみを出力してください。
Markdownのコードブロック（```json ... ```）や、前後の挨拶・解説文は一切含めず、純粋なJSON文字列だけを出力してください。

【出力すべきJSON構造】
{
  "summaries": [
    {
      "race_id": "tokyo11",
      "updated_at": "開催当日 14:30更新",
      "post_count": 1250,
      "dark_horses": [
        {"num": 10, "comment": "追い切りの気配が抜群に良く、穴党からの支持が急増しています。", "heat": 4},
        {"num": 5, "comment": "前走の不利が大きかったため、今回は見直す声が多いです。", "heat": 3}
      ],
      "going": {
        "trend": "outside",
        "reports": [
          {"time": "15分前", "text": "直線の外めを伸びる馬が目立ち始めました。"},
          {"time": "40分前", "text": "内ラチ沿いもまだ耐えておりフラットな状態です。"}
        ]
      },
      "views": [
        {"tag": "展開", "text": "先行馬が競り合う形になり、差し馬に展開が向きそう。", "likes": 112},
        {"tag": "馬場", "text": "外差しが徐々に決まり出す馬場コンディション。", "likes": 84}
      ]
    }
  ]
}
"""

        print("Gemini APIにリクエストを送信中 (gemini-2.5-flash)...")
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        
        text = response.text.strip()
        
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
