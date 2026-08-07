from flask import Flask, request, abort

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import *

#======python的函數庫==========
import tempfile, os
import datetime
import openai
import time
import traceback
import base64
import requests
import json
#======python的函數庫==========

# === 新增 Firebase 套件 ===
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
#==========================

app = Flask(__name__)

# firebase 資料庫
firebase_key_path = os.getenv('FIREBASE_KEY_PATH')
cred = credentials.Certificate(firebase_key_path)
firebase_admin.initialize_app(cred)
db = firestore.client()

static_tmp_path = os.path.join(os.path.dirname(__file__), 'static', 'tmp')
# Channel Access Token
line_bot_api = LineBotApi(os.getenv('CHANNEL_ACCESS_TOKEN'))
# Channel Secret
handler = WebhookHandler(os.getenv('CHANNEL_SECRET'))
# OPENAI API Key初始化設定
openai.api_key = os.getenv('OPENAI_API_KEY')



# def save_user_meal(user_id, recipe_result):
#     """將這次生成的食譜寫入資料庫"""
#     try:
#         # 路徑：users(集合) -> user_id(文件) -> meals(集合) -> 自動產生的ID(文件)
#         doc_ref = db.collection('users').document(user_id).collection('meals').document()
#         doc_ref.set({
#             'recipe': recipe_result,
#             'timestamp': firestore.SERVER_TIMESTAMP # 讓資料庫自動押上時間
#         })
#     except Exception as e:
#         print(f"寫入資料庫失敗: {e}")

# def get_recent_meals(user_id):
#     """撈取該使用者最近 3 次的飲食紀錄"""
#     try:
#         meals_ref = db.collection('users').document(user_id).collection('meals')
#         # 依照時間排序（新到舊），只取前 3 筆以節省 Token
#         query = meals_ref.order_by('timestamp', direction=firestore.Query.DESCENDING).limit(3)
#         results = query.stream()
        
#         past_meals = []
#         for doc in results:
#             data = doc.to_dict()
#             past_meals.append(data.get('recipe', ''))
            
#         if not past_meals:
#             return "目前沒有近期飲食紀錄。"
#         return "\n".join(past_meals)
#     except Exception as e:
#         print(f"讀取資料庫失敗: {e}")
#         return "無法讀取歷史紀錄。"

