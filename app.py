from flask import Flask, request, abort

from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import *

#======python 函數庫==========
import tempfile, os
import datetime
import openai
import time
import traceback
import base64
import requests
import json
#======python 函數庫==========

#======Firebase 套件======
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
#==========================

#========網頁套件=========
from flask import Flask, request, abort, render_template, jsonify
import re
from google.cloud.firestore_v1.base_query import FieldFilter
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

def process_nutrition_analysis(user_id, doc_id, meal_name, extra_desc=""):
    """負責將餐點名稱與補充說明送給 AI 分析，並存入資料庫"""
    try:
        desc_prompt = f"使用者補充說明：{extra_desc}" if extra_desc else "無補充說明"
        
        res_nutrition = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": f"""使用者原本的餐點辨識為【{meal_name}】。
                    【{desc_prompt}】請綜合原本的餐點與「補充說明」中的所有食物（務必包含飲料、額外配菜等），重新估算總營養價值，並「絕對只能」輸出 JSON 格式：
                    {{
                        "recipe_name": "(請根據補充說明，重新命名這餐的完整名稱，例如：紫米起司雞肉捲與麥芽牛奶)",
                        "style": "飲食紀錄",
                        "category": "使用者自行上傳",
                        "ingredients": ["(必須包含補充說明提到的所有食材與飲品)"],
                        "steps": ["1. 營養小評：請在此直接寫出你的評估內容，不要照抄括號提示", "2. 熱量說明：請簡述你預估的熱量依據"],
                        "calories": 預估總熱量數字(必須是整數，例如: 650),
                        "source_url": "無"
                    }}"""
                }
            ],
            temperature=0.3
        )
        
        nutri_raw = res_nutrition['choices'][0]['message']['content']
        clean_json_str = nutri_raw.replace("```json", "").replace("```", "").strip()
        nutri_data = json.loads(clean_json_str)
        
        # 取得 AI 重新命名的完整餐點名稱
        final_meal_name = nutri_data.get('recipe_name', meal_name)
        
        # 覆寫原本 pending 的紀錄，更新為 confirmed 狀態並存入 JSON
        db.collection('users').document(user_id).collection('meals').document(doc_id).set({
            'recipe': nutri_data,
            'status': 'confirmed',
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        
        # 將回傳文字的變數改為 final_meal_name
        return f"已幫您記錄這餐：【{final_meal_name}】\n\n營養師小點評：\n{nutri_data['steps'][0]}\n{nutri_data['steps'][1]}"
    except Exception as e:
        print(f"營養分析失敗：{e}")
        return "抱歉，分析營養時發生錯誤，請稍後再試。"

def update_user_preferences(user_id, preferences):
    """將使用者的飲食設定存入資料庫"""
    try:
        doc_ref = db.collection('users').document(user_id)
        # 使用 merge=True 避免覆寫掉原本使用者的其他結構
        doc_ref.set({'preferences': preferences}, merge=True)
        return True
    except Exception as e:
        print(f"更新設定失敗: {e}")
        return False

def get_user_preferences(user_id):
    """讀取使用者的飲食設定"""
    try:
        doc = doc_ref = db.collection('users').document(user_id).get()
        if doc.exists:
            return doc.to_dict().get('preferences', '無特殊飲食限制')
        return '無特殊飲食限制'
    except Exception as e:
        print(f"讀取設定失敗: {e}")
        return '無特殊飲食限制'

def create_recipe_flex_message(recipes):
    """將食譜陣列轉換為可左右滑動的 Flex Message 卡片"""
    bubbles = []
    
    for recipe in recipes:
        name = recipe.get('recipe_name', '美味料理')
        style = recipe.get('style', '家常菜')
        url = recipe.get('source_url', '')
        
        # 決定按鈕的動作 (有網址就開啟網頁，沒網址就傳送提示文字)
        action = {
            "type": "uri",
            "label": "查看食譜",
            "uri": url
        } if url and url != '無' else {
            "type": "message",
            "label": "無外部連結",
            "text": f"這道【{name}】是 AI 原創食譜，沒有外部連結喔！"
        }

        bubble = {
            "type": "bubble",
            "size": "micro",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": style, "size": "xs", "color": "#8c8c8c"},
                    {"type": "text", "text": name, "weight": "bold", "size": "md", "wrap": True}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#ff7a00",
                        "action": action
                    }
                ]
            }
        }
        bubbles.append(bubble)

    return FlexSendMessage(
        alt_text="您的歷史菜譜",
        contents={
            "type": "carousel",
            "contents": bubbles
        }
    )

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

def search_cookpad_recipes(ingredients_list):
    """利用 Google Custom Search API 搜尋 Cookpad 網站的真實食譜"""
    api_key = os.getenv('GOOGLE_SEARCH_API_KEY')
    cx = os.getenv('GOOGLE_SEARCH_CX')
    
    if not api_key or not cx:
        print("尚未設定 Google Search 金鑰")
        return "無網路搜尋結果"

    url = "https://www.googleapis.com/customsearch/v1"
    for ingredient in ingredients_list:
        params = {
            'key': api_key,
            'cx': cx,
            'q': ingredient, # 每次只搜一個食材
            'num': 3
        }
        try:
            response = requests.get(url, params=params)
            data = response.json()
            items = data.get('items', [])
            
            # 若找到結果，立刻組合字串並 return 結束迴圈
            if items:
                search_results = f"基於核心食材「{ingredient}」的搜尋結果：\n"
                for i, item in enumerate(items):
                    search_results += f"【參考食譜{i+1}】\n菜名：{item.get('title')}\n網址：{item.get('link')}\n\n"
                return search_results
                
        except Exception as e:
            print(f"Google 搜尋發生錯誤：{e}")
            
    return "查無相關食譜"
            

def get_recent_meals(user_id):
    try:
        meals_ref = db.collection('users').document(user_id).collection('meals')
        query = meals_ref.where(
                                filter=FieldFilter('status', '==', 'confirmed')).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(3)
        results = query.stream()
        
        past_meals = []
        for doc in results:
            data = doc.to_dict()
            recipe = data.get('recipe', {})
        
            if isinstance(recipe, dict):
                past_meals.append(recipe)
            
        if not past_meals:
            print("目前沒有近期飲食紀錄。")
            return None
        
        return past_meals
        
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

        res_vision = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": """請觀察這張圖片，判斷是「生鮮食材」還是「煮好的餐點成品」。
                            1.請「絕對只能」回傳 JSON 格式，不要包含 Markdown 標籤，同時只能輸出圖片中有的食材，絕對不應出現圖片中沒有的食材
                            2.絕對只能使用台灣人常用的詞彙(例如應該使用「鮭魚」、「花椰菜」! "不應該"「三文魚」、「西蘭花」等等)：
                            {
                            "image_type": "raw", // 生鮮食材填 raw，煮好的成品填 cooked
                            "items": ["番茄", "雞蛋"] // raw 填食材陣列，cooked 填菜名陣列
                            }"""},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "high"}}
                    ]
                }
            ],
            temperature=0.0
        )

        ai_raw_text = res_vision['choices'][0]['message']['content']
        clean_json_str = ai_raw_text.replace("```json", "").replace("```", "").strip()
        ai_result = json.loads(clean_json_str)
        print(f"辨識出：{ai_result}")

        if ai_result.get("image_type") == "raw":
            # 【生鮮食材流程】：走原本的 Cookpad 搜尋與食譜生成
            ingredients_list = ai_result.get("items", [])
            cookpad_results = search_cookpad_recipes(ingredients_list)
            ingredients_str = "、".join(ingredients_list)
            past_meals_str = get_recent_meals(user_id)
            user_prefs = get_user_preferences(user_id) # 讀取個人飲食設定

            print(f"參考：{cookpad_results},使用者之前吃過{past_meals_str}")
            response = openai.ChatCompletion.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": f"""你是一位專業的營養師與主廚，非常了解台灣人吃飯的口味。
                        【使用者專屬飲食設定】：\n{user_prefs}
                        【使用者近期飲食紀錄】：\n{past_meals_str}
                        【使用者現有的食材】：\n{ingredients_str}
                        【Cookpad 搜尋結果】：\n{cookpad_results}
                        
                        【任務】：
                        1. 核心鐵律：你「必須、絕對要」使用【使用者現有的食材】來作為這道菜的主要材料，但為了搭配合理性可以不用將全部食材用完！(此 case 為假設，不一定代表真實情況：食材有空心菜、雞蛋、蔥，應建議"炒空心菜" 或是 "蔥香烘蛋"擇一，絕對不要出現空心菜炒蛋這種奇怪的搭配)
                        2. 請嚴格遵守【使用者專屬飲食設定】(避開過敏原、符合其飲食法)在不違反條件下，優先參考【Cookpad 搜尋結果】中的菜名與作法來設計食譜，確保這是一道真實存在且能煮出來的料理，若有參考一定要提供來源。
                        3. 根據【使用者近期飲食紀錄】：給予合適的搭配與營養考量。
                        4. 請你「絕對只能」輸出 JSON 格式，不要包含 Markdown 標記，同時絕對只能使用台灣人常用的詞彙(例如應該使用「鮭魚」、「花椰菜」! "不應該"「三文魚」、「西蘭花」等等)，格式如下：
                        {{
                        "recipe_name": "菜名",
                        "style": "日式 / 中式 / 西式",
                        "category": "肉類料理 / 蔬菜料理 / 海鮮料理",
                        "ingredients": ["食材A", "食材B"],
                        "steps": ["1. 步驟一", "2. 步驟二"],
                        "calories": 預估熱量數字(必須是整數，例如: 600),
                        "source_url": "參考食譜的網址(真的沒有才填無)"
                        }}"""
                    }
                ],
                max_tokens=800,
                temperature=0.4
            )
            
            ai_raw_text = response['choices'][0]['message']['content']
            
            # 4. JSON 解析與排版
            try:
                clean_json_str = ai_raw_text.replace("```json", "").replace("```", "").strip()
                recipe_data = json.loads(clean_json_str)
                
                # 排版字串 (加入了來源網址)
                reply_text = f"為您推薦：【{recipe_data['recipe_name']}】\n"
                reply_text += f"風格：{recipe_data['style']} | {recipe_data['category']}\n\n"
                reply_text += "🥗 系統辨識出您有食材：\n" + "、".join(recipe_data['ingredients']) + "\n\n"
                reply_text += "🍳 步驟：\n" + "\n".join(recipe_data['steps']) + "\n\n"
                reply_text += f"🔗 參考來源：\n{recipe_data.get('source_url', '無')}"
                
                doc_id = save_pending_meal(user_id, recipe_data)
                
                text_message = TextSendMessage(
                    text=reply_text + "\n\n您對這次的食譜滿意嗎？滿意才會存入飲食記憶喔！",
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
        
        elif ai_result.get("image_type") == "cooked":
            meal_name = "、".join(ai_result.get("items", []))
            
            # 1. 建立一個暫存的餐點紀錄，用來記住這道菜的名字與 ID
            doc_ref = db.collection('users').document(user_id).collection('meals').document()
            doc_ref.set({
                'temp_meal_name': meal_name,
                'status': 'waiting_for_desc',
                'timestamp': firestore.SERVER_TIMESTAMP
            })
            doc_id = doc_ref.id
            
            # 2. 更新使用者的「對話狀態標籤」
            db.collection('users').document(user_id).set({
                'current_action': 'waiting_for_desc',
                'pending_meal_id': doc_id
            }, merge=True)
            
            # 3. 傳送詢問訊息與 Quick Reply
            text_message = TextSendMessage(
                text=f"看來您吃了【{meal_name}】！\n請問需要補充說明嗎？(例如：我只吃了半份、這是素食)\n\n如果有，請直接打字告訴我；如果不需要，請點擊下方按鈕。",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=PostbackAction(label="不需要補充", data=f"skip_desc&{doc_id}"))
                ])
            )
            line_bot_api.reply_message(event.reply_token, text_message)


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
        # === 新增：檢查使用者是否處於「等待補充說明」的狀態 ===
        user_doc = db.collection('users').document(user_id).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            if user_data.get('current_action') == 'waiting_for_desc':
                # 1. 讀取剛剛暫存的餐點名稱
                pending_doc_id = user_data.get('pending_meal_id')
                meal_doc = db.collection('users').document(user_id).collection('meals').document(pending_doc_id).get()
                
                if meal_doc.exists:
                    meal_name = meal_doc.to_dict().get('temp_meal_name')
                    
                    # 2. 清除使用者的等待狀態 (先清掉避免卡死)
                    db.collection('users').document(user_id).update({
                        'current_action': firestore.DELETE_FIELD,
                        'pending_meal_id': firestore.DELETE_FIELD
                    })
                    
                    # 3. 呼叫分析函式 (將 msg 當作補充說明傳入)
                    reply_text = process_nutrition_analysis(user_id, pending_doc_id, meal_name, extra_desc=msg)
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
                    return # 處理完畢提早結束，不往下跑選單邏輯

        if msg == "查看歷史菜譜":
            history = get_recent_meals(user_id)
            print(f"您的近期紀錄如下：\n{history}")
            
            if history is None:
                line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(f"您還未有已儲存菜譜哦")
            )
            else:
                # 呼叫剛剛寫好的 Flex Message 生成器
                flex_msg = create_recipe_flex_message(history)
                line_bot_api.reply_message(event.reply_token, flex_msg)
            
        elif msg == "個人飲食設定":
            reply = "請告訴我您的飲食禁忌或目標！\n(請以「SET:」開頭，例如「SET:不吃海鮮、正在減脂」)"
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(reply)
            )
        elif msg.startswith("SET:"):
            preferences = msg.replace("SET:", "").strip()
            update_user_preferences(user_id, preferences)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(f"好的，已為您記錄專屬設定：【{preferences}"))
            
        elif msg == "營養價值量表":
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
    elif action == 'skip_desc':
        # 讀取並分析
        meal_doc = db.collection('users').document(user_id).collection('meals').document(doc_id).get()
        if meal_doc.exists:
            meal_name = meal_doc.to_dict().get('temp_meal_name')
            
            # 清除等待狀態
            db.collection('users').document(user_id).update({
                'current_action': firestore.DELETE_FIELD,
                'pending_meal_id': firestore.DELETE_FIELD
            })
            
            # 呼叫分析函式 (不傳入 extra_desc)
            reply_text = process_nutrition_analysis(user_id, doc_id, meal_name, extra_desc="")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


@handler.add(MemberJoinedEvent)
def welcome(event):
    uid = event.joined.members[0].user_id
    gid = event.source.group_id
    profile = line_bot_api.get_group_member_profile(gid, uid)
    name = profile.display_name
    message = TextSendMessage(text=f'{name}歡迎加入')
    line_bot_api.reply_message(event.reply_token, message)


# 營養分析量表
@app.route('/')
def home():
    return "Bot is running!"
# 1. 負責顯示網頁的路由
@app.route('/dashboard')
def dashboard():
    # 當使用者點開網址，回傳一個 HTML 網頁，並把 user_id 傳進去
    return render_template('dashboard.html')

# 2. 負責提供圖表數據的 API
@app.route('/api/nutrition/<user_id>')
def get_nutrition_data(user_id):
    try:
        print(f"========== 開始抓取圖表資料 (User: {user_id}) ==========")
        meals_ref = db.collection('users').document(user_id).collection('meals')
        query = meals_ref.where(filter=FieldFilter('status', '==', 'confirmed')).order_by('timestamp', direction=firestore.Query.DESCENDING).limit(10)
        results = query.stream()
        
        labels = []
        calories = []
        doc_count = 0
        
        for doc in results:
            doc_count += 1
            data = doc.to_dict()
            print(f"\n--- [文件 {doc_count}] ID: {doc.id} ---")
            
            recipe = data.get('recipe', {})
            if not isinstance(recipe, dict):
                print("❌ 略過：不是字典")
                continue
                
            name = recipe.get('recipe_name', '未知餐點')
            
            # 優先從資料庫直接抓取 calories 整數欄位
            cal_val = recipe.get('calories')
            
            # 如果抓不到（舊版資料），才啟用相容模式去掃描文字
            if cal_val is None:
                cal_val = 0
                steps = recipe.get('steps', [])
                if isinstance(steps, list):
                    steps_text = " ".join(steps)
                    match = re.search(r'(\d+)\s*(?:大卡|卡|kcal)', steps_text, re.IGNORECASE)
                    if match:
                        cal_val = int(match.group(1))
                        print(f"✅ 成功從 steps 抓出熱量: {cal_val}")
                    else:
                        print("⚠️ 警告：無法從文字中找到熱量")
            else:
                print(f"✅ 成功從 calories 欄位讀取: {cal_val}")
            
            labels.append(name)
            calories.append(cal_val)
            
        print(f"\n========== 抓取結束 ==========")
        labels.reverse()
        calories.reverse()
        
        return jsonify({"labels": labels, "calories": calories})
    except Exception as e:
        print(f"❌ 取得圖表資料發生崩潰：{e}")
        return jsonify({"labels": [], "calories": []})

        
import os
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