def save_pending_meal(user_id, recipe_result):
    # """先將食譜存入，狀態設為 pending，並回傳這筆資料的 ID"""
    try:
        doc_ref = db.collection('users').document(user_id).collection('meals').document()
        doc_ref.set({
            'recipe': recipe_result,
            'status': 'pending', # 新增狀態欄位
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        return doc_ref.id # 回傳 ID 給按鈕使用
    except Exception as e:
        print(f"寫入資料庫失敗: {e}")
        return None

def update_meal_status(user_id, doc_id, is_satisfied):
    # """根據按鈕點擊結果，更新或刪除該筆紀錄"""
    try:
        doc_ref = db.collection('users').document(user_id).collection('meals').document(doc_id)
        if is_satisfied:
            doc_ref.update({'status': 'confirmed'})
        else:
            doc_ref.delete()
    except Exception as e:
        print(f"更新資料庫狀態失敗: {e}")

def search_cookpad_recipes(ingredients_str):
    """利用 Google Custom Search API 搜尋 Cookpad 網站的真實食譜"""
    api_key = os.getenv('GOOGLE_SEARCH_API_KEY')
    cx = os.getenv('GOOGLE_SEARCH_CX')
    
    if not api_key or not cx:
        print("尚未設定 Google Search 金鑰")
        return "無網路搜尋結果"

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': api_key,
        'cx': cx,
        'q': ingredients_str, # 搜尋關鍵字 (例如: 番茄 雞蛋)
        'num': 3 # 抓取最相關的前 3 筆即可
    }
    
    try:
        response = requests.get(url, params=params)
        data = response.json()
        items = data.get('items', [])
        
        if not items:
            return "查無相關食譜"
            
        search_results = ""
        for i, item in enumerate(items):
            search_results += f"【參考食譜 {i+1}】\n菜名：{item.get('title')}\n網址：{item.get('link')}\n\n"
        return search_results
    except Exception as e:
        print(f"Google 搜尋發生錯誤: {e}")
        return "搜尋功能異常"

def get_recent_meals(user_id):
    try:
        meals_ref = db.collection('users').document(user_id).collection('meals')
        query = meals_ref.where('status', '==', 'confirmed').order_by('timestamp', direction=firestore.Query.DESCENDING).limit(3)
        results = query.stream()
        
        past_meals = []
        for doc in results:
            data = doc.to_dict()
            past_meals.append(data.get('recipe', ''))
            
        if not past_meals:
            return "目前沒有近期飲食紀錄。"
        return "\n".join(past_meals)
        
    except Exception as e:
        # 絕對不要只寫 pass，一定要把錯誤印出來看！
        print(f"資料庫查詢發生錯誤: {e}") 
        return None

def GPT_response(text):
    # 接收回應 (改用 ChatCompletion 寫法)
    response = openai.ChatCompletion.create(
        model="gpt-5-nano", # 填入你想要使用的模型名稱
        messages=[
            {"role": "system", "content":"你是一個營養師，你的任務是用有限的食材讓使用者吃到美味健康的一餐"},
            {"role": "user", "content": text}
        ],
        # temperature=0.5, 
        # max_completion_tokens=500
    )
    print(response)
    
    # 重組回應 (ChatCompletion 的回傳 JSON 結構與以往不同，需改為 ['message']['content'])
    answer = response['choices'][0]['message']['content']
    return answer

# 監聽圖片訊息
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
        user_id = event.source.user_id # 取得使用者 ID
        loading_url = "https://api.line.me/v2/bot/chat/loading/start"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('CHANNEL_ACCESS_TOKEN')}"
        }
        data = {
            "chatId": user_id,
            "loadingSeconds": 20 # 動畫顯示秒數 (最高 20)
        }
        # 送出請求，使用者的 LINE 畫面會立刻出現「...」動畫
        requests.post(loading_url, headers=headers, json=data)

        # 1. 向 LINE 伺服器請求下載使用者傳送的圖片
        message_content = line_bot_api.get_message_content(event.message.id)
        image_data = b""
        for chunk in message_content.iter_content():
            image_data += chunk
            
        # 2. 將二進位圖片資料轉換為 Base64 字串 (OpenAI 接收的格式)
        base64_image = base64.b64encode(image_data).decode('utf-8')

        # 階段一：請 AI「只」辨識食材
        # ==========================================
        res_ingredients = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "請辨識這張圖片中的主要食材，並「只」回傳用空格分隔的食材名稱（例如：番茄 雞蛋 蔥）。不要輸出任何其他說明文字。"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "low"}}
                    ]
                }
            ],
            temperature=0.2 # 降低隨機性，讓輸出更穩定
        )
        ingredients_str = res_ingredients['choices'][0]['message']['content'].strip()
        print(f"辨識出食材：{ingredients_str}")

        cookpad_results = search_cookpad_recipes(ingredients_str)
        past_meals_str = get_recent_meals(user_id)

        # 3. 將圖片與 Prompt 結合，送給 OpenAI (使用支援視覺的 gpt-4o-mini)
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"""你是一位專業的營養師與主廚。
                    【使用者近期飲食紀錄】：\n{past_meals_str}
                    【真實 Cookpad 搜尋結果】：\n{cookpad_results}
                    
                    【任務】：
                    1. 優先參考「Cookpad 搜尋結果」來決定今天的食譜，並結合使用者的近期飲食給予合適的搭配。若無搜尋結果再自行發揮。
                    2. 根據使用者的近期飲食，判斷缺乏哪些營養素並在這次食譜中優先補足。
                    3. 請你「絕對只能」輸出 JSON 格式，不要包含 Markdown 標記，格式如下：
                    {{
                      "recipe_name": "菜名",
                      "style": "日式 / 中式 / 西式",
                      "category": "肉類料理 / 蔬菜料理 / 海鮮料理",
                      "ingredients": ["食材A", "食材B"],
                      "steps": ["1. 步驟一", "2. 步驟二"],
                      "source_url": "參考食譜的網址(若無請填無)"
                    }}"""
                }
            ],
            max_tokens=800,
            temperature=0.5
        )
        
        ai_raw_text = response['choices'][0]['message']['content']
        
        # 4. JSON 解析與排版
        try:
            clean_json_str = ai_raw_text.replace("```json", "").replace("```", "").strip()
            recipe_data = json.loads(clean_json_str)
            
            # 排版字串 (加入了來源網址)
            reply_text = f"為您推薦：【{recipe_data['recipe_name']}】\n"
            reply_text += f"風格：{recipe_data['style']} | {recipe_data['category']}\n\n"
            reply_text += "🥗 食材：\n" + "、".join(recipe_data['ingredients']) + "\n\n"
            reply_text += "🍳 步驟：\n" + "\n".join(recipe_data['steps']) + "\n\n"
            reply_text += f"🔗 參考來源：\n{recipe_data.get('source_url', '無')}"
            
            doc_id = save_pending_meal(user_id, recipe_data)
            
            text_message = TextSendMessage(
                text=reply_text + "\n\n您對這次的食譜滿意嗎？滿意才會存入喔！",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=PostbackAction(label="滿意", data=f"satisfy&{doc_id}")),
                    QuickReplyButton(action=PostbackAction(label="不滿意", data=f"unsatisfy&{doc_id}"))
                ])
            )
            
            line_bot_api.reply_message(event.reply_token, text_message)
            
        except json.JSONDecodeError as e:
            print(f"JSON 解析失敗: {e}\nAI 原始回應: {ai_raw_text}")
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage('抱歉，小助手剛剛整理食譜格式時出錯了，請再試一次！')
            )


# 監聽所有來自 /callback 的 Post Request
@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']
    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


# 處理訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text
    user_id = event.source.user_id # 取得使用者 ID
    try:
        if msg == "查看歷史菜譜":
            history = get_recent_meals(user_id)
            print(f"您的近期紀錄如下：\n{history}")
            if history is None:
                line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(f"您還未有已儲存菜譜哦")
            )
            else:
                line_bot_api.reply_message(
                    event.reply_token, 
                    TextSendMessage(f"您的近期紀錄如下：\n{history}")
                )
            
        elif msg == "輸入我吃過的東西":
            print("請直接打字告訴我您今天吃了什麼，或是傳食物照片給我喔！")
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage("請直接打字告訴我您今天吃了什麼，或是傳食物照片給我喔！")
            )
            
        elif msg == "營養分析表":
            print("營養分析圖表功能正在努力開發中，敬請期待！")
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage("營養分析圖表功能正在努力開發中，敬請期待！")
            )
        
        # 非選單指令交由 model 處理 
        else:
            loading_url = "https://api.line.me/v2/bot/chat/loading/start"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.getenv('CHANNEL_ACCESS_TOKEN')}"
            }
            data = {
                "chatId": user_id,
                "loadingSeconds": 20 # 動畫顯示秒數 (最高 20)
            }
            # 送出請求，使用者的 LINE 畫面會立刻出現「...」動畫
            requests.post(loading_url, headers=headers, json=data)

            GPT_answer = GPT_response(msg)
            print(f'AI 回應:{GPT_answer}')

            if not GPT_answer or GPT_answer.strip() == "":
                GPT_answer = "抱歉，我剛剛腦筋一片空白，可以請你再輸入一次嗎？"

            line_bot_api.reply_message(event.reply_token, TextSendMessage(GPT_answer))
    except:
        print(traceback.format_exc())
        line_bot_api.reply_message(event.reply_token, TextSendMessage('此服務目前不通...請稍後再試!'))
        

@handler.add(PostbackEvent)
def handle_postback(event):
    user_id = event.source.user_id
    # 切割傳過來的 data，例如 "satisfy&123456" 會變成 action="satisfy", doc_id="123456"
    action, doc_id = event.postback.data.split('&')

    if action == 'satisfy':
        update_meal_status(user_id, doc_id, True)
        line_bot_api.reply_message(event.reply_token, TextSendMessage('太棒了！已經幫您把這道菜存入歷史紀錄。'))
    elif action == 'unsatisfy':
        update_meal_status(user_id, doc_id, False)
        line_bot_api.reply_message(event.reply_token, TextSendMessage('好的，這次的紀錄已取消，期待下次能給您更棒的建議！'))


@handler.add(MemberJoinedEvent)
def welcome(event):
    uid = event.joined.members[0].user_id
    gid = event.source.group_id
    profile = line_bot_api.get_group_member_profile(gid, uid)
    name = profile.display_name
    message = TextSendMessage(text=f'{name}歡迎加入')
    line_bot_api.reply_message(event.reply_token, message)
        
        
import os
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
